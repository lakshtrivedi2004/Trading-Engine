#!/usr/bin/env python3
"""Where do entries die? Replay candles through the decision layer, bar by bar.

For every bar inside the tradable window this runs exactly what the live hunt
runs before a contract is chosen - arb/regime.py:regime_gate, then
arb/confluence.py:evaluate - and records the FIRST reason the bar was refused,
or the setup that fired. The output is a funnel: how many bars reached each
stage, and what killed the rest.

What this is for: finding a gate that refuses everything. What it is NOT for:
judging profitability. It does not choose a contract, price an option, or
simulate a fill, and on synthetic data (the default when no cache exists) there
is no edge to find by construction - a random walk only tells you how often
the rules fire, never whether firing makes money.

    python tools/funnel.py                     # cached real candles if present
    python tools/funnel.py --synthetic --days 5 --seeds 4
    python tools/funnel.py --set midday_mode=block --set min_confirmations=2
"""
import argparse
import glob
import json
import math
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb.config import IntradayLimits
from arb.confluence import evaluate
from arb.instruments import IST
from arb.regime import regime_gate
from arb.smc import RANGE


def synthetic_days(days=5, seed=0, start_px=25000.0, per_min_vol=0.00045):
    """1-minute NIFTY-like bars with trending and choppy stretches, and volume.

    Drift switches regime every 20-90 minutes so there are both trends to
    follow and ranges to sit out. Overnight gaps are included because the
    session-level playbooks depend on them.
    """
    rng = random.Random(seed)
    rows, px = [], start_px
    day0 = datetime(2026, 9, 14, 9, 15, tzinfo=IST)
    d = 0
    while len([1 for _ in range(d)]) < days:
        date = day0 + timedelta(days=d)
        d += 1
        if date.weekday() >= 5:
            days += 1
            continue
        px *= math.exp(rng.gauss(0, 0.004))              # overnight gap
        drift, left = 0.0, 0
        for m in range(375):                             # 09:15 .. 15:29
            if left <= 0:
                drift = rng.choice([0.0, 0.0, 1.0, -1.0]) * per_min_vol * rng.uniform(0.15, 0.5)
                left = rng.randint(20, 90)
            left -= 1
            vol = per_min_vol * (1.6 if m < 30 or m > 345 else 1.0)
            o = px
            c = o * math.exp(drift + rng.gauss(0, vol))
            wick = abs(rng.gauss(0, vol)) * o
            h = max(o, c) + wick * rng.random()
            l = min(o, c) - wick * rng.random()
            v = abs(rng.gauss(1.0, 0.35)) * (2.0 if m < 30 else 1.0) * 1000.0
            ts = (date + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
            rows.append([ts, o, h, l, c, v])
            px = c
    return rows


def cached_series():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".cache", "candles")
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "*_1m.json"))):
        try:
            with open(path) as fh:
                rows = json.load(fh)
            if len(rows) > 100:
                out[os.path.basename(path).split("_")[0]] = rows
        except Exception:
            continue
    return out


def _minutes(iso):
    t = datetime.fromisoformat(iso)
    return t, t.hour * 60 + t.minute


def replay(rows, L, window=400, first_min=9 * 60 + 30, last_min=14 * 60 + 45,
           skip_days=1, spacing=5, evaluate_fn=evaluate, gate_fn=regime_gate):
    """Returns (Counter of outcomes, list of fired (iso, side, playbooks, grade))."""
    outcome, fired = Counter(), []
    day_of = [r[0][:10] for r in rows]
    days = sorted(set(day_of))
    live_days = set(days[skip_days:])
    last_fire = -10**9
    for i in range(len(rows)):
        if day_of[i] not in live_days:
            continue
        t, m = _minutes(rows[i][0])
        if m < first_min or m > last_min:
            continue
        seg = rows[max(0, i - window + 1):i + 1]
        g = gate_fn(seg, L, now=t)
        if g:
            outcome[f"regime:{g}"] += 1
            continue
        v = evaluate_fn(seg, seg[-1][4], L)
        if not v.tradable:
            key = v.veto or (v.reason.split(" (")[0] if v.reason else "refused")
            outcome[f"decision:{key}"] += 1
            continue
        outcome["SIGNAL"] += 1
        if i - last_fire >= spacing:
            last_fire = i
            fired.append((rows[i][0], v.side, [s.name for s in v.setups],
                          getattr(v, "grade", "")))
    return outcome, fired


def _parse_set(pairs):
    out = {}
    for p in pairs or []:
        k, _, val = p.partition("=")
        try:
            out[k] = json.loads(val)
        except ValueError:
            out[k] = val
    return out


def report(title, outcome, fired, sessions):
    total = sum(outcome.values())
    print(f"\n== {title}")
    print(f"   bars evaluated {total:,} over {sessions} sessions")
    for k, n in outcome.most_common(14):
        print(f"   {n:7,}  {100.0 * n / max(total, 1):5.1f}%  {k}")
    per = len(fired) / max(sessions, 1)
    print(f"   distinct entries (>= 5 bars apart): {len(fired)}  = {per:.1f} per session")
    by = Counter(p for _, _, ps, _ in fired for p in ps)
    if by:
        print("   playbooks fired (a signal can carry several): "
              + ", ".join(f"{k} {n}" for k, n in by.most_common()))
    g = Counter(gr for *_, gr in fired if gr)
    if g:
        print("   grades: " + ", ".join(f"{k} {g[k]}" for k in sorted(g)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--set", action="append", help="override a limit, k=v (JSON value)")
    a = ap.parse_args()

    L = IntradayLimits.load()
    for k, v in _parse_set(a.set).items():
        if not hasattr(L, k):
            sys.exit(f"unknown limit {k}")
        setattr(L, k, v)

    series = {} if a.synthetic else cached_series()
    if series:
        for name, rows in series.items():
            out, fired = replay(rows, L)
            report(f"{name} (cached real candles)", out, fired,
                   max(1, len({r[0][:10] for r in rows}) - 1))
        return
    tot, allf = Counter(), []
    for seed in range(a.seeds):
        out, fired = replay(synthetic_days(a.days, seed), L)
        tot.update(out)
        allf.extend(fired)
    report(f"synthetic, {a.seeds} seeds x {a.days} days (frequency only - no edge by construction)",
           tot, allf, a.seeds * (a.days - 1))


if __name__ == "__main__":
    main()
