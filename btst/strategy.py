"""BTST from a Telegram channel's signals (owner, 23 Oct 2026).

Day D, scan_start - scan_end (15:15 - 16:00 IST): every channel message posted
in the window is read once. Orders can be placed until market_close (15:45 -
the market's hours changed in 2026; the channel posted every October signal at
15:33 - 15:39); a signal read after it is saved with its reading, not traded. A BTST signal (btst/signal.py) is entered at
market - the option it names, nearest expiry after today (btst/contracts.py),
the most lots costing at most trade_amount + amount_tolerance_pct (Rs 15,000
+ 5%, fixed - no compounding; none if one lot costs more), CARRYFORWARD - and a target LIMIT sell is placed at target_mult x
the signal's price (3 x on SENSEX, 2 x otherwise). One BTST a day; no signal,
no trade (the day is still recorded, btst_days).

Day D+1 (the next trading day), tracked live from the open (owner, 26 Oct
2026), against the first buy price; at most 100% of the first amount added:
    down less than 25%: the target stays
    first down 25%: add 50%; target cut 25%, then 1 point lower per point
        (trail_step_points) below that buy, never under the new average + 10%
    then down 50%: add the other 50%; target = the new average + 10%
    already down 50% when first seen (a gap down): add 100%; target =
        the new average + 10%
GIFT Nifty is read at the entry day's close and at gift_check_at (01:00) and
reported - information only (btst/gift.py).
Every target change (placed, AMO, trailed, replaced) is kept on the job
(target_history), and the trailing state (trail) with each step.
The exchange cancels every order at the close. Live, the target goes back in
at amo_at (17:00) on day D as an AMO for the next open, so an opening spike
fills it; it stays through the decision (averaging down cancels it first -
never two sells at once), and an AMO the exchange rejects at the open is
replaced by a normal target. Paper places the target again at the open. At
exit_at (15:14) whatever is still open is cancelled and sold at market. Every step - the message, the signal, each order, the decision, the
close with its metrics - is written to MongoDB (services/mongo.py) under the
job's id, and logged ("LEVEL area | text", btst/logbook.py) with the reason
for every trade not taken. No stop-loss, by the owner's design.

Never more than one trade open; a job left half-entered by a crash is not
retried - it is marked for a human to look at.
"""
import math
import queue
import threading
import time
import traceback
from datetime import datetime, time as dtime, timedelta

from arb.instruments import IST, holidays, market_phase, now_ist
from btst import chart as CH
from btst import gift as G
from btst import recorder as REC
from btst import signal as S
from btst.contracts import days_left, find_option, tick_round
from btst.logbook import fmt


# What the page shows of the rules - with the values the code falls back to.
RULE_DEFAULTS = {"exit_at": "15:14", "topup_first_loss_pct": 25.0, "topup_first_pct": 50.0,
                 "topup_second_loss_pct": 50.0, "target_cut_pct": 25.0, "floor_target_pct": 10.0,
                 "trail_step_points": 1.0, "amo_at": "17:00", "market_open": "09:15", "market_close": "15:45",
                 "session_refresh_at": "09:14", "trade_amount": 0, "amount_tolerance_pct": 5.0,
                 "gift_check_at": "01:00"}


def _no_channel(job):
    """A job for the page: without the channel's name (owner, 24 Oct 2026)."""
    if not job:
        return job
    j = dict(job)
    if isinstance(j.get("message"), dict):
        j["message"] = {k: v for k, v in j["message"].items() if k != "channel"}
    return j


def _hm(s):
    p = [int(x) for x in str(s).split(":")]
    return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)


def _secs(t):
    return t.hour * 3600 + t.minute * 60 + t.second


def _first_line(text, n=90):
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    s = " / ".join(lines[:2])
    return s if len(s) <= n else s[:n] + "..."


