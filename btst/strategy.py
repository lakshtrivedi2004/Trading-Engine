"""BTST from a Telegram channel's signals (owner, 23 Oct 2026).

Day D, scan_start - scan_end (15:15 - 15:30 IST): every channel message posted
in the window is read once. A BTST signal (btst/signal.py) is entered at
market - the option it names, nearest expiry after today (btst/contracts.py),
`lots` lots, CARRYFORWARD - and a target LIMIT sell is placed at target_mult x
the signal's price (3 x on SENSEX, 2 x otherwise). One BTST a day.

Day D+1 (the next trading day), at decide_at:
    up, or down by avg_down_loss_pct (20%) or less: keep the target
    down by more: buy again for about the amount first invested (whole lots,
                  the count nearest that amount), and target the new average
                  price + avg_target_pct (10%)
The target is placed again each morning (the exchange cancels every order at
the close). At exit_at (15:14) whatever is still open is cancelled and sold at
market. Every step - the message, the signal, each order, the decision, the
close with its metrics - is written to MongoDB (services/mongo.py) under the
job's id. No stop-loss, by the owner's design.

Never more than one trade open; a job left half-entered by a crash is not
retried - it is marked for a human to look at.
"""
import json
import queue
from datetime import datetime, timedelta

from arb.instruments import IST, holidays, market_phase, now_ist
from btst import signal as S
from btst.contracts import find_option, tick_round, days_left


def _hm(s):
    p = [int(x) for x in str(s).split(":")]
    return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)


def _secs(t):
    return t.hour * 3600 + t.minute * 60 + t.second


