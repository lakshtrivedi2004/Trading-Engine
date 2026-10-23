#!/usr/bin/env python3
"""Pace gate 0.5-1.5 on every index, and trading only with the trend (owner, 21 Oct 2026).

    HISTORY_DIR=.cache/history_live nice -n 19 venv/bin/python research/pace_grid_test.py

.cache/history_live = the year of Angel 1-minute bars (to 13 Oct) joined with
the engine's own live-recorded bars (14-21 Oct), so the last month is in.
Entries are decided on completed 1-minute bars, as live (candle_interval 1m,
decide_on_closed_bars).

Pace (ATR ratio: the last 20 bars' ATR over its 200-bar median) at one value
0.5 ... 1.5 for both halves of the day, against the live per-index table
(config atr_by_index). Trend filters on top of the live 4-hour trend gate:
  4h        live: the last 5 four-hour bars moved the trade's way
  4h+day    and the index is on the trade's side of the previous close
  4h+open   and on the trade's side of today's open
Book at Rs 11,000, live risk cap, side cooldown 30, 15% halt; columns: the
year's halves (split 25 May 2026) and the last month (22 Sep - 21 Oct 2026).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features, gate_halves                 # noqa: E402
from playbook_lab import INDICES, candidates             # noqa: E402
from fixes_test import priced                            # noqa: E402
from exit_sweep import stats                             # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

CAPITAL = 11000.0
MONTH = "2026-09-22"
VALUES = [round(0.5 + 0.1 * k, 1) for k in range(11)]


def main():
    L = IntradayLimits.load()
    rz.L = L
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    days = sorted({r[0][:10] for ix in IX.values() for r in ix.rows})
    prev = {}
    for n, ix in IX.items():
        close, last = {}, None
        for r in ix.rows:
            close[r[0][:10]] = r[4]
        ds = sorted(close)
        prev[n] = {d: close[p] for p, d in zip(ds, ds[1:])}
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    P = []
    for t in priced(C, IX, CAPITAL, True):
        if F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] <= 0:
            continue
        pc = prev[t["u"]].get(t["day"])
        t["with_prev"] = pc is not None and (IX[t["u"]].rows[t["i"]][4] - pc) * t["side"] > 0
        t["with_open"] = t.get("withday", 0) > 0
        P.append(t)
    halt = L.max_daily_loss_pct * CAPITAL / 10000.0
    live = dict(L.atr_by_index)
    print(f"{days[0]} - {days[-1]}, {len(days)} days; capital Rs {CAPITAL:,.0f}, "
          f"risk {L.max_risk_pct_per_trade:g}%; live pace {live}\n", flush=True)

    def row(label, ts):
        tr = bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=30,
                    halt_pct=halt)
        s = stats(tr, days)
        mo = [t["net2"] for t in tr if t["day"] >= MONTH]
        mw = sum(x > 0 for x in mo)
        return (f"| {label} | {s['n']} | {s['win']} | {s['rr']} | {s['year']:+,} | {s['ins']:+,} | "
                f"{s['out']:+,} | {len(mo)} ({mw} won) | {sum(mo):+,.0f} | {s['per_month']:+,} | "
                f"{s['dd']:,} | {100 * s['trade_days'] / s['days']:.0f}% |")
    head = ("| {} | Trades | Win % | RR | Year Rs | Before 25 May | After 25 May | Last month trades | "
            "Last month Rs | Per month | Max DD | Days with a trade |")
    sep = "|---" * 12 + "|"

    for tname, tf in (("4h (live)", lambda t: True), ("4h+day", lambda t: t["with_prev"]),
                      ("4h+open", lambda t: t["with_open"])):
        print(f"\n### All three indices, trend filter: {tname}\n\n" + head.format("Pace") + "\n" + sep)
        print(row("live per-index", [t for t in P if tf(t) and gate_halves(t, *live[t["u"]])]),
              flush=True)
        for v in VALUES:
            print(row(f"{v:g}", [t for t in P if tf(t) and gate_halves(t, v, v)]), flush=True)

    print("\n### Live pace, trend 4h: which indices the retest playbook trades\n\n"
          + head.format("Indices") + "\n" + sep)
    for label, keep in (("all three (live)", set(INDICES)), ("BANKNIFTY + SENSEX (NIFTY off)",
                                                             {"BANKNIFTY", "SENSEX"}),
                        ("SENSEX only", {"SENSEX"})):
        print(row(label, [t for t in P if t["u"] in keep and gate_halves(t, *live[t["u"]])]),
              flush=True)

    for n in INDICES:
        print(f"\n### {n} alone, trend 4h (live)\n\n" + head.format("Pace") + "\n" + sep)
        Q = [t for t in P if t["u"] == n]
        print(row(f"live {live[n]}", [t for t in Q if gate_halves(t, *live[n])]), flush=True)
        for v in VALUES:
            print(row(f"{v:g}", [t for t in Q if gate_halves(t, v, v)]), flush=True)


if __name__ == "__main__":
    main()
