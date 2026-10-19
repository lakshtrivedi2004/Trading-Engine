#!/usr/bin/env python3
"""Pace gate, same-day expiry and stop placement - the 19 Oct 2026 missed trade.

    nice -n 19 venv/bin/python research/pace_stop_test.py [capital]

On 19 Oct NIFTY fell all day in a steady staircase; the owner bought the
break of the 22,898 low and its one-candle retest (23000 PE, +Rs 1,258). The
engine refused every setup: pace 0.87-0.91x against the 1.5x gate. The
owner's idea: trade on pace RISING, not on pace reaching a fixed 1.2/1.3/1.5.

Pace gates (the same candidate trades, only the filter changes):
  live        per-index half-day ATR-ratio thresholds (config atr_by_index)
  none        no pace gate (4-hour trend gate kept, as in every row)
  accel F     ATR ratio rising over the last 10 bars (now > 5 bars ago > 10
              bars ago) and at least F - the owner's "speed increasing"
  live|accel  either
  live|break  live, or the index made a new session high (long) / low (short)
              within the last 15 bars - a trend day's staircase
  live|steady live, or the last 15 bars moved the trade's way with Kaufman
              efficiency >= E (a steady, one-way tape that is slow by ATR)
  all three   live | accel 0.8 | break
Expiry: "next" (live: next week's expiry on expiry day) or "0DTE" (same day).
Stops (each needs its own candidate run):
  zone        live: the setup's zone edge - 0.10 ATR, else the last swing
  nearest     stop_nearest_swing: the nearer of zone edge and last swing
  pad 0 / 0.25, min_stop_atr 0.5, swing_strength 1

Real 1-minute index bars for a year (22 Sep 2025 - 30 Sep 2026); options
priced by research/lab/realistic.py, whose moves research/live_calibration.py
matched against the live recorded chains (slope 0.97-0.995). Fixed capital,
live risk cap, 15% daily halt, side cooldown 30, one position at a time.
A change counts only if better in BOTH halves (split 25 May 2026).
Results: research/pace_stop_result.jsonl (resumable) and the table printed.
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features, gate_halves                 # noqa: E402
from playbook_lab import INDICES, candidates             # noqa: E402
from exit_sweep import stats                             # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402
from arb.regime import atr_ratio, efficiency             # noqa: E402

CAPITAL = float(sys.argv[1]) if len(sys.argv) > 1 else 11000.0
OUT = os.path.join(ROOT, "research", "pace_stop_result.jsonl")
STOPS = [("zone (live)", {}), ("nearest swing", {"stop_nearest_swing": True}),
         ("zone pad 0", {"zone_stop_pad_atr": 0.0}), ("zone pad 0.25", {"zone_stop_pad_atr": 0.25}),
         ("min_stop_atr 0.5", {"min_stop_atr": 0.5}), ("swing_strength 1", {"swing_strength": 1})]


def features(t, IX, L):
    rows, i, side = IX[t["u"]].rows, t["i"], t["side"]

    def ar(k):
        j = i - k
        return atr_ratio(rows[max(0, j - 400):j + 1], period=L.atr_period,
                         baseline_bars=L.atr_baseline_bars,
                         no_gap=bool(getattr(L, "pace_no_gap", False)))
    t["ar0"], t["ar5"], t["ar10"] = ar(0), ar(5), ar(10)
    day = t["day"]
    k0 = i
    while k0 > 0 and rows[k0 - 1][0][:10] == day:
        k0 -= 1
    prior, recent = rows[k0:i - 14], rows[max(k0, i - 14):i + 1]
    t["brk"] = False
    if len(prior) >= 15:
        if side > 0:
            t["brk"] = max(r[2] for r in recent) > max(r[2] for r in prior)
        else:
            t["brk"] = min(r[3] for r in recent) < min(r[3] for r in prior)
    c = [r[4] for r in rows[i - 15:i + 1]]
    t["eff"] = efficiency(c) if (c[-1] - c[0]) * side > 0 else 0.0
    return t


def gates(L):
    halves = dict(L.atr_by_index or {})

    def live(t):
        a, b = halves.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio))
        return gate_halves(t, a, b)

    def accel(f):
        return lambda t: t["ar0"] >= f and t["ar0"] > t["ar5"] > t["ar10"]
    return [
        ("live", live),
        ("none", lambda t: True),
        ("accel 0.8", accel(0.8)),
        ("accel 1.0", accel(1.0)),
        ("live|accel 0.8", lambda t: live(t) or accel(0.8)(t)),
        ("live|break", lambda t: live(t) or t["brk"]),
        ("live|steady 0.4", lambda t: live(t) or t["eff"] >= 0.4),
        ("live|steady 0.6", lambda t: live(t) or t["eff"] >= 0.6),
        ("live|accel 0.8|break", lambda t: live(t) or accel(0.8)(t) or t["brk"]),
        ("break only", lambda t: t["brk"]),
    ]


def main():
    L = IntradayLimits.load()
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    days = sorted({r[0][:10] for ix in IX.values() for r in ix.rows})
    n4 = int(L.trend_4h_bars or 0)
    halt = L.max_daily_loss_pct * CAPITAL / 10000.0
    done = set()
    if os.path.exists(OUT):
        for ln in open(OUT):
            try:
                done.add(json.loads(ln)["label"])
            except ValueError:
                pass
    print(f"capital Rs {CAPITAL:,.0f}, risk {L.max_risk_pct_per_trade:g}%, {len(days)} days", flush=True)
    for slabel, sover in STOPS:
        labels = [f"{slabel} | {g} | {e}" for g, _ in gates(L) for e in ("next", "0DTE")]
        if all(lb in done for lb in labels):
            continue
        Lv = copy.copy(L)
        for k, v in sover.items():
            setattr(Lv, k, v)
        C = candidates(dict(sover, use_regime_gate=False, playbooks=list(L.playbooks)), 6)
        rz.L = Lv
        for exp in ("next", "0DTE"):
            P = []
            for t in C:
                r = rz.reprice(t, IX[t["u"]], CAPITAL, next_expiry_on_expiry_day=(exp == "next"),
                               costs_inside=True)
                if r is not None:
                    u = dict(t)
                    u.update(r)
                    if not n4 or F.h4_ret(u, n4) * u["side"] > 0:
                        P.append(features(u, IX, Lv))
            for gname, g in gates(Lv):
                label = f"{slabel} | {gname} | {exp}"
                if label in done:
                    continue
                ts = sorted((t for t in P if g(t)), key=lambda t: (t["day"], t["m"], t["u"]))
                tr = bt.run(ts, side_cooldown=30, halt_pct=halt)
                s = stats(tr, days)
                s.pop("trades")
                r = dict(label=label, **s)
                with open(OUT, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                done.add(label)
                print(f"{label:52s} n={s['n']:3d} win={s['win']:4.1f}% RR {s['rr']:4.2f} "
                      f"year Rs{s['year']:+8,} (IN {s['ins']:+7,} / OUT {s['out']:+7,}) "
                      f"/day {s['per_day']:+4,} maxDD {s['dd']:6,} trade-days "
                      f"{100 * s['trade_days'] / s['days']:4.1f}%", flush=True)
        rz.L = L


if __name__ == "__main__":
    main()
