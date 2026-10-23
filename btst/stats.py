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
        "time_exits": count(lambda r: str(r.get("exit_reason", "")).startswith("time_exit")),
        "averaged": count(lambda r: r.get("averaged")),
        "charges": round(sum(float(j["result"].get("charges") or 0) for j in closed), 2),
        "open_or_failed": sum(1 for j in jobs if (mode is None or j.get("mode") == mode)
                              and j.get("status") not in ("closed",)),
    }


def by_day(jobs, quiet_days=()):
    """One row per day, newest first: the signal, the trade and its result -
    and the days whose scan window passed with no signal (no trade)."""
    rows = []
    for d in quiet_days:
        rows.append({"date": d.get("date"), "job_id": "", "mode": "", "status": "no signal - no trade",
                     "signal": "", "reason": f"{d.get('messages_read', 0)} messages read in the scan window, none a signal"})
    for j in sorted(jobs, key=lambda j: (j.get("trade_date", ""), j.get("created_at", "")), reverse=True):
        r, s, c, p = j.get("result") or {}, j.get("signal") or {}, j.get("contract") or {}, j.get("position") or {}
        rows.append({
            "date": j.get("trade_date"), "job_id": j.get("job_id"), "mode": j.get("mode"),
            "status": j.get("status"), "signal": f"{s.get('indices', '')} {s.get('strike_price', '')} "
                                                  f"{s.get('side', '')} @ {s.get('price', '')}".strip(),
            "symbol": c.get("symbol"), "qty": p.get("qty"), "avg_price": p.get("avg_price"),
            "invested": p.get("invested"), "target": j.get("target_price"),
            "scenario": r.get("scenario") or (j.get("decision") or {}).get("scenario"),
            "exit_reason": r.get("exit_reason"), "exit_price": r.get("exit_price"),
            "net_pnl": r.get("net_pnl"), "pnl_pct": r.get("pnl_pct"), "held_hours": r.get("held_hours"),
            "reason": j.get("reject_reason", ""),
        })
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    return rows