class BtstStrategy:
    def __init__(self, cfg, store, broker, master_fn, llm, channel, log=print, now=now_ist,
                 notifier=None, cost_model=None):
        self.cfg, self.store, self.broker = cfg, store, broker
        self.master_fn, self.llm, self.channel = master_fn, llm, channel
        self.log, self.now, self.notifier = log, now, notifier
        self.cm = cost_model
        self.inbox = queue.Queue()
        self.last_ltp_at = 0.0
        self.status_note = "starting"

    # ── settings ────────────────────────────────────────────────────────────
    def c(self, k, default=None):
        return self.cfg.get(k, default)

    def target_mult(self, index):
        m = self.c("target_mult", {}) or {}
        return float(m.get(index, m.get("default", 2.0)))

    def _ts(self):
        return self.now().isoformat(timespec="seconds")

    def _say(self, text, job_id=None, notify=False):
        self.log(f"[btst] {text}")
        if job_id:
            try:
                self.store.add_event(job_id, self._ts(), text)
            except Exception as e:
                self.log(f"[btst] mongo event write failed: {e!r}")
        if notify and self.notifier:
            try:
                self.notifier.send(f"🌙 <b>BTST</b> {text}")
            except Exception:
                pass

    # ── messages (the Telegram reader's thread puts, step() takes) ─────────
    def on_message(self, msg):
        text = getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""
        self.inbox.put({"id": int(msg.id), "date": msg.date, "text": text})

    def _in_window(self, when):
        """A message posted today between scan_start and scan_end (IST)."""
        t = when.astimezone(IST) if when.tzinfo else when.replace(tzinfo=IST)
        now = self.now()
        return (t.date() == now.date()
                and _hm(self.c("scan_start", "15:15")) <= _secs(t) < _hm(self.c("scan_end", "15:30")))

    def _process_messages(self):
        while True:
            try:
                m = self.inbox.get_nowait()
            except queue.Empty:
                return
            if not self._in_window(m["date"]):
                continue                        # only the 15:15 - 15:30 messages of today
            if self.store.message_seen(self.channel, m["id"]):
                continue
            res = S.parse(m["text"], llm=self.llm, model=self.c("ai_model"),
                          markers=tuple(self.c("markers", ["BTST", "Disclaimer"])),
                          ai_fallback_regex=bool(self.c("ai_fallback_regex", True)),
                          thinking=self.c("ai_thinking", "low"))
            doc = {"channel": self.channel, "msg_id": m["id"],
                   "date": m["date"].astimezone(IST).isoformat(timespec="seconds"),
                   "text": m["text"], "is_signal": res["is_signal"], "reason": res["reason"],
                   "parsed_by": res["parsed_by"], "signal": res["signal"], "ai": res["ai"],
                   "read_at": self._ts()}
            self.store.save_message(doc)
            if not res["is_signal"]:
                self.log(f"[btst] message {m['id']}: not a signal - {res['reason']}")
                continue
            self._on_signal(res, m)

    def _on_signal(self, res, m):
        today = self.now().date().isoformat()
        sig = res["signal"]
        what = f"{sig['indices']} {sig['strike_price']:g} {sig['side']} @ {sig['price']:g}"
        done = self.store.job_for_trade_date(today)
        if done:
            self._say(f"signal {what} (message {m['id']}) ignored - one BTST a day, "
                      f"today's is {done['job_id']}")
            return
        if self.store.open_job():
            self._say(f"signal {what} ignored - a trade is still open")
            return
        if _secs(self.now()) >= _hm(self.c("scan_end", "15:30")):
            self._say(f"signal {what} ignored - the market has closed")
            return
        self._enter(sig, res, m)

    # ── entry ───────────────────────────────────────────────────────────────
    def _enter(self, sig, res, m):
        now = self.now()
        today = now.date()
        job = {"job_id": self.store.new_job_id(), "status": "entering", "mode": self.broker.mode,
               "trade_date": today.isoformat(), "created_at": now.isoformat(timespec="seconds"),
               "message": {"channel": self.channel, "msg_id": m["id"],
                           "date": m["date"].astimezone(IST).isoformat(timespec="seconds"), "text": m["text"]},
               "signal": dict(sig, parsed_by=res["parsed_by"]), "orders": [], "events": []}
        jid = self.store.insert_job(job)
        what = f"{sig['indices']} {sig['strike_price']:g} {sig['side']} @ {sig['price']:g}"
        c = find_option(self.master_fn(), sig["indices"], sig["strike_price"], sig["side"], today)
        if c is None:
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": "no such contract"})
            self._say(f"{what}: no {sig['indices']} {sig['strike_price']:g} {sig['side']} contract "
                      f"after today in the scrip master - not traded", jid, notify=True)
            return
        lots = max(1, int(self.c("lots", 1)))
        qty = lots * c["lot_size"]
        ltp = self.broker.ltp(c)
        self.store.update_job(jid, set_={"contract": c, "lots": lots, "ltp_at_signal": ltp,
                                         "days_to_expiry": days_left(c, today)})
        if ltp is None:
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": "no LTP"})
            self._say(f"{what}: no price for {c['symbol']} - not traded", jid, notify=True)
            return
        need = ltp * qty * 1.05
        funds = self.broker.funds()
        if funds is not None and funds < need:
            self.store.update_job(jid, set_={"status": "rejected",
                                             "reject_reason": f"funds Rs {funds:,.0f} < Rs {need:,.0f}"})
            self._say(f"{what}: {c['symbol']} needs about Rs {need:,.0f}, the account has "
                      f"Rs {funds:,.0f} - not traded", jid, notify=True)
            return
        buy = self.broker.buy_market(c, qty)
        buy.update(kind="buy", at=self._ts())
        self.store.add_order(jid, buy)
        if not buy.get("ok"):
            self.store.update_job(jid, set_={"status": "failed", "reject_reason": buy.get("error")})
            self._say(f"{what}: BUY {c['symbol']} x{qty} not filled - {buy.get('error')}", jid, notify=True)
            return
        q, px = int(buy["filled"]), float(buy["avg_price"])
        target = tick_round(sig["price"] * self.target_mult(sig["indices"]), c["tick"], up=True)
        pos = {"qty": q, "avg_price": round(px, 2), "invested": round(px * q, 2),
               "entry_price": round(px, 2), "entry_qty": q, "entry_at": self._ts()}
        self.store.update_job(jid, set_={"status": "open", "position": pos, "target_price": target,
                                         "target_mult": self.target_mult(sig["indices"]),
                                         "ltp_high": px, "ltp_low": px})
        self._say(f"BOUGHT {c['symbol']} x{q} @ {px:.2f} (Rs {px * q:,.0f}; signal {sig['price']:g}); "
                  f"target {target:.2f} = {self.target_mult(sig['indices']):g} x the signal's price", jid,
                  notify=True)
        self._place_target(self.store.get_job(jid), target, "target")

    def _place_target(self, job, price, kind):
        c, q = job["contract"], int(job["position"]["qty"])
        o = self.broker.place_limit_sell(c, q, price)
        o.update(kind=kind, at=self._ts(), price=price)
        self.store.add_order(job["job_id"], o)
        self.store.update_job(job["job_id"], set_={"target_price": price, "target_order": o,
                                                   "soft_target": not o.get("ok")})
        if o.get("ok"):
            self._say(f"target SELL {c['symbol']} x{q} @ {price:.2f} placed ({o.get('order_id')})", job["job_id"])
        else:
            self._say(f"target order refused ({o.get('error')}) - watching the price instead and selling "
                      f"at market at {price:.2f}", job["job_id"], notify=True)

    # ── the next day ────────────────────────────────────────────────────────
    def _manage(self, job):
        now = self.now()
        if market_phase(now, start="09:15", end="15:30") != "open":
            return
        c = job["contract"]
        jid = job["job_id"]
        ltp = self._watch(job)                  # first, so the day's high and low include this pass
        # A filled target closes the trade whenever it happens.
        to = job.get("target_order")
        if to and to.get("ok"):
            st = self.broker.order_state(to)
            if st and st["status"] == "complete":
                self._close(job, "target", st["avg"] or job["target_price"], None)
                return
        if job.get("soft_target") and ltp and ltp >= job["target_price"]:
            self._exit_market(job, "target (watched)")
            return
        if now.date().isoformat() <= job["trade_date"]:
            return                              # the entry day: nothing more until tomorrow
        if not job.get("decision") and _secs(now) >= _hm(self.c("decide_at", "09:15:30")):
            self._decide(job, ltp)
            return
        if job.get("decision") and _secs(now) >= _hm(self.c("exit_at", "15:14")):
            self._exit_market(job, "time_exit")

    def _watch(self, job):
        """The LTP every poll_sec, with the day's high and low kept for the metrics."""
        import time
        if time.time() - self.last_ltp_at < float(self.c("ltp_poll_sec", 10)):
            return job.get("last_ltp")
        self.last_ltp_at = time.time()
        ltp = self.broker.ltp(job["contract"])
        if ltp:
            upd = {"last_ltp": ltp, "last_ltp_at": self._ts(),
                   "ltp_high": max(float(job.get("ltp_high") or ltp), ltp),
                   "ltp_low": min(float(job.get("ltp_low") or ltp), ltp)}
            self.store.update_job(job["job_id"], set_=upd)
            job.update(upd)
        return ltp

    def _decide(self, job, ltp):
        jid, c, pos = job["job_id"], job["contract"], job["position"]
        if not ltp:
            ltp = self.broker.ltp(c)
        if not ltp:
            self.log("[btst] no opening price yet - deciding on the next pass")
            return
        # Yesterday's target order died at the close (unless it filled, handled above).
        pnl_pct = 100.0 * (ltp - pos["avg_price"]) / pos["avg_price"]
        limit = float(self.c("avg_down_loss_pct", 20.0))
        decision = {"at": self._ts(), "ltp": ltp, "pnl_pct": round(pnl_pct, 2), "avg_before": pos["avg_price"]}
        if pnl_pct >= -limit:
            decision["scenario"] = "hold" if pnl_pct >= 0 else "hold_small_loss"
            self.store.update_job(jid, set_={"decision": decision})
            self._say(f"opened {pnl_pct:+.1f}% ({ltp:.2f} vs {pos['avg_price']:.2f}) - "
                      f"{'up' if pnl_pct >= 0 else f'loss within {limit:g}%'}: target stays "
                      f"{job['target_price']:.2f}, out at {self.c('exit_at', '15:14')} if not hit", jid, notify=True)
            self._place_target(self.store.get_job(jid), job["target_price"], "target_day2")
            return
        # Down by more than the limit: about the same amount again, in whole lots.
        lot = int(c["lot_size"])
        per_lot = ltp * lot
        n = max(1, round(pos["invested"] / per_lot))
        add = n * lot
        buy = self.broker.buy_market(c, add)
        buy.update(kind="average_down", at=self._ts())
        self.store.add_order(jid, buy)
        if not buy.get("ok"):
            decision["scenario"] = "average_down_failed"
            self.store.update_job(jid, set_={"decision": decision})
            self._say(f"opened {pnl_pct:+.1f}%: average-down BUY x{add} not filled ({buy.get('error')}) - "
                      f"keeping the target {job['target_price']:.2f}", jid, notify=True)
            self._place_target(self.store.get_job(jid), job["target_price"], "target_day2")
            return
        q2, p2 = int(buy["filled"]), float(buy["avg_price"])
        q = pos["qty"] + q2
        avg = (pos["avg_price"] * pos["qty"] + p2 * q2) / q
        target = tick_round(avg * (1 + float(self.c("avg_target_pct", 10.0)) / 100.0), c["tick"], up=True)
        new_pos = dict(pos, qty=q, avg_price=round(avg, 2), invested=round(pos["invested"] + p2 * q2, 2),
                       added_qty=q2, added_price=round(p2, 2), added_amount=round(p2 * q2, 2))
        decision.update(scenario="average_down", added_qty=q2, added_price=p2, avg_after=round(avg, 2),
                        first_invested=pos["invested"], added_amount=round(p2 * q2, 2))
        self.store.update_job(jid, set_={"decision": decision, "position": new_pos})
        self._say(f"opened {pnl_pct:+.1f}% (over {limit:g}% down): bought {q2} more @ {p2:.2f} "
                  f"(Rs {p2 * q2:,.0f} vs Rs {pos['invested']:,.0f} first) - average {avg:.2f}, "
                  f"target {target:.2f} (+{self.c('avg_target_pct', 10.0):g}%)", jid, notify=True)
        self._place_target(self.store.get_job(jid), target, "target_after_average")

    def _exit_market(self, job, reason):
        jid, c = job["job_id"], job["contract"]
        q = int(job["position"]["qty"])
        to = job.get("target_order")
        if to and to.get("ok"):
            st = self.broker.cancel(to)
            self.store.add_order(jid, {"kind": "target_cancel", "at": self._ts(), "order_id": to.get("order_id"),
                                       "state": st})
            if st and st["status"] == "complete":
                self._close(job, "target", st["avg"] or job["target_price"], None)
                return
            if st and st.get("filled"):
                q -= int(st["filled"])          # part of the target filled before the cancel
        held = self.broker.net_qty(c)
        if held is not None and held < q:
            self._say(f"the account holds {held} of {c['symbol']}, the job {q} - selling {max(0, held)}",
                      jid, notify=True)
            q = max(0, held)
        if q <= 0:
            self._close(job, f"{reason} (nothing left at the broker)", job.get("last_ltp") or 0.0, None)
            return
        sell = self.broker.sell_market(c, q)
        sell.update(kind="exit", at=self._ts(), reason=reason)
        self.store.add_order(jid, sell)
        if not sell.get("ok"):
            self._say(f"EXIT SELL {c['symbol']} x{q} not filled ({sell.get('error')}) - retrying; "
                      f"check the Angel app", jid, notify=True)
            return
        self._close(job, reason, float(sell["avg_price"]), sell)

    # ── close and metrics ───────────────────────────────────────────────────
    def _cost(self, orders):
        if self.cm is None:
            return 0.0
        from arb.costs import leg_cost
        total = 0.0
        for o in orders:
            if o.get("filled") and o.get("avg_price"):
                total += leg_cost(float(o["avg_price"]), int(o["filled"]), o.get("side", "BUY"), "OPT", self.cm)
        return total

    def _close(self, job, reason, exit_px, sell):
        jid = job["job_id"]
        job = self.store.get_job(jid) or job
        pos, sig = job["position"], job["signal"]
        q, avg, inv = int(pos["qty"]), float(pos["avg_price"]), float(pos["invested"])
        orders = list(job.get("orders") or [])
        if reason == "target" and exit_px:
            orders.append({"side": "SELL", "filled": q, "avg_price": exit_px})
        gross = (float(exit_px) - avg) * q
        cost = self._cost(orders)
        net = gross - cost
        entered = datetime.fromisoformat(pos["entry_at"])
        now = self.now()
        hi, lo = float(job.get("ltp_high") or avg), float(job.get("ltp_low") or avg)
        if exit_px:                             # the exit traded there, whatever the polls saw
            hi, lo = max(hi, float(exit_px)), min(lo, float(exit_px))
        dec = job.get("decision") or {}
        metrics = {
            "closed_at": now.isoformat(timespec="seconds"), "exit_reason": reason,
            "exit_price": round(float(exit_px), 2), "qty": q, "avg_price": round(avg, 2),
            "invested": round(inv, 2), "gross_pnl": round(gross, 2), "charges": round(cost, 2),
            "net_pnl": round(net, 2), "pnl_pct": round(100.0 * net / inv, 2) if inv else 0.0,
            "gross_pct": round(100.0 * (float(exit_px) - avg) / avg, 2) if avg else 0.0,
            "target_price": job.get("target_price"), "target_hit": reason.startswith("target"),
            "signal_price": sig.get("price"), "entry_price": pos.get("entry_price"),
            "entry_slippage_pct": round(100.0 * (pos["entry_price"] - sig["price"]) / sig["price"], 2)
            if sig.get("price") else None,
            "scenario": dec.get("scenario", "closed_on_entry_day"),
            "open_pnl_pct_next_day": dec.get("pnl_pct"), "averaged": dec.get("scenario") == "average_down",
            "added_qty": pos.get("added_qty", 0), "added_price": pos.get("added_price"),
            "max_ltp": hi, "min_ltp": lo,
            "mfe_pct": round(100.0 * (hi - avg) / avg, 2) if avg else 0.0,
            "mae_pct": round(100.0 * (lo - avg) / avg, 2) if avg else 0.0,
            "held_hours": round((now - entered).total_seconds() / 3600.0, 2),
            "days_to_expiry_at_entry": job.get("days_to_expiry"),
            "mode": job.get("mode"),
        }
        self.store.update_job(jid, set_={"status": "closed", "result": metrics})
        self._say(f"CLOSED {job['contract']['symbol']} x{q} @ {exit_px:.2f} ({reason}) - net Rs {net:+,.0f} "
                  f"({metrics['pnl_pct']:+.1f}% of Rs {inv:,.0f}, charges Rs {cost:,.0f})", jid, notify=True)

    # ── the loop ────────────────────────────────────────────────────────────
    def resume(self):
        """On start: a job left 'entering' by a crash is not retried."""
        j = self.store.open_job()
        if j and j["status"] == "entering":
            self.store.update_job(j["job_id"], set_={"status": "needs_review",
                                                     "reject_reason": "the service stopped during entry"})
            self._say(f"job {j['job_id']} was being entered when the service stopped - not retried; "
                      f"check the Angel app for {j.get('contract', {}).get('symbol', 'the contract')}",
                      j["job_id"], notify=True)
        elif j:
            self._say(f"resuming {j['job_id']} ({j['contract']['symbol']} x{j['position']['qty']})")

    def _record_quiet_day(self):
        """After the scan window of a trading day with no job at all: no signal,
        no trade - saved, so the page lists every trading day."""
        now = self.now()
        today = now.date().isoformat()
        if now.weekday() >= 5 or today in holidays() or _secs(now) < _hm(self.c("scan_end", "15:30")):
            return
        if self.store.any_job_on(today) or self.store.day_recorded(today):
            return
        read = self.store.messages_on(today)
        looked = sum(1 for m in read if "marker" not in str(m.get("reason", "")))
        self.store.save_day({"date": today, "status": "no_signal", "messages_read": len(read),
                             "looked_like_signals": looked, "at": self._ts()})
        self._say(f"{today}: no BTST signal between {self.c('scan_start', '15:15')} and "
                  f"{self.c('scan_end', '15:30')} ({len(read)} messages read) - no trade")

    def step(self):
        try:
            self._process_messages()
        except Exception as e:
            self.log(f"[btst] message handling error: {e!r}")
        try:
            self._record_quiet_day()
        except Exception as e:
            self.log(f"[btst] day record error: {e!r}")
        try:
            job = self.store.open_job()
            if job and job["status"] == "open":
                self._manage(job)
                self.status_note = f"managing {job['job_id']}"
            else:
                self.status_note = "waiting for a signal (15:15 - 15:30)"
        except Exception as e:
            self.log(f"[btst] manage error: {e!r}")

    def status(self):
        j = self.store.open_job()
        return {"now": self.now().isoformat(timespec="seconds"), "mode": self.broker.mode,
                "channel": self.channel, "note": self.status_note,
                "window": f"{self.c('scan_start', '15:15')}-{self.c('scan_end', '15:30')}",
                "open_job": j, "jobs": self.store.recent_jobs(20),
                "messages": self.store.recent_messages(20)}