class BtstStrategy:
    def __init__(self, cfg, store, broker, master_fn, llm, channel, log=print, now=now_ist,
                 notifier=None, cost_model=None, prices=None, recorder=None, candles=None):
        self.cfg, self.store, self.broker = cfg, store, broker
        self.master_fn, self.llm, self.channel = master_fn, llm, channel
        self.log, self.now, self.notifier = log, now, notifier
        self.cm = cost_model
        self.prices = prices            # btst/ticks.py PriceWatch: websocket ticks + the day high
        self.inbox = queue.Queue()
        self.last_ltp_at = 0.0
        self.last_store_at = 0.0
        self.last_order_check = 0.0
        self.touch_said = None
        self.managing = False
        self.last_trail_modify = 0.0
        self.last_offhours_price = 0.0     # 0: the first pass after a start fetches at once
        self.gift_fetch = G.fetch           # GIFT Nifty (btst/gift.py); tests replace it
        self.gift_failed_at = 0.0
        self.recorder = recorder            # btst/recorder.py: every price of a trade's option
        self.candles_fn = candles           # (contract, from, to) -> 1-minute bars, to fill gaps
        self.rec_jobs, self.rec_map, self.rec_checked, self.rec_failed = [], {}, 0.0, {}
        self.rec_candle_tries = {}          # job_id -> saves put off for Angel's candles
        if self.prices is not None and self.recorder is not None:
            self.prices.on_tick = self._on_tick
        self.health = {}                # the runner's view: session, websocket, reader, holidays
        self.lock = threading.RLock()   # one pass of step() or one mode switch at a time
        self.last_status_log = 0.0
        self.window_said = {}           # day -> "open" / "closed" already logged
        self.status_note = "starting"

    # ── settings and logging ────────────────────────────────────────────────
    def c(self, k, default=None):
        return self.cfg.get(k, default)

    def _market_open(self, now):
        """Orders can be placed: market_open - market_close (09:15 - 15:45) on a trading day."""
        return market_phase(now, start=self.c("market_open", "09:15"), end=self.c("market_close", "15:45")) == "open"

    def target_mult(self, index):
        m = self.c("target_mult", {}) or {}
        return float(m.get(index, m.get("default", 2.0)))

    def _ts(self):
        return self.now().isoformat(timespec="seconds")

    def _log(self, area, text, level="INFO"):
        self.log(fmt(level, area, text))

    def _say(self, text, job_id=None, notify=False, area="event", level="INFO"):
        """Log it, keep it on the job in MongoDB, and (notify) send it to Telegram."""
        self._log(area, text + (f" [{job_id}]" if job_id else ""), level)
        if job_id:
            try:
                self.store.add_event(job_id, self._ts(), text)
            except Exception as e:
                self._log("mongo", f"event write for {job_id} failed: {e!r}", "ERROR")
        if notify and self.notifier:
            try:
                self.notifier.send(f"🌙 <b>BTST</b> {text}")
            except Exception as e:
                self._log("telegram", f"alert not sent: {e!r}", "WARN")

    def _history(self, jid, kind, **fields):
        """One line of the job's target history (placed, amo, trail, cancel ...)."""
        try:
            self.store.update_job(jid, push={"target_history": dict(at=self._ts(), kind=kind, **fields)})
        except Exception as e:
            self._log("mongo", f"target history for {jid} not saved: {e!r}", "ERROR")

    def _next_session(self, day):
        """The first trading day after `day` (weekends and holidays skipped)."""
        d = day + timedelta(days=1)
        while d.weekday() >= 5 or d.isoformat() in holidays():
            d += timedelta(days=1)
        return d

    # ── messages (the Telegram reader's thread puts, step() takes) ─────────
    def on_message(self, msg):
        text = getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""
        self.inbox.put({"id": int(msg.id), "date": msg.date, "text": text})

    def _ist(self, when):
        return when.astimezone(IST) if when.tzinfo else when.replace(tzinfo=IST)

    def _in_window(self, when):
        """A message posted today between scan_start and scan_end (IST)."""
        t, now = self._ist(when), self.now()
        return (t.date() == now.date()
                and _hm(self.c("scan_start", "15:15")) <= _secs(t) < _hm(self.c("scan_end", "15:30")))

    def _window_edges(self):
        """Say once a day when the scan window opens and when it closes."""
        now = self.now()
        day = now.date().isoformat()
        if now.weekday() >= 5 or day in holidays():
            return
        s, e, t = _hm(self.c("scan_start", "15:15")), _hm(self.c("scan_end", "15:30")), _secs(now)
        said = self.window_said.setdefault(day, set())
        if s <= t < e and "open" not in said:
            said.add("open")
            self._log("window", f"scan window OPEN {self.c('scan_start', '15:15')}-{self.c('scan_end', '15:30')}: "
                                f"reading the channel for a BTST signal")
        if t >= e and "closed" not in said and ("open" in said or t < e + 3600):
            said.add("closed")
            read = self.store.messages_on(day)
            sigs = [m for m in read if m.get("is_signal")]
            self._log("window", f"scan window CLOSED: {len(read)} message(s) read today, {len(sigs)} signal(s)")

    def _process_messages(self):
        skipped = 0
        while True:
            try:
                m = self.inbox.get_nowait()
            except queue.Empty:
                break
            posted = self._ist(m["date"])
            if not self._in_window(m["date"]):
                skipped += 1                    # not today's scan window - never read
                continue
            if self.store.message_seen(self.channel, m["id"]):
                self._log("message", f"#{m['id']} ({posted:%H:%M:%S}) already read before - skipped")
                continue
            self._log("message", f"#{m['id']} posted {posted:%H:%M:%S}: {_first_line(m['text'])}")
            if self.llm is not None and S.has_markers(m["text"], tuple(self.c("markers", ["BTST", "Disclaimer"]))):
                self._log("ai", f"#{m['id']} has the markers - asking {self.c('ai_model') or 'the model'} to read it")
            t0 = time.time()
            res = S.parse(m["text"], llm=self.llm, model=self.c("ai_model"),
                          markers=tuple(self.c("markers", ["BTST", "Disclaimer"])),
                          ai_fallback_regex=bool(self.c("ai_fallback_regex", True)),
                          thinking=self.c("ai_thinking", "low"))
            ai = res.get("ai") or {}
            if "answer" in ai:
                self._log("ai", f"#{m['id']} model answered in {time.time() - t0:.1f}s: {ai['answer']}")
            elif "error" in ai:
                self._log("ai", f"#{m['id']} model call failed: {ai['error']}", "WARN")
            strict = S.strict_read(m["text"])
            if strict:
                self._log("signal", f"#{m['id']} strict read of the line: {strict}")
            doc = {"channel": self.channel, "msg_id": m["id"], "date": posted.isoformat(timespec="seconds"),
                   "text": m["text"], "is_signal": res["is_signal"], "reason": res["reason"],
                   "parsed_by": res["parsed_by"], "signal": res["signal"], "ai": res["ai"],
                   "read_at": self._ts()}
            try:
                self.store.save_message(doc)
            except Exception as e:
                self._log("mongo", f"#{m['id']} could not be saved: {e!r}", "ERROR")
            if not res["is_signal"]:
                self._log("signal", f"#{m['id']} NOT a signal - {res['reason']}",
                          "WARN" if "refused" in res["reason"] or "unavailable" in res["reason"] else "INFO")
                continue
            self._log("signal", f"#{m['id']} SIGNAL ({res['parsed_by']}): {res['signal']} - {res['reason']}")
            self._on_signal(res, m)
        if skipped:
            self._log("message", f"{skipped} message(s) outside today's "
                                 f"{self.c('scan_start', '15:15')}-{self.c('scan_end', '15:30')} window - not read")

    def _on_signal(self, res, m):
        today = self.now().date().isoformat()
        sig = res["signal"]
        what = f"{sig['indices']} {sig['strike_price']:g} {sig['side']} @ {sig['price']:g}"
        done = self.store.job_for_trade_date(today)
        if done:
            self._say(f"signal {what} (message {m['id']}) ignored - one BTST a day, "
                      f"today's is {done['job_id']} ({done.get('status')})", area="signal", level="WARN")
            return
        j = self.store.open_job()
        if j:
            self._say(f"signal {what} ignored - {j['job_id']} is still open", area="signal", level="WARN")
            return
        close = self.c("market_close", "15:45")
        if _secs(self.now()) >= _hm(close):
            self._after_close_signal(sig, res, m, what, close)
            return
        self._log("entry", f"signal {what} accepted - entering ({self.broker.mode.upper()})")
        self._enter(sig, res, m)

    def _after_close_signal(self, sig, res, m, what, close):
        """Read after the market closed: kept as the day's job (rejected, with
        the signal and the reason) - on the page and in MongoDB - not traded."""
        now = self.now()
        posted = self._ist(m["date"])
        job = {"job_id": self.store.new_job_id(), "status": "rejected", "mode": self.broker.mode,
               "trade_date": now.date().isoformat(), "created_at": now.isoformat(timespec="seconds"),
               "message": {"channel": self.channel, "msg_id": m["id"], "date": posted.isoformat(timespec="seconds"),
                           "text": m["text"]},
               "signal": dict(sig, parsed_by=res["parsed_by"]), "orders": [], "events": [],
               "reject_reason": f"posted {posted:%H:%M:%S}, after the market closed at {close} - "
                                f"no order possible until the next open"}
        jid = self.store.insert_job(job)
        self._say(f"signal {what} (message {m['id']}, posted {posted:%H:%M:%S}) read after the market closed "
                  f"at {close} - saved, NOT traded (no order can be placed until tomorrow's open)",
                  jid, True, "signal", "WARN")

    # ── entry ───────────────────────────────────────────────────────────────
    def _enter(self, sig, res, m):
        now = self.now()
        today = now.date()
        job = {"job_id": self.store.new_job_id(), "status": "entering", "mode": self.broker.mode,
               "trade_date": today.isoformat(), "created_at": now.isoformat(timespec="seconds"),
               "message": {"channel": self.channel, "msg_id": m["id"],
                           "date": self._ist(m["date"]).isoformat(timespec="seconds"), "text": m["text"]},
               "signal": dict(sig, parsed_by=res["parsed_by"]), "orders": [], "events": []}
        jid = self.store.insert_job(job)
        self._log("entry", f"job {jid} created (status entering)")
        what = f"{sig['indices']} {sig['strike_price']:g} {sig['side']} @ {sig['price']:g}"
        try:
            master = self.master_fn()
            c = find_option(master, sig["indices"], sig["strike_price"], sig["side"], today)
        except Exception as e:                  # never leave the job 'entering': it would block the day
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": f"contract lookup: {e!r}"})
            self._say(f"{what}: contract lookup failed ({e!r}) - not traded", jid, True, "contract", "ERROR")
            self._log("contract", traceback.format_exc(), "ERROR")
            return
        if c is None:
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": "no such contract"})
            self._say(f"{what}: no {sig['indices']} {sig['strike_price']:g} {sig['side']} contract after today "
                      f"in the scrip master ({len(master)} rows) - not traded", jid, True, "contract", "WARN")
            return
        self._log("contract", f"{c['symbol']} token {c['token']} on {c['exchange']}, expiry {c['expiry']} "
                              f"({days_left(c, today)} days), lot {c['lot_size']}, tick {c['tick']}")
        ltp = self.broker.ltp(c)
        self.store.update_job(jid, set_={"contract": c, "ltp_at_signal": ltp, "days_to_expiry": days_left(c, today)})
        if ltp is None:
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": "no LTP"})
            self._say(f"{what}: no price for {c['symbol']} - not traded", jid, True, "entry", "ERROR")
            return
        lots, sizing = self._size(c, ltp)
        if not lots:
            self.store.update_job(jid, set_={"status": "rejected", "reject_reason": sizing, "sizing": sizing})
            self._say(f"{what}: {sizing} - not traded", jid, True, "entry", "WARN")
            return
        qty = lots * c["lot_size"]
        self._log("entry", f"{c['symbol']}: {lots} lot(s) = {qty} qty, LTP {ltp} (signal {sig['price']:g}) - {sizing}")
        self.store.update_job(jid, set_={"lots": lots, "sizing": sizing})
        need = ltp * qty * 1.05
        funds = self.broker.funds()
        self._log("entry", f"funds check: need about Rs {need:,.0f} (LTP x qty + 5%), "
                           f"available Rs {funds:,.0f}" if funds is not None else "funds unreadable")
        if funds is not None and funds < need:
            self.store.update_job(jid, set_={"status": "rejected",
                                             "reject_reason": f"funds Rs {funds:,.0f} < Rs {need:,.0f}"})
            self._say(f"{what}: {c['symbol']} needs about Rs {need:,.0f}, the account has "
                      f"Rs {funds:,.0f} - not traded", jid, True, "entry", "WARN")
            return
        self._log("order", f"BUY {c['symbol']} x{qty} at market ({self.broker.mode})", "TRADE")
        buy = self.broker.buy_market(c, qty)
        buy.update(kind="buy", at=self._ts())
        self.store.add_order(jid, buy)
        if not buy.get("ok"):
            self.store.update_job(jid, set_={"status": "failed", "reject_reason": buy.get("error")})
            self._say(f"{what}: BUY {c['symbol']} x{qty} not filled - {buy.get('error')}", jid, True,
                      "order", "ERROR")
            return
        q, px = int(buy["filled"]), float(buy["avg_price"])
        mult = self.target_mult(sig["indices"])
        target = tick_round(sig["price"] * mult, c["tick"], up=True)
        self._log("order", f"BUY filled {q} @ {px:.2f} (order {buy.get('order_id')}, status {buy.get('status')})",
                  "TRADE")
        pos = {"qty": q, "avg_price": round(px, 2), "invested": round(px * q, 2),
               "entry_price": round(px, 2), "entry_qty": q, "entry_at": self._ts()}
        self.store.update_job(jid, set_={"status": "open", "position": pos, "target_price": target,
                                         "target_mult": mult, "ltp_high": px, "ltp_low": px})
        self._start_recording(jid, c, pos["entry_at"], today)
        self._say(f"BOUGHT {c['symbol']} x{q} @ {px:.2f} (Rs {px * q:,.0f}; signal {sig['price']:g}); "
                  f"target {target:.2f} = {mult:g} x the signal's price", jid, True, "entry", "TRADE")
        self._place_target(self.store.get_job(jid), target, "target")

    def _size(self, c, ltp):
        """(lots, why). trade_amount (Rs 15,000) + amount_tolerance_pct (5%),
        the same every trade - no compounding: the most lots whose total is
        below the amount + 5% (owner, 24 Oct 2026); none if one lot is not. With
        no trade_amount, a fixed `lots`."""
        amount = float(self.c("trade_amount", 0) or 0)
        per_lot = ltp * int(c["lot_size"])
        if amount <= 0:
            n = max(1, int(self.c("lots", 1)))
            return n, f"fixed {n} lot(s) = Rs {n * per_lot:,.0f}"
        tol = float(self.c("amount_tolerance_pct", 5.0))
        hi, lo = amount * (1 + tol / 100.0), amount * (1 - tol / 100.0)
        n = int(math.ceil(hi / per_lot - 1e-9)) - 1        # the most lots whose total stays below the cap
        if n < 1:
            return 0, (f"one lot ({c['lot_size']} x {ltp:g} = Rs {per_lot:,.0f}) is not below Rs {hi:,.0f} "
                       f"(Rs {amount:,.0f} + {tol:g}%)")
        cost = n * per_lot
        where = "within" if cost >= lo else "under"
        return n, (f"{n} lot(s) x Rs {per_lot:,.0f} = Rs {cost:,.0f} for Rs {amount:,.0f} +/- {tol:g}% "
                   f"({where} the band Rs {lo:,.0f} - {hi:,.0f}; always below Rs {hi:,.0f})")

    def _place_target(self, job, price, kind):
        c, q = job["contract"], int(job["position"]["qty"])
        self._log("order", f"SELL {c['symbol']} x{q} LIMIT {price:.2f} DAY ({kind})", "TRADE")
        o = self.broker.place_limit_sell(c, q, price)
        o.update(kind=kind, at=self._ts(), price=price)
        self.store.add_order(job["job_id"], o)
        self.store.update_job(job["job_id"], set_={"target_price": price, "target_order": o,
                                                   "target_last_status": o.get("status"),
                                                   "soft_target": not o.get("ok")})
        self._history(job["job_id"], kind, price=price, qty=q, order_id=o.get("order_id"),
                      variety=o.get("variety", "NORMAL"), ok=bool(o.get("ok")), status=o.get("status"),
                      error=o.get("error"), watched=not o.get("ok"))
        if o.get("ok"):
            self._say(f"target SELL {c['symbol']} x{q} @ {price:.2f} placed ({o.get('order_id')}, "
                      f"{o.get('status')})", job["job_id"], area="order", level="TRADE")
        else:
            self._say(f"target order refused ({o.get('error')}) - watching the price instead and selling "
                      f"at market at {price:.2f}", job["job_id"], True, "order", "WARN")

    # ── the next day ────────────────────────────────────────────────────────
    def _manage(self, job):
        now = self.now()
        if not self._market_open(now):
            return
        jid = job["job_id"]
        ltp = self._watch(job)                  # first, so the day's high and low include this pass
        tp = job.get("target_price")
        peak = self.prices.peak() if self.prices is not None else None
        touched = bool(tp and ((peak and peak >= tp) or (ltp and ltp >= tp)))
        if touched and self.touch_said != (jid, tp):
            self.touch_said = (jid, tp)
            self._log("manage", f"the price reached the target {tp:.2f}: LTP {ltp}, highest traded since the "
                                f"target went in {peak} - checking the order now", "TRADE")
        to = job.get("target_order")
        st = None
        # Paper decides its fill here, every pass. Live, the exchange fills the
        # order whatever we do: its state is read every order_poll_sec, and at
        # once when the price has reached the target.
        due = (self.broker.mode == "paper" or touched
               or time.time() - self.last_order_check >= float(self.c("order_poll_sec", 3)))
        if to and to.get("ok") and due:
            self.last_order_check = time.time()
            st = self.broker.order_state(to)
            status = (st or {}).get("status", "unreadable")
            if status != job.get("target_last_status"):
                self._log("order", f"target order {to.get('order_id')} is now {status}"
                                   + (f" (filled {st['filled']} @ {st['avg']})" if st and st.get("filled") else ""),
                          "TRADE" if status == "complete" else ("WARN" if status in ("rejected", "unreadable") else "INFO"))
                self.store.update_job(jid, set_={"target_last_status": status})
                job["target_last_status"] = status
            if st and st["status"] == "complete":
                self._close(job, "target", st["avg"] or job["target_price"], None)
                return
            if (st and to.get("kind") == "amo_target" and st["status"] in ("rejected", "cancelled")
                    and now.date().isoformat() > job["trade_date"]):
                self._say(f"the AMO target {to.get('order_id')} was {st['status']} at the open"
                          + (f" ({st['text']})" if st.get("text") else "")
                          + f" - placing a normal target at {tp:.2f}", jid, True, "order", "WARN")
                self._place_target(job, tp, "target_day2")
                return
        self._status_line(job, ltp, st)
        if job.get("soft_target") and touched:
            self._log("exit", f"the price reached the watched target {tp:.2f} (LTP {ltp}, highest {peak}) - "
                              f"selling", "TRADE")
            self._exit_market(job, "target (watched)")
            return
        if now.date().isoformat() <= job["trade_date"]:
            return                              # the entry day: nothing more until tomorrow
        planned = self._next_session(datetime.fromisoformat(job["trade_date"]).date())
        if now.date() > planned:                # the desk was down on the planned day: out now
            self._log("exit", f"the trade was due out on {planned} (the desk was not running then) - "
                              f"closing at market now", "WARN")
            self._exit_market(job, "overdue_exit")
            return
        if not job.get("decision"):
            self._session_start(job, ltp)       # the open: the target back in, the rules armed
            return
        if _secs(now) >= _hm(self.c("exit_at", "15:14")):
            self._log("exit", f"{self.c('exit_at', '15:14')} reached - target not hit, closing at market", "TRADE")
            self._exit_market(job, "time_exit")
            return
        if self._topup_rules(self.store.get_job(jid) or job, ltp):
            return
        if (job.get("trail") or {}).get("active"):
            self._trail(job, ltp)

    def _status_line(self, job, ltp, st):
        """Every log_status_sec while a trade is open: where it stands."""
        if time.time() - self.last_status_log < float(self.c("log_status_sec", 60)):
            return
        self.last_status_log = time.time()
        p = job["position"]
        pnl = (ltp - p["avg_price"]) * p["qty"] if ltp else None
        pct = 100.0 * (ltp - p["avg_price"]) / p["avg_price"] if ltp else None
        self._log("manage", f"{job['contract']['symbol']} x{p['qty']} avg {p['avg_price']:.2f} | LTP {ltp} "
                            + (f"({pct:+.1f}%, Rs {pnl:+,.0f}) " if pnl is not None else "")
                            + f"| target {job['target_price']:.2f} ({(st or {}).get('status') or ('watched' if job.get('soft_target') else job.get('target_last_status', '?'))})"
                            + (f" | price via {self.prices.state()['source']}" if self.prices is not None else "")
                            + (f" | decision {job['decision']['scenario']}" if job.get("decision") else " | decision at the next open"))

    def _watch(self, job):
        """The price this pass - from the websocket, or the LTP call while it is
        quiet (btst/ticks.py) - with the high and low kept for the metrics."""
        if self.prices is not None:
            ltp = self.prices.price(job["contract"])
            peak = self.prices.peak()
        else:
            if time.time() - self.last_ltp_at < float(self.c("ltp_poll_sec", 1)):
                return job.get("last_ltp")
            self.last_ltp_at = time.time()
            ltp, peak = self.broker.ltp(job["contract"]), None
        if not ltp:
            if time.time() - self.last_store_at >= 30:
                self.last_store_at = time.time()
                self._log("manage", f"no price for {job['contract']['symbol']} this pass", "WARN")
            return job.get("last_ltp")
        hi = max(float(job.get("ltp_high") or ltp), ltp, peak or 0.0)
        lo = min(float(job.get("ltp_low") or ltp), ltp)
        src = (self.prices.state().get("source") or "ltp call") if self.prices is not None else "ltp call"
        upd = {"last_ltp": ltp, "last_ltp_at": self._ts(), "ltp_high": hi, "ltp_low": lo, "last_ltp_source": src}
        moved = hi != job.get("ltp_high") or lo != job.get("ltp_low") or src != job.get("last_ltp_source")
        job.update(upd)
        if moved or time.time() - self.last_store_at >= float(self.c("ltp_store_sec", 5)):
            self.last_store_at = time.time()        # MongoDB: on a new high or low, else every few seconds
            self.store.update_job(job["job_id"], set_=upd)
        return ltp

    def _session_start(self, job, ltp):
        """The first pass of the next session: the open is recorded and the
        target goes back in unless it is still at the exchange (the AMO)."""
        jid, pos = job["job_id"], job["position"]
        if not ltp:
            ltp = self.broker.ltp(job["contract"])
        if not ltp:
            self._log("decision", "no price yet at the open - trying again on the next pass", "WARN")
            return
        base = float(pos["entry_price"])
        pnl_pct = 100.0 * (ltp - base) / base
        decision = {"at": self._ts(), "ltp": ltp, "pnl_pct": round(pnl_pct, 2), "avg_before": pos["avg_price"],
                    "scenario": "hold"}
        self.store.update_job(jid, set_={"decision": decision})
        job["decision"] = decision
        first, second = float(self.c("topup_first_loss_pct", 25.0)), float(self.c("topup_second_loss_pct", 50.0))
        self._say(f"session open: {ltp:.2f} vs the buy {base:.2f} = {pnl_pct:+.1f}%. Watching live: at -{first:g}% "
                  f"({base * (1 - first / 100):.2f}) top up {self.c('topup_first_pct', 50.0):g}% and cut the target "
                  f"{self.c('target_cut_pct', 25.0):g}%; at -{second:g}% ({base * (1 - second / 100):.2f}) top up "
                  f"to 100%, target = average + {self.c('floor_target_pct', 10.0):g}%; out at "
                  f"{self.c('exit_at', '15:14')} if not hit", jid, True, "decision")
        st = self._target_state(job)
        if st and st["status"] == "complete":
            self._close(job, "target", st["avg"] or job["target_price"], None)
            return
        if st and not st["final"]:
            to = job["target_order"]
            self._log("decision", f"the target order {to.get('order_id')} ({to.get('variety', 'NORMAL')}, "
                                  f"{st['status']}) is still at the exchange - kept, no second sell placed")
            self._history(jid, "kept_at_open", price=job["target_price"], order_id=to.get("order_id"),
                          status=st["status"])
        else:
            self._place_target(self.store.get_job(jid), job["target_price"], "target_day2")
        self._topup_rules(self.store.get_job(jid), ltp)

    def _topup_rules(self, job, ltp):
        """Owner, 26 Oct 2026 - the price tracked live, against the FIRST buy
        price; at most 100% of the first amount added in all:
          already -50% or worse, nothing added yet -> add 100%; target =
              the new average + 10%
          first reaches -25%                        -> add 50%; target cut by
              25%, then 1 point lower per point below that buy, never under
              the new average + 10% (there trailing stops)
          after that, reaches -50%                  -> add the other 50%;
              target = the new average + 10% (trailing ends)
        True if it acted this pass."""
        if not ltp or job.get("status") != "open":
            return False
        base = float(job["position"]["entry_price"])
        loss = 100.0 * (base - float(ltp)) / base
        done = float(job.get("topped_pct") or 0.0)
        first, second = float(self.c("topup_first_loss_pct", 25.0)), float(self.c("topup_second_loss_pct", 50.0))
        half = float(self.c("topup_first_pct", 50.0))
        if done <= 0 and loss >= second:
            return self._topup(job, ltp, 100.0, loss, "full")
        if done <= 0 and loss >= first:
            return self._topup(job, ltp, half, loss, "first")
        if 0 < done < 100 and loss >= second:
            return self._topup(job, ltp, 100.0 - done, loss, "second")
        return False

    def _topup(self, job, ltp, pct, loss, step):
        """Add pct% of the first amount at market (whole lots, the count nearest
        it, at least 1); then the target as the rule says. Never two sells: the
        resting target (or AMO) is cancelled first."""
        jid, c = job["job_id"], job["contract"]
        pos = job["position"]
        first_amount = float(pos["entry_price"]) * int(pos["entry_qty"])
        floor_pct = float(self.c("floor_target_pct", 10.0))
        gone = self._cancel_resting(job, f"top-up {pct:g}% at {-loss:.1f}% - a new target for the whole position follows")
        if gone == "closed":
            return True
        if gone == "unsure":
            return True                         # tried again on the next pass
        job = self.store.get_job(jid)
        pos = job["position"]
        lot = int(c["lot_size"])
        amount = first_amount * pct / 100.0
        n = max(1, round(amount / (ltp * lot)))
        add = n * lot
        self._log("decision", f"{-loss:.1f}% below the buy {pos['entry_price']:.2f}: top up {pct:g}% of "
                              f"Rs {first_amount:,.0f} = Rs {amount:,.0f} -> {n} lot(s) = {add} qty @ ~{ltp:.2f} "
                              f"(Rs {add * ltp:,.0f})", "TRADE")
        self._log("order", f"BUY {c['symbol']} x{add} at market (top-up {step})", "TRADE")
        buy = self.broker.buy_market(c, add)
        buy.update(kind=f"topup_{step}", at=self._ts())
        self.store.add_order(jid, buy)
        if not buy.get("ok"):
            fails = int(job.get("topup_failures") or 0) + 1
            self.store.update_job(jid, set_={"topup_failures": fails})
            self._say(f"top-up BUY x{add} not filled ({buy.get('error')}) - the target {job['target_price']:.2f} "
                      f"goes back in" + ("; trying again on the next pass" if fails < 3 else "; no more tries"),
                      jid, True, "order", "ERROR")
            if fails >= 3:
                self.store.update_job(jid, set_={"topped_pct": 100.0})
            self._place_target(self.store.get_job(jid), job["target_price"], "target_after_failed_topup")
            return True
        q2, p2 = int(buy["filled"]), float(buy["avg_price"])
        q = int(pos["qty"]) + q2
        avg = (float(pos["avg_price"]) * int(pos["qty"]) + p2 * q2) / q
        floor = tick_round(avg * (1 + floor_pct / 100.0), c["tick"], up=True)
        done = float(job.get("topped_pct") or 0.0) + pct
        if step == "first":
            target = max(floor, tick_round(float(job["target_price"]) * (1 - float(self.c("target_cut_pct", 25.0)) / 100.0),
                                           c["tick"], up=True))
        else:
            target = floor
        topups = list(job.get("topups") or []) + [{"at": buy["at"], "step": step, "pct": pct, "loss_pct": round(loss, 2),
                                                   "ltp": round(float(ltp), 2), "qty": q2, "price": round(p2, 2),
                                                   "amount": round(p2 * q2, 2), "order_id": buy.get("order_id"),
                                                   "qty_before": int(pos["qty"]), "qty_after": q,
                                                   "avg_before": round(float(pos["avg_price"]), 2),
                                                   "avg_after": round(avg, 2),
                                                   "invested_after": round(float(pos["invested"]) + p2 * q2, 2),
                                                   "target_before": job.get("target_price"), "target_after": target,
                                                   "floor": floor, "mode": job.get("mode")}]
        new_pos = dict(pos, qty=q, avg_price=round(avg, 2), invested=round(float(pos["invested"]) + p2 * q2, 2),
                       added_qty=int(pos.get("added_qty") or 0) + q2, added_price=round(p2, 2),
                       added_amount=round(float(pos.get("added_amount") or 0) + p2 * q2, 2), added_at=buy["at"])
        decision = dict(job.get("decision") or {}, scenario="topup_100" if done >= 100 else "topup_50",
                        added_qty=new_pos["added_qty"], added_price=round(p2, 2), avg_after=round(avg, 2),
                        added_amount=new_pos["added_amount"], first_invested=round(first_amount, 2))
        upd = {"position": new_pos, "topped_pct": done, "topups": topups, "decision": decision}
        if step == "first" and target > floor:
            upd["trail"] = {"active": True, "ref": round(p2, 2), "low": round(p2, 2), "initial": target, "floor": floor,
                            "step": float(self.c("trail_step_points", 1.0)), "started_at": buy["at"], "steps": []}
        elif job.get("trail"):
            upd["trail"] = dict(job["trail"], active=False, stopped_at=self._ts(),
                                stop_reason=f"top-up to {done:g}%: target = average + {floor_pct:g}%")
        self.store.update_job(jid, set_=upd)
        self._say(f"{-loss:.1f}% below the buy: topped up {pct:g}% ({q2} @ {p2:.2f}, Rs {p2 * q2:,.0f}) - now "
                  f"{q} @ {avg:.2f}; target {target:.2f}"
                  + (f" (cut {self.c('target_cut_pct', 25.0):g}%; then 1 point lower per point below {p2:.2f}, "
                     f"never under {floor:.2f})" if step == "first" and target > floor
                     else f" (= average + {floor_pct:g}%)"), jid, True, "decision", "TRADE")
        self._place_target(self.store.get_job(jid), target, f"target_after_topup_{step}")
        return True

    # ── the trailing target (after the first top-up) ───────────────────────
    def _trail(self, job, ltp):
        """1 point lower for each point the price has fallen below the top-up
        buy (the lowest since then), never under the floor (the average + 10%);
        at the floor the trailing stops. It only ever comes down."""
        tr = dict(job["trail"])
        if not ltp:
            return
        jid, c = job["job_id"], job["contract"]
        low = min(float(tr["low"]), float(ltp))
        moved_low = low < float(tr["low"])
        tr["low"] = round(low, 2)
        drop = math.floor((float(tr["ref"]) - low) / float(tr["step"]) + 1e-9) * float(tr["step"])
        want = max(float(tr["floor"]), tick_round(float(tr["initial"]) - max(0.0, drop), c["tick"], up=True))
        cur = float(job["target_price"])
        if want >= cur - 1e-9 or time.time() - self.last_trail_modify < float(self.c("trail_modify_gap_sec", 3)):
            if moved_low:
                self.store.update_job(jid, set_={"trail": tr})
                job["trail"] = tr
            return
        self.last_trail_modify = time.time()
        why = (f"price {ltp} - lowest {low:.2f}, {drop:g} point(s) below the top-up buy {float(tr['ref']):.2f}")
        if not self._move_target(job, want, "trail", why):
            self.store.update_job(jid, set_={"trail": tr})
            return
        tr["steps"] = list(tr.get("steps") or []) + [{"at": self._ts(), "ltp": ltp, "low": tr["low"],
                                                     "from": cur, "to": want}]
        if want <= float(tr["floor"]) + 1e-9:
            tr.update(active=False, stopped_at=self._ts(),
                      stop_reason=f"reached the floor {float(tr['floor']):.2f} (average + "
                                  f"{self.c('floor_target_pct', 10.0):g}%) - trailing stops")
            self._say(f"target trailed to its floor {want:.2f} - trailing stops", jid, True, "trail", "TRADE")
        self.store.update_job(jid, set_={"trail": tr})
        job["trail"] = tr

    def _move_target(self, job, price, kind, why):
        """The resting target to `price` (modified in place: one sell all along).
        True if the target is now `price`."""
        jid, c = job["job_id"], job["contract"]
        q, cur = int(job["position"]["qty"]), float(job["target_price"])
        to = job.get("target_order") or {}
        if job.get("soft_target") or not to.get("ok"):
            self.store.update_job(jid, set_={"target_price": price})
            job["target_price"] = price
            self._history(jid, kind, price=price, previous=cur, watched=True, why=why)
            self._log("trail", f"watched target {cur:.2f} -> {price:.2f} ({why})", "TRADE")
            return True
        r = self.broker.modify_sell(to, c, q, price)
        if r.get("ok"):
            to = dict(to, price=price, price_sent=price)
            self.store.update_job(jid, set_={"target_price": price, "target_order": to})
            job.update(target_price=price, target_order=to)
            self._history(jid, kind, price=price, previous=cur, order_id=to.get("order_id"), why=why)
            self._log("trail", f"target {cur:.2f} -> {price:.2f} ({why})", "TRADE")
            return True
        st = self.broker.order_state(to)
        if st and st["status"] == "complete":
            self._log("trail", "the target had filled before the modify - closing as a target hit", "TRADE")
            self._close(job, "target", st["avg"] or cur, None)
            return False
        if st and st["final"]:
            self._log("trail", f"the target {to.get('order_id')} is {st['status']} - placing a new one at {price:.2f}",
                      "WARN")
            self._place_target(job, price, "target_trail")
            return True
        self._log("trail", f"modify to {price:.2f} refused ({r.get('error')}) - trying again shortly", "WARN")
        self._history(jid, kind + "_refused", price=price, previous=cur, order_id=to.get("order_id"),
                      error=r.get("error"))
        return False

    def _target_state(self, job):
        """The resting target's state at the broker, or None if there is none."""
        to = job.get("target_order")
        if not (to and to.get("ok")):
            return None
        return self.broker.order_state(to)

    def _cancel_resting(self, job, why):
        """Cancel the target still at the exchange (the AMO) before another
        sell goes in - never two at once. 'closed' if it had filled (closed as a
        target hit), 'unsure' if the cancel is not confirmed, else None."""
        jid, c = job["job_id"], job["contract"]
        st = self._target_state(job)
        if st is None or (st["final"] and st["status"] != "complete"):
            return None
        to = job["target_order"]
        if st["status"] != "complete":
            self._log("order", f"cancelling the target {to.get('order_id')} ({to.get('variety', 'NORMAL')}) - {why}")
            st = self.broker.cancel(to)
            self.store.add_order(jid, {"kind": "target_cancel", "at": self._ts(), "order_id": to.get("order_id"),
                                       "variety": to.get("variety", "NORMAL"), "state": st, "why": why})
        if st and st["status"] == "complete":
            self._log("exit", "the target had filled - closing as a target hit", "TRADE")
            self._close(job, "target", st["avg"] or job["target_price"], None)
            return "closed"
        if not st or not st["final"]:
            self._say(f"the target {to.get('order_id')} did not confirm cancelled ({(st or {}).get('status')}) - "
                      f"no new order sent; check the Angel app", jid, True, "order", "ERROR")
            return "unsure"
        if st.get("filled"):
            held = self.broker.net_qty(c)
            if held is not None:
                self.store.update_job(jid, set_={"position": dict(job["position"], qty=int(held))})
            self._say(f"{st['filled']} of the target filled before the cancel - the account holds {held}",
                      jid, True, "order", "WARN")
        return None

    # ── after the close (live): the target as an AMO for the next open ─────
    def _amo_slot(self, now):
        """The day of the latest amo_at (17:00): today's from 17:00, yesterday's
        until amo_until (08:55) the next morning; None in between."""
        t = _secs(now)
        if t >= _hm(self.c("amo_at", "17:00")):
            return now.date()
        if t < _hm(self.c("amo_until", "08:55")):
            return now.date() - timedelta(days=1)
        return None

    def _amo_check(self, job):
        """Every day at amo_at - weekends and holidays too: Angel drops an AMO
        over a weekend or a holiday - while a live trade waits for its next
        session: the target AMO is kept if it is at Angel, placed if not."""
        if (self.broker.mode != "live" or not self.c("amo_exit", True)
                or not hasattr(self.broker, "place_amo_sell") or job.get("decision")):
            return
        now = self.now()
        slot = self._amo_slot(now)
        if slot is None or slot.isoformat() < job["trade_date"]:
            return
        jid, key = job["job_id"], slot.isoformat()
        rec = job.get("amo_check") or {}
        if rec.get("slot") == key:
            if rec.get("done") or int(rec.get("attempts") or 0) >= int(self.c("amo_tries", 3)):
                return
            if rec.get("failed_at") and (now - datetime.fromisoformat(rec["failed_at"])).total_seconds() \
                    < float(self.c("amo_retry_sec", 300)):
                return
        else:
            rec = {"slot": key, "attempts": 0}
        to = job.get("target_order") or {}
        st = self._target_state(job)
        if st and st["status"] == "complete":
            self._close(job, "target", st["avg"] or job["target_price"], None)
            return
        if st and not st["final"] and to.get("kind") == "amo_target":
            rec.update(done=True, checked_at=self._ts(), action="kept")
            self.store.update_job(jid, set_={"amo_check": rec},
                                  push={"amo_history": {"at": self._ts(), "slot": key, "action": "kept",
                                                        "order_id": to.get("order_id"), "status": st["status"]}})
            self._log("order", f"{key} {self.c('amo_at', '17:00')} check: the AMO target {to.get('order_id')} is at "
                               f"Angel ({st['status']}) - kept")
            return
        why = ("no target order at Angel" if not to.get("ok") else
               f"the target {to.get('order_id')} ({to.get('variety', 'NORMAL')}) is "
               f"{(st or {}).get('status', 'unreadable')}")
        o = self._place_amo(job, why)
        rec["attempts"] = int(rec.get("attempts") or 0) + 1
        hist = {"at": self._ts(), "slot": key, "action": "placed" if o.get("ok") else "refused", "why": why,
                "order_id": o.get("order_id"), "status": o.get("status"), "error": o.get("error"),
                "price": job["target_price"], "qty": int(job["position"]["qty"])}
        if o.get("ok"):
            rec.update(done=True, checked_at=self._ts(), action="placed")
        else:
            rec.update(failed_at=self._ts(), error=o.get("error"))
            last = rec["attempts"] >= int(self.c("amo_tries", 3))
            self._say(f"AMO target refused ({o.get('error')}) - "
                      + ("no more tries: the target goes in normally at the open" if last
                         else f"retrying in {float(self.c('amo_retry_sec', 300)) / 60:g} min "
                              f"({rec['attempts']}/{self.c('amo_tries', 3)})"),
                      jid, last, "order", "ERROR" if last else "WARN")
        self.store.update_job(jid, set_={"amo_check": rec}, push={"amo_history": hist})

    def _place_amo(self, job, why):
        jid, c = job["job_id"], job["contract"]
        q, tp = int(job["position"]["qty"]), float(job["target_price"])
        self._log("order", f"AMO SELL {c['symbol']} x{q} LIMIT {tp:.2f} for the next open ({why})", "TRADE")
        o = self.broker.place_amo_sell(c, q, tp)
        o.update(kind="amo_target", at=self._ts(), price=tp)
        self.store.add_order(jid, o)
        if o.get("ok"):
            amo = {"order_id": o.get("order_id"), "uid": o.get("uid"), "price": tp, "qty": q,
                   "placed_at": o["at"], "status": o.get("status"), "why": why}
            self.store.update_job(jid, set_={"amo": amo, "target_order": o, "target_last_status": o.get("status"),
                                             "soft_target": False})
            self._history(jid, "amo_target", price=tp, qty=q, order_id=o.get("order_id"), variety="AMO",
                          status=o.get("status"), why=why)
            self._say(f"AMO target SELL {c['symbol']} x{q} @ {tp:.2f} placed for the next open "
                      f"({o.get('order_id')}, {o.get('status')}); cancelled and sold at market at "
                      f"{self.c('exit_at', '15:14')} if not hit", jid, True, "order", "TRADE")
        return o

    def _after_close(self, job):
        self._amo_check(job)
        self._gift(job)

    # ── recording a trade's prices (btst/recorder.py) ──────────────────────
    def _start_recording(self, jid, c, start_iso, trade_day):
        """From the fill to market_close of the next trading day - past the exit too."""
        if self.recorder is None:
            return
        until = datetime.combine(self._next_session(trade_day), dtime.fromisoformat(self.c("market_close", "15:45")),
                                 tzinfo=IST)
        rec = {"status": "recording", "token": str(c["token"]), "symbol": c["symbol"], "exchange": c["exchange"],
               "from": start_iso, "until": until.isoformat(timespec="seconds"), "file": self.recorder.path(jid)}
        self.store.update_job(jid, set_={"recording": rec})
        self.rec_checked = 0.0
        self._log("record", f"recording {c['symbol']}'s prices for {jid} until {rec['until']} (every tick, "
                            f"saved to MongoDB after that)")

    def _on_tick(self, token, price, ts):
        """The websocket's thread (and LTP calls): a price for each trade recording this contract."""
        for jid in self.rec_map.get(str(token), ()):
            self.recorder.record(jid, ts, price)

    def _recording_pass(self):
        if self.recorder is None:
            return
        if time.time() - self.rec_checked >= 10:
            self.rec_checked = time.time()
            try:
                self.rec_jobs = self.store.recording_jobs()
            except Exception as e:
                self._log("record", f"recording list unreadable: {e!r}", "WARN")
        now, live, rec_map = self.now(), [], {}
        for j in self.rec_jobs:
            r = j.get("recording") or {}
            until = datetime.fromisoformat(r["until"])
            if now >= until + timedelta(minutes=2):
                if time.time() - self.rec_failed.get(j["job_id"], 0.0) >= 300:
                    self._finalize_recording(j)
                continue
            live.append(j)
            rec_map.setdefault(r["token"], []).append(j["job_id"])
        self.rec_map = rec_map
        if self.prices is not None:
            self.prices.set_extra([j["contract"] for j in live if j.get("contract")])

    def _finalize_recording(self, job):
        """The window is over: 1-minute bars (gaps from Angel's candles), the
        gzipped ticks and the trade's events into MongoDB; the local file goes
        once the copy is checked."""
        jid, r, c = job["job_id"], job["recording"], job.get("contract") or {}
        try:
            ticks = self.recorder.read(jid)
            tick_bars = REC.bars(ticks)
            candle_bars = []
            if self.candles_fn is not None:
                start, end = datetime.fromisoformat(r["from"]), datetime.fromisoformat(r["until"])
                d = start.date()
                while d <= end.date():
                    if d.weekday() < 5 and d.isoformat() not in holidays():
                        a = max(start, datetime.combine(d, dtime.fromisoformat(self.c("market_open", "09:15")), tzinfo=IST))
                        b = min(end, datetime.combine(d, dtime.fromisoformat(self.c("market_close", "15:45")), tzinfo=IST))
                        if a < b:
                            try:
                                candle_bars += self.candles_fn(c, a, b) or []
                            except Exception as e:
                                tries = self.rec_candle_tries.get(jid, 0) + 1
                                self.rec_candle_tries[jid] = tries
                                if tries < 3:           # often a rate limit: the save waits and tries again
                                    raise RuntimeError(f"Angel's 1-minute candles for {d} not read ({e!r}); "
                                                       f"try {tries} of 3")
                                self._log("record", f"{jid}: Angel's 1-minute candles for {d} not read ({e!r}) - "
                                                    f"saved with the ticks alone", "WARN")
                    d += timedelta(days=1)
            merged = REC.merge(tick_bars, candle_bars)
            gz = self.recorder.compress(jid)
            try:
                from bson import Binary
                gz_value = Binary(gz)
            except ImportError:
                gz_value = gz
            filled = sum(1 for b in merged if b.get("src") == "candle")
            fresh = self.store.get_job(jid) or job
            ext = CH.extremes(fresh, merged)
            self.store.save_ticks({"job_id": jid, "symbol": r.get("symbol"), "token": r.get("token"),
                                   "exchange": r.get("exchange"), "from": r.get("from"), "until": r.get("until"),
                                   "count": len(ticks), "bars": merged, "gz": gz_value, "gz_bytes": len(gz),
                                   "candle_minutes": filled, "events": CH.events(fresh), "extremes": ext,
                                   "saved_at": self._ts()})
            check = self.store.get_ticks(jid)
            if not check or int(check.get("count", -1)) != len(ticks):
                raise RuntimeError("the saved copy does not match the local file")
            self.recorder.remove(jid)
            self.store.update_job(jid, set_={"recording": dict(r, status="saved", count=len(ticks), bars=len(merged),
                                                               candle_minutes=filled, gz_bytes=len(gz),
                                                               extremes=ext, saved_at=self._ts())})
            self.rec_checked = 0.0
            self._log("record", f"{jid}: {len(ticks)} prices -> {len(merged)} 1-minute bars ({filled} from Angel's "
                                f"candles), {len(gz):,} bytes gzipped - saved to MongoDB; local file removed")
        except Exception as e:
            self.rec_failed[jid] = time.time()
            self._log("record", f"{jid}: recording not saved ({e!r}) - the local file stays; trying again in 5 min",
                      "ERROR")

    def extremes(self, job):
        """Max profit / max loss over the recorded window: saved with the
        recording, or from the local file while it runs (cached 15 s)."""
        rec = job.get("recording") or {}
        if rec.get("status") == "saved":
            return rec.get("extremes")
        if rec.get("status") != "recording" or self.recorder is None:
            return None
        cache = getattr(self, "_ext_cache", {})
        hit = cache.get(job["job_id"])
        if hit and time.time() - hit[0] < 15:
            return hit[1]
        ext = CH.extremes(job, REC.bars(self.recorder.read(job["job_id"])))
        if ext:
            ext["basis"] = "recorded so far (the recording runs to " + rec.get("until", "?")[:16].replace("T", " ") + ")"
        cache[job["job_id"]] = (time.time(), ext)
        self._ext_cache = cache
        return ext

    def chart(self, job_id):
        """The page's chart for a trade: from MongoDB once saved, from the local file while recording."""
        job = self.store.get_job(job_id)
        if not job:
            return {"error": f"no job {job_id}"}
        saved = self.store.get_ticks(job_id) if (job.get("recording") or {}).get("status") == "saved" else None
        if saved:
            bars, source = saved.get("bars") or [], "mongo"
        elif self.recorder is not None:
            bars, source = REC.bars(self.recorder.read(job_id)), "recording"
        else:
            bars, source = [], "none"
        out = CH.build(_no_channel(job), bars)
        out["source"] = source
        return out

    # ── GIFT Nifty, information only (btst/gift.py) ────────────────────────
    def _gift_read(self, what):
        if time.time() - self.gift_failed_at < float(self.c("gift_retry_sec", 300)):
            return None
        try:
            return self.gift_fetch(self.now().date())
        except Exception as e:
            self.gift_failed_at = time.time()
            self._log("gift", f"GIFT Nifty {what} not read ({e!r}) - trying again in "
                              f"{float(self.c('gift_retry_sec', 300)) / 60:g} min; nothing depends on it", "WARN")
            return None

    def _gift(self, job):
        """The entry day's close: GIFT Nifty's baseline. gift_check_at (01:00)
        on the next session's date: its move since, for or against the trade."""
        if not self.c("gift_enabled", True):
            return
        now = self.now()
        jid, trade_day = job["job_id"], datetime.fromisoformat(job["trade_date"]).date()
        t = _secs(now)
        if now.date() == trade_day and t >= _hm(self.c("market_close", "15:45")) and not job.get("gift_close"):
            q = self._gift_read("at the close")
            if q:
                self.store.update_job(jid, set_={"gift_close": dict(q, at=self._ts())})
                self._log("gift", f"GIFT Nifty at the close: {q['price']:,.1f} ({q['expiry']} future; day change "
                                  f"{q.get('day_change')}) - the baseline for the {self.c('gift_check_at', '01:00')} "
                                  f"check")
            return
        planned = self._next_session(trade_day)
        if (now.date() != planned or job.get("gift_check") or t < _hm(self.c("gift_check_at", "01:00"))
                or t >= _hm(self.c("market_open", "09:15"))):
            return
        q = self._gift_read("check")
        if not q:
            return
        base = (job.get("gift_close") or {}).get("price")
        move = q["price"] - base if base else q.get("day_change")
        side = (job.get("signal") or {}).get("side", "")
        verdict = G.against(side, move)
        measured = "since the entry day's close" if base else "on the day (NSE IX)"
        rec = dict(q, at=self._ts(), base=base, move=round(move, 2) if move is not None else None,
                   verdict=verdict, measured=measured)
        self.store.update_job(jid, set_={"gift_check": rec})
        self._say(f"GIFT Nifty {q['price']:,.1f}: {move:+.0f} points {measured} - "
                  + ({"against": f"AGAINST our {side}: a weak open for this trade is likely",
                      "with": f"with our {side}", "flat": "flat"}[verdict])
                  + "; the live rules act on the open's price (information only)",
                  jid, True, "gift", "WARN" if verdict == "against" else "INFO")

    def _offhours_price(self, job):
        """The market shut, a trade open: its last traded price from Angel at
        once after a start and every offhours_price_sec (600 s) - the page
        shows the trade's P&L on it. No websocket (nothing trades)."""
        if time.time() - self.last_offhours_price < float(self.c("offhours_price_sec", 600)):
            return
        self.last_offhours_price = time.time()
        jid, c, p = job["job_id"], job["contract"], job["position"]
        rest = getattr(self.prices, "rest_quote", None) if self.prices is not None else None
        try:
            q = rest(c) if rest else None
            ltp = (q or {}).get("ltp") if rest else self.broker.ltp(c)
        except Exception as e:
            ltp = None
            self._log("manage", f"last price for {c['symbol']} unreadable: {e!r}", "WARN")
        if not ltp:
            self._log("manage", f"no last traded price for {c['symbol']} (market closed) - the page keeps "
                                f"{job.get('last_ltp')}", "WARN")
            return
        ltp = float(ltp)
        upd = {"last_ltp": ltp, "last_ltp_at": self._ts(), "last_ltp_source": "last traded price (market closed)",
               "ltp_high": max(float(job.get("ltp_high") or ltp), ltp),
               "ltp_low": min(float(job.get("ltp_low") or ltp), ltp)}
        self.store.update_job(jid, set_=upd)
        job.update(upd)
        pct = 100.0 * (ltp - p["avg_price"]) / p["avg_price"]
        self._log("manage", f"{c['symbol']} last traded {ltp:.2f} (market closed): {pct:+.2f}%, "
                            f"Rs {(ltp - p['avg_price']) * p['qty']:+,.0f} on {p['qty']} @ {p['avg_price']:.2f}")

    def _exit_market(self, job, reason):
        jid, c = job["job_id"], job["contract"]
        q = int(job["position"]["qty"])
        to = job.get("target_order")
        if to and to.get("ok"):
            self._log("exit", f"cancelling target order {to.get('order_id')}")
            st = self.broker.cancel(to)
            self._log("exit", f"target order after the cancel: {st}")
            self.store.add_order(jid, {"kind": "target_cancel", "at": self._ts(), "order_id": to.get("order_id"),
                                       "state": st})
            if st and st["status"] == "complete":
                self._log("exit", "the target filled before the cancel - closing as a target hit", "TRADE")
                self._close(job, "target", st["avg"] or job["target_price"], None)
                return
            if st and st.get("filled"):
                self._log("exit", f"{st['filled']} of the target filled before the cancel", "TRADE")
                q -= int(st["filled"])
        held = self.broker.net_qty(c)
        self._log("exit", f"the broker holds {held} of {c['symbol']}, the job {q}")
        if held is not None and held < q:
            self._say(f"the account holds {held} of {c['symbol']}, the job {q} - selling {max(0, held)}",
                      jid, True, "exit", "WARN")
            q = max(0, held)
        if q <= 0:
            self._close(job, f"{reason} (nothing left at the broker)", job.get("last_ltp") or 0.0, None)
            return
        self._log("order", f"SELL {c['symbol']} x{q} at market ({reason})", "TRADE")
        sell = self.broker.sell_market(c, q)
        sell.update(kind="exit", at=self._ts(), reason=reason)
        self.store.add_order(jid, sell)
        if not sell.get("ok"):
            self._say(f"EXIT SELL {c['symbol']} x{q} not filled ({sell.get('error')}) - retrying; "
                      f"check the Angel app", jid, True, "order", "ERROR")
            return
        self._log("order", f"SELL filled {sell['filled']} @ {sell['avg_price']:.2f} (order {sell.get('order_id')})",
                  "TRADE")
        self._history(jid, "exit", price=float(sell["avg_price"]), qty=int(sell["filled"]),
                      order_id=sell.get("order_id"), why=reason)
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
            "open_pnl_pct_next_day": dec.get("pnl_pct"),
            "averaged": float(job.get("topped_pct") or 0) > 0, "topped_pct": job.get("topped_pct") or 0,
            "topups": len(job.get("topups") or []),
            "trailed": bool((job.get("trail") or {}).get("steps")),
            "trail_steps": len((job.get("trail") or {}).get("steps") or []),
            "target_initial": (job.get("target_history") or [{}])[0].get("price"),
            "target_changes": len(job.get("target_history") or []),
            "amo_placed": sum(1 for a in job.get("amo_history") or [] if a.get("action") == "placed"),
            "entry_at": pos.get("entry_at"),
            "added_qty": pos.get("added_qty", 0), "added_price": pos.get("added_price"),
            "max_ltp": hi, "min_ltp": lo,
            "mfe_pct": round(100.0 * (hi - avg) / avg, 2) if avg else 0.0,
            "mae_pct": round(100.0 * (lo - avg) / avg, 2) if avg else 0.0,
            "held_hours": round((now - entered).total_seconds() / 3600.0, 2),
            "days_to_expiry_at_entry": job.get("days_to_expiry"),
            "mode": job.get("mode"),
        }
        self.store.update_job(jid, set_={"status": "closed", "result": metrics})
        self._log("close", f"metrics {jid}: " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
        self._say(f"CLOSED {job['contract']['symbol']} x{q} @ {exit_px:.2f} ({reason}) - net Rs {net:+,.0f} "
                  f"({metrics['pnl_pct']:+.1f}% of Rs {inv:,.0f}, charges Rs {cost:,.0f})", jid, True,
                  "close", "TRADE")

    # ── mode switches (btst/modes.py) ──────────────────────────────────────
    def park(self, job, why):
        """An open paper trade set aside by a switch to live: its position,
        target and P&L kept as they are now; not managed until a switch back."""
        jid, p = job["job_id"], job["position"]
        ltp = job.get("last_ltp")
        pnl = round((ltp - p["avg_price"]) * p["qty"], 2) if ltp else None
        pct = round(100.0 * (ltp - p["avg_price"]) / p["avg_price"], 2) if ltp else None
        state = {"at": self._ts(), "why": why, "last_ltp": ltp, "last_ltp_at": job.get("last_ltp_at"),
                 "pnl": pnl, "pnl_pct": pct, "qty": p["qty"], "avg_price": p["avg_price"],
                 "target_price": job.get("target_price"), "decision": (job.get("decision") or {}).get("scenario")}
        self.store.update_job(jid, set_={"status": "parked", "parked": state})
        self._say(f"paper trade {job['contract']['symbol']} x{p['qty']} @ {p['avg_price']} parked ({why}) - "
                  f"saved at {ltp} ({pct:+.2f}%, Rs {pnl:+,.0f}); it resumes on a switch back to PAPER"
                  if ltp else f"paper trade {job['contract']['symbol']} parked ({why}); it resumes on a switch "
                              f"back to PAPER", jid, True, "mode", "WARN")
        return state

    def unpark(self, mode):
        """The newest trade parked in `mode`, managed again (the paper broker
        takes its position and target back)."""
        parked = self.store.parked_jobs(mode)
        if not parked:
            return None
        j = parked[0]
        self.store.update_job(j["job_id"], set_={"status": "open", "unparked_at": self._ts()})
        j = self.store.get_job(j["job_id"])
        if hasattr(self.broker, "adopt"):
            self.broker.adopt(j)
        self._say(f"paper trade {j['contract']['symbol']} x{j['position']['qty']} resumed (parked "
                  f"{(j.get('parked') or {}).get('at', '?')})", j["job_id"], True, "mode")
        return j

    def exit_now(self, job, reason):
        """Sell an open trade at market now (a switch away from live). The
        market must be open. True if the trade is closed."""
        if not self._market_open(self.now()):
            return False
        self._exit_market(job, reason)
        j = self.store.get_job(job["job_id"])
        return bool(j and j.get("status") == "closed")

    # ── the loop ────────────────────────────────────────────────────────────
    def resume(self):
        """On start: a job left 'entering' by a crash is not retried."""
        j = self.store.open_job()
        if j and j["status"] == "entering":
            self.store.update_job(j["job_id"], set_={"status": "needs_review",
                                                     "reject_reason": "the service stopped during entry"})
            self._say(f"job {j['job_id']} was being entered when the service stopped - not retried; "
                      f"check the Angel app for {j.get('contract', {}).get('symbol', 'the contract')}",
                      j["job_id"], True, "resume", "ERROR")
        elif j:
            self._say(f"resuming {j['job_id']} ({j['contract']['symbol']} x{j['position']['qty']} "
                      f"@ {j['position']['avg_price']}, target {j.get('target_price')})", area="resume")
            if self.recorder is not None and not j.get("recording"):
                self._start_recording(j["job_id"], j["contract"], j["position"].get("entry_at") or self._ts(),
                                      datetime.fromisoformat(j["trade_date"]).date())
            if hasattr(self.broker, "adopt"):
                self.broker.adopt(j)            # paper: the position and target lived in the old process
        else:
            self._log("resume", "no open trade to resume")

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
                  f"{self.c('scan_end', '15:30')} ({len(read)} messages read, {looked} with the markers) - "
                  f"no trade", area="day")

    def step(self):
        with self.lock:
            self._step()

    def _step(self):
        for name, fn in (("window", self._window_edges), ("message", self._process_messages),
                         ("day", self._record_quiet_day)):
            try:
                fn()
            except Exception as e:
                self._log(name, f"error: {e!r}\n{traceback.format_exc()}", "ERROR")
        try:
            job = self.store.open_job()
            self.managing = bool(job and job["status"] == "open"
                                 and self._market_open(self.now()))
            if job and job["status"] == "open":
                if self.managing:
                    self._manage(job)
                else:
                    self._after_close(job)
                    self._offhours_price(job)
                self.status_note = f"managing {job['job_id']}"
            else:
                self.status_note = (f"waiting for a signal ({self.c('scan_start', '15:15')} - "
                                    f"{self.c('scan_end', '15:30')})")
            self._recording_pass()
            if self.prices is not None:
                if not self.managing:
                    self.prices.release()       # no open trade: the socket keeps only what is recorded
                if self._market_open(self.now()) and (self.managing or self.prices.extra):
                    self.prices.pulse()
                else:
                    self.prices.stop()          # the market shut, or nothing to watch: no socket
        except Exception as e:
            self._log("manage", f"error: {e!r}\n{traceback.format_exc()}", "ERROR")

    def wait_sec(self):
        """Seconds to the next pass: manage_poll_sec (1) while a trade is open in
        market hours, poll_sec (3) otherwise."""
        return float(self.c("manage_poll_sec", 1) if self.managing else self.c("poll_sec", 3))

    def status(self):
        j = self.store.open_job()
        return {"now": self.now().isoformat(timespec="seconds"), "mode": self.broker.mode,
                "market": market_phase(self.now(), start=self.c("market_open", "09:15"),
                                       end=self.c("market_close", "15:45")),
                "note": self.status_note,
                "window": f"{self.c('scan_start', '15:15')}-{self.c('scan_end', '15:30')}",
                "open_job": _no_channel(j), "jobs": [_no_channel(x) for x in self.store.recent_jobs(20)],
                "messages": [{k: v for k, v in m.items() if k != "channel"} for m in self.store.recent_messages(20)],
                "price": self.prices.state() if self.prices is not None else None,
                "health": dict(self.health),
                "parked": [_no_channel(x) for x in self.store.parked_jobs()],
                "next_session": self._next_session(datetime.fromisoformat(j["trade_date"]).date() if j
                                                   else self.now().date()).isoformat(),
                "rules": {k: self.c(k, v) for k, v in RULE_DEFAULTS.items()}}
