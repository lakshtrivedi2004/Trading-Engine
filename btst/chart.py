"""A BTST's chart (owner, 26 Oct 2026): the option's 1-minute price from the buy
to the next session's close, the target, the average price and the position at
each minute, the P&L, and every event - the buy, each top-up, each target
change and trailing step, the AMO, the exit - as JSON for the page and for
MongoDB (btst_ticks.events).

P&L at a minute: (price - average) x quantity while the trade is open; after
the exit the realised net P&L stays, and `hold_pnl` shows what holding on would
have made - the line to read when deciding a higher or lower target.
"""
from datetime import datetime

from arb.instruments import IST


def _ms(iso):
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def events(job):
    """The trade's events, oldest first: [{t, kind, price, qty, label}]."""
    ev = []
    p = job.get("position") or {}
    if p.get("entry_at"):
        ev.append({"t": _ms(p["entry_at"]), "kind": "buy", "price": p.get("entry_price"), "qty": p.get("entry_qty"),
                   "label": f"buy {p.get('entry_qty')} @ {p.get('entry_price')}"})
    for u in job.get("topups") or []:
        ev.append({"t": _ms(u.get("at")), "kind": "topup", "price": u.get("price"), "qty": u.get("qty"),
                   "label": f"top-up {u.get('pct'):g}% at {-float(u.get('loss_pct') or 0):.1f}%: "
                            f"+{u.get('qty')} @ {u.get('price')}"})
    for h in job.get("target_history") or []:
        kind = "trail" if str(h.get("kind", "")).startswith("trail") else (
            "amo" if h.get("kind") == "amo_target" else "target")
        ev.append({"t": _ms(h.get("at")), "kind": kind, "price": h.get("price"), "qty": h.get("qty"),
                   "label": f"{h.get('kind')} {h.get('price')}" + (f" (from {h.get('previous')})"
                                                                   if h.get("previous") else "")})
    r = job.get("result") or {}
    if r.get("closed_at"):
        ev.append({"t": _ms(r["closed_at"]), "kind": "exit", "price": r.get("exit_price"), "qty": r.get("qty"),
                   "label": f"exit {r.get('exit_reason')} @ {r.get('exit_price')} - net {r.get('net_pnl')}",
                   "pnl": r.get("net_pnl")})
    return sorted((e for e in ev if e["t"]), key=lambda e: e["t"])


def _positions(job):
    """[(epoch ms, qty, average)]: the position after the buy and after each top-up."""
    p = job.get("position") or {}
    qty, avg = int(p.get("entry_qty") or p.get("qty") or 0), float(p.get("entry_price") or p.get("avg_price") or 0)
    steps = [(_ms(p.get("entry_at")) or 0, qty, avg)]
    for u in sorted(job.get("topups") or [], key=lambda u: str(u.get("at"))):
        q2, p2 = int(u.get("qty") or 0), float(u.get("price") or 0)
        if q2:
            avg = (avg * qty + p2 * q2) / (qty + q2)
            qty += q2
            steps.append((_ms(u.get("at")) or 0, qty, avg))
    return steps


def extremes(job, bars):
    """The best and the worst the position could have shown over the recorded
    window - the buy to the next session's close, past the exit too: P&L at each
    minute's high and low, with the position held then (top-ups count; after
    the exit, the position as it was at the exit)."""
    steps = _positions(job)
    r = job.get("result") or {}
    exit_t = _ms(r.get("closed_at")) if r else None
    best = worst = hi = lo = None
    i = 0
    for b in bars:
        t = b["t"] + 59_999
        if t < steps[0][0]:
            continue
        while i + 1 < len(steps) and steps[i + 1][0] <= t:
            i += 1
        _, q, a = steps[i]
        if not q or not a:
            continue
        if hi is None or b["h"] > hi[0]:
            hi = (b["h"], b["t"])
        if lo is None or b["l"] < lo[0]:
            lo = (b["l"], b["t"])
        up, down = (b["h"] - a) * q, (b["l"] - a) * q
        after = bool(exit_t and b["t"] >= exit_t)
        if best is None or up > best[0]:
            best = (up, b["h"], b["t"], after, round(100.0 * (b["h"] - a) / a, 2))
        if worst is None or down < worst[0]:
            worst = (down, b["l"], b["t"], after, round(100.0 * (b["l"] - a) / a, 2))
    if best is None:
        return None
    iso = lambda ms: datetime.fromtimestamp(ms / 1000, tz=IST).isoformat(timespec="minutes")   # IST, whatever the server's zone
    return {"max_profit": round(best[0], 2), "max_profit_price": best[1], "max_profit_at": iso(best[2]),
            "max_profit_after_exit": best[3], "max_profit_pct": best[4],
            "max_loss": round(worst[0], 2), "max_loss_price": worst[1], "max_loss_at": iso(worst[2]),
            "max_loss_after_exit": worst[3], "max_loss_pct": worst[4], "minutes": len(bars),
            "high": hi[0], "high_at": iso(hi[1]), "low": lo[0], "low_at": iso(lo[1]),
            "candle_minutes": sum(1 for b in bars if b.get("src") == "candle"),
            "basis": "recorded prices, buy to the next session's close"}


def build(job, bars):
    """{job_id, symbol, bars: [{t, o, h, l, c, src, target, avg, qty, pnl, hold_pnl, open}], events, ...}."""
    ev = events(job)
    r = job.get("result") or {}
    exit_t = _ms(r.get("closed_at")) if r else None
    pos_steps = _positions(job)
    tgt_steps = [(e["t"], float(e["price"])) for e in ev if e["kind"] in ("target", "trail", "amo") and e.get("price")]
    out = []
    pi = ti = 0
    tgt = tgt_steps[0][1] if tgt_steps else None
    for b in bars:
        t = b["t"] + 59_999                     # the minute's end
        while pi + 1 < len(pos_steps) and pos_steps[pi + 1][0] <= t:
            pi += 1
        while ti < len(tgt_steps) and tgt_steps[ti][0] <= t:
            tgt = tgt_steps[ti][1]
            ti += 1
        _, q, a = pos_steps[pi]
        is_open = (not exit_t or b["t"] < exit_t) and (pos_steps[0][0] <= t)
        hold = round((b["c"] - a) * q, 2) if q and a else None
        pnl = hold if is_open else (float(r.get("net_pnl")) if r.get("net_pnl") is not None else hold)
        out.append(dict(b, target=tgt if is_open else None, avg=round(a, 2), qty=q, pnl=pnl, hold_pnl=hold,
                        open=is_open))
    c = job.get("contract") or {}
    return {"job_id": job.get("job_id"), "symbol": c.get("symbol"), "side": (job.get("signal") or {}).get("side"),
            "mode": job.get("mode"), "status": job.get("status"), "bars": out, "events": ev,
            "extremes": extremes(job, bars),
            "recording": job.get("recording"), "exit": {"t": exit_t, "price": r.get("exit_price"),
                                                       "reason": r.get("exit_reason"), "net_pnl": r.get("net_pnl")}
            if exit_t else None}
