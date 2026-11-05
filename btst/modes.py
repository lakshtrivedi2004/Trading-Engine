"""Switching the BTST desk between paper and live (owner, 24 Oct 2026), and
setting its principal (owner, 4 Nov 2026).

    LIVE -> PAPER, no trade open    switch.
    LIVE -> PAPER, a live trade     only with exit_trade (the page asks first):
                                    the trade is sold at market now - in market
                                    hours - and closed, then the switch. Without
                                    it, or with the market shut, nothing changes.
    PAPER -> LIVE                   switch at once. An open paper trade is
                                    parked: its position, target and P&L saved,
                                    no longer managed, not counted against live
                                    trades; it resumes on the next switch back
                                    to PAPER.
The mode is saved (.btst_mode) so a restart keeps it. Every switch is logged
and sent to Telegram.

The principal (config/btst.json "principal") is set from the page too: each
new trade invests trade_pct (10%) of it, from the next signal. An open trade
keeps its size - its top-ups add up to its own first amount. Saved
(.btst_principal, over config/btst.json) so a restart keeps it; logged and
sent to Telegram.
"""
import math

from btst.logbook import fmt


def switch(strategy, to, exit_trade=False, make_broker=None, save_mode=None, notify=None, log=print):
    if to not in ("paper", "live"):
        return {"ok": False, "error": "to must be paper or live"}
    with strategy.lock:                         # never while step() is managing the same trade
        cur = strategy.broker.mode
        if to == cur:
            return {"ok": True, "mode": to, "note": f"already {to.upper()}"}
        job = strategy.store.open_job()
        notes = []
        if job and job.get("mode") == "live":
            sym = job["contract"]["symbol"]
            if not exit_trade:
                log(fmt("WARN", "mode", f"switch to {to.upper()} refused - live trade {job['job_id']} is open"))
                return {"ok": False, "need_exit": True, "job_id": job["job_id"], "symbol": sym,
                        "qty": job["position"]["qty"],
                        "error": f"a LIVE trade is open ({sym} x{job['position']['qty']}) - sell it at market "
                                 f"first, or stay LIVE"}
            if not strategy._market_open(strategy.now()):
                log(fmt("WARN", "mode", f"switch to {to.upper()} refused - the market is shut, {sym} can't be sold now"))
                return {"ok": False, "error": f"the market is closed - the live position {sym} can only be sold in "
                                              f"market hours; still LIVE"}
            log(fmt("TRADE", "mode", f"switch to {to.upper()}: selling the live trade {sym} at market first"))
            if not strategy.exit_now(job, "mode_switch_exit"):
                return {"ok": False, "error": f"the market sell of {sym} did not complete - still LIVE; "
                                              f"see the log and the Angel app"}
            notes.append(f"the live trade {sym} was sold at market")
        elif job:                               # an open paper trade: saved, set aside
            strategy.park(job, f"switched to {to.upper()}")
            notes.append(f"the paper trade {job['contract']['symbol']} is parked (saved; it resumes on PAPER)")
        if save_mode:
            save_mode(to)
        if make_broker:
            strategy.broker = make_broker(to)
        if getattr(strategy, "prices", None) is not None:
            strategy.prices.stop()
        if to == "paper":
            j = strategy.unpark("paper")
            if j:
                notes.append(f"the parked paper trade {j['contract']['symbol']} resumed")
        log(fmt("WARN" if to == "live" else "INFO", "mode",
                f"mode switched {cur.upper()} -> {to.upper()}" + (f" ({'; '.join(notes)})" if notes else "")))
        if notify:
            try:
                notify(f"🌙 <b>BTST</b> mode is now <b>{to.upper()}</b>" + (f"\n{'; '.join(notes)}" if notes else ""))
            except Exception:
                pass
        return {"ok": True, "mode": to, "note": "; ".join(notes)}


PRINCIPAL_MIN, PRINCIPAL_MAX = 1000.0, 10_00_00_000.0      # Rs 1,000 - Rs 10 crore: a typo guard


def set_principal(strategy, amount, save=None, notify=None, log=print):
    try:
        v = float(amount)
    except (TypeError, ValueError):
        v = float("nan")
    if not math.isfinite(v) or not PRINCIPAL_MIN <= v <= PRINCIPAL_MAX:
        return {"ok": False, "error": f"the principal must be a number of rupees, Rs {PRINCIPAL_MIN:,.0f} - "
                                      f"Rs {PRINCIPAL_MAX:,.0f} (got {amount!r})"}
    v = float(round(v))
    with strategy.lock:                         # never in the middle of an entry
        was = float(strategy.c("principal", 0) or 0)
        if save:
            save(v)
        strategy.cfg["principal"] = v
        per = strategy.trade_amount()
        cap = per * (1 + float(strategy.c("amount_tolerance_pct", 5.0)) / 100.0)
        job = strategy.store.open_job()
        note = (f"the open trade {job['contract']['symbol']} keeps its size; its top-ups add up to its own "
                f"first amount" if job and job.get("position") else "")
        msg = (f"principal Rs {was:,.0f} -> Rs {v:,.0f}: each new trade Rs {per:,.0f} "
               f"({float(strategy.c('trade_pct', 10.0)):g}%), the most lots below Rs {cap:,.0f}"
               + (f" ({note})" if note else ""))
        log(fmt("WARN" if strategy.broker.mode == "live" else "INFO", "capital", msg))
        if notify:
            try:
                notify(f"🌙 <b>BTST</b> principal is now <b>Rs {v:,.0f}</b> ({strategy.broker.mode.upper()}): "
                       f"each new trade Rs {per:,.0f}" + (f"\n{note}" if note else ""))
            except Exception:
                pass
        return {"ok": True, "principal": v, "was": was, "trade_amount": per, "cap": round(cap, 2), "note": note}
