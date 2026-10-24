"""Switching the BTST desk between paper and live (owner, 24 Oct 2026).

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
"""
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
