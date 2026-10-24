"""Statistics over every BTST job in MongoDB, per mode (paper and live apart)."""


def summary(jobs, mode=None):
    """Totals for the closed trades of one mode (or all)."""
    closed = [j for j in jobs if j.get("status") == "closed" and j.get("result")
              and (mode is None or j.get("mode") == mode)]
    closed.sort(key=lambda j: j.get("trade_date", ""))
    nets = [float(j["result"].get("net_pnl") or 0) for j in closed]
    pcts = [float(j["result"].get("pnl_pct") or 0) for j in closed]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    n = len(closed)
    count = lambda f: sum(1 for j in closed if f(j["result"]))
    return {
        "mode": mode or "all", "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1) if n else 0.0,
        "net_pnl": round(sum(nets), 2), "invested_total": round(sum(float(j["result"].get("invested") or 0)
                                                                   for j in closed), 2),
        "avg_pnl": round(sum(nets) / n, 2) if n else 0.0,
        "avg_pct": round(sum(pcts) / n, 2) if n else 0.0,
        "best": round(max(nets), 2) if nets else 0.0, "worst": round(min(nets), 2) if nets else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if sum(losses) < 0 else None,
        "max_drawdown": round(dd, 2),
        "target_hits": count(lambda r: r.get("target_hit")),
        "time_exits": count(lambda r: str(r.get("exit_reason", "")).startswith(("time_exit", "overdue_exit"))),
        "averaged": count(lambda r: r.get("averaged")),
        "trailed": count(lambda r: r.get("trailed")),
        "charges": round(sum(float(j["result"].get("charges") or 0) for j in closed), 2),
        "open_or_failed": sum(1 for j in jobs if (mode is None or j.get("mode") == mode)
                              and j.get("status") not in ("closed",)),
    }


def _g(x):
    try:
        return format(float(x), "g")
    except (TypeError, ValueError):
        return x if x is not None else ""


def by_day(jobs, quiet_days=()):
    """One row per day, newest first: the signal, the trade and its result -
    and the days whose scan window passed with no signal (no trade)."""
    rows = []
    traded = {j.get("trade_date") for j in jobs}
    for d in quiet_days:
        if d.get("date") in traded:                 # a day with a trade (or a signal) is not a no-signal day
            continue
        rows.append({"date": d.get("date"), "job_id": "", "mode": "", "status": "no signal - no trade",
                     "signal": "", "messages_read": d.get("messages_read", 0),
                     "reason": f"{d.get('messages_read', 0)} messages read in the scan window, none a signal"})
    for k, j in enumerate(sorted(jobs, key=lambda j: (j.get("trade_date", ""), j.get("created_at", "")),
                                 reverse=True)):
        r, s, c, p = j.get("result") or {}, j.get("signal") or {}, j.get("contract") or {}, j.get("position") or {}
        to, tr, dec = j.get("target_order") or {}, j.get("trail") or {}, j.get("decision") or {}
        now_px = r.get("exit_price") if r else j.get("last_ltp")
        avg = p.get("avg_price")
        pnl = r.get("net_pnl") if r else ((now_px - avg) * p["qty"] if now_px and avg and p.get("qty") else None)
        pct = r.get("pnl_pct") if r else (round(100.0 * (now_px - avg) / avg, 2) if now_px and avg else None)
        row = {
            "date": j.get("trade_date"), "job_id": j.get("job_id"), "mode": j.get("mode"),
            "status": j.get("status"), "signal": f"{s.get('indices', '')} {_g(s.get('strike_price'))} "
                                                  f"{s.get('side', '')} @ {_g(s.get('price'))}".strip(),
            "signal_at": (j.get("message") or {}).get("date"), "parsed_by": s.get("parsed_by"),
            "symbol": c.get("symbol"), "exchange": c.get("exchange"), "expiry": c.get("expiry"),
            "qty": p.get("qty"), "entry_price": p.get("entry_price"), "entry_qty": p.get("entry_qty"),
            "buy_at": p.get("entry_at"), "avg_price": avg, "invested": p.get("invested"),
            "added_qty": p.get("added_qty"), "added_price": p.get("added_price"), "added_at": p.get("added_at"),
            "now_price": now_px, "now_at": r.get("closed_at") or j.get("last_ltp_at"), "live_pnl": pnl,
            "now_source": None if r else j.get("last_ltp_source"),
            "live_pct": pct, "high": j.get("ltp_high"), "low": j.get("ltp_low"),
            "target": j.get("target_price"), "target_kind": to.get("kind"), "target_variety": to.get("variety"),
            "target_status": j.get("target_last_status"), "watched": bool(j.get("soft_target")),
            "trail": ({"active": tr.get("active"), "initial": tr.get("initial"), "floor": tr.get("floor"),
                       "ref": tr.get("ref"), "low": tr.get("low"), "steps": len(tr.get("steps") or []),
                       "stop_reason": tr.get("stop_reason")} if tr else None),
            "amo": j.get("amo"), "scenario": r.get("scenario") or dec.get("scenario"),
            "opened_pct": dec.get("pnl_pct"), "exit_reason": r.get("exit_reason"), "exit_price": r.get("exit_price"),
            "exit_at": r.get("closed_at"), "net_pnl": r.get("net_pnl"), "pnl_pct": r.get("pnl_pct"),
            "charges": r.get("charges"), "held_hours": r.get("held_hours"), "mfe_pct": r.get("mfe_pct"),
            "mae_pct": r.get("mae_pct"), "reason": j.get("reject_reason", ""),
        }
        if k < 30:                                  # the newest trades carry their whole story
            row["timeline"] = {"events": j.get("events") or [], "targets": j.get("target_history") or [],
                               "amo": j.get("amo_history") or [],
                               "orders": [{x: o.get(x) for x in ("kind", "side", "qty", "price_sent", "avg_price",
                                                                  "filled", "status", "order_id", "variety", "at",
                                                                  "error", "duration")}
                                          for o in j.get("orders") or []]}
        rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows
