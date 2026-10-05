#!/usr/bin/env python3
"""Should the ATR-ratio pace thresholds be RE-CALIBRATED from recent real market data? (owner, 5 Oct 2026)

Live today (config/intraday.json atr_by_index): NIFTY 1.3 before 12:15 / 1.5 after,
BANKNIFTY 1.5 / 1.5, SENSEX 1.2 / 1.2. The ratio itself (ATR20 / median TR of the
200 bars before) is already computed from the live feed every bar; the question is
whether the THRESHOLDS should be re-fitted every week / month on the most recent
real 1-minute index data instead of staying fixed.

Same candidates and pricing as research/pace_test.py and research/pace_daywise_test.py:
playbook_lab.candidates with the pace gate off (each candidate carries its own ar),
fixes_test.priced(C, IX, capital, True), the live 4h trend (5 bars), then the live
portfolio sequencing fixes_test.run(..., side_cooldown=30) (one position at a time
across indices, live daily halt, F4 30 min).

Walk-forward. Grid per index per half {0 (off), 1.0 .. 1.6, 1.8}. At each rebalance
date (weekly = first trading day of each ISO week, monthly = first trading day of each
month), using ONLY days strictly before it and inside a trailing window of W trading
days (W = 40, 60, 120, all past), choose per index and half the threshold with the
highest training P&L. Training P&L = the same run() sequencing (side_cooldown=30,
daily halt) on that index's candidates of that half only. run() keeps no state across
days, so it is computed once per (index, half, threshold) and summed by day: exact,
not an approximation. A threshold is eligible only if it gives >= 5 training trades;
if none is eligible the live value is kept. Ties go to the threshold closest to the
live value (rule R1; rule R2, also shown: the window needs >= 5 gate-off
trades, then any threshold is eligible and one that takes no trade scores 0). 'plateau' = argmax of the mean of the threshold and its grid neighbours.
The chosen values trade the next period only; all out-of-sample days are joined and
the portfolio is run ONCE, and compared with the FIXED live thresholds on exactly the
same days. A walk-forward needs W days of history before it can trade (all-past: 40).
Also every variant is shown on one COMMON window (after the first 120 trading days)
so variants can be compared with each other.

    nice -n 10 venv/bin/python research/pace_walkforward.py > research/pace_walkforward_result.txt
    (--regen rebuilds research_out/pace_wf_P.pkl, the priced candidate cache)
"""
import hashlib
import os
import pickle
import sys
from collections import defaultdict
from datetime import date

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
from common import HALF, SPLIT, gate_halves, live_halt_pct  # noqa: E402
from fixes_test import line                             # noqa: E402
from arb.config import IntradayLimits                   # noqa: E402

CACHE = os.path.join(ROOT, "research_out", "pace_wf_P.pkl")
CFG = os.path.join(ROOT, "config", "intraday.json")
GRID = [0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8]
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
LIVE = {"NIFTY": (1.3, 1.5), "BANKNIFTY": (1.5, 1.5), "SENSEX": (1.2, 1.2)}
MIN_TRAIN = 5
COOL = 30


def cfg_hash():
    return hashlib.sha1(open(CFG, "rb").read()).hexdigest()[:12]


def build():
    import realistic as rz
    from common import Features
    from playbook_lab import candidates
    from fixes_test import priced
    L = IntradayLimits.load(); rz.L = L
    capital = float(L.capital_ceiling or L.starting_capital)
    h = cfg_hash()
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 3)
    P = [t for t in priced(C, IX, capital, True) if F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0]
    days = sorted({r[0][:10] for u in INDICES for r in IX[u].rows})
    if cfg_hash() != h:
        print("WARNING: config/intraday.json changed while candidates were generated", file=sys.stderr)
    D = dict(P=P, days=days, cfg=h, raw=len(C), capital=capital, playbooks=list(L.playbooks),
             atr_by_index={u: tuple(v) for u, v in dict(L.atr_by_index).items()},
             halt=live_halt_pct(), trend=int(L.trend_4h_bars or 0))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(D, f)
    return D


def load():
    if "--regen" in sys.argv or not os.path.exists(CACHE):
        return build()
    with open(CACHE, "rb") as f:
        D = pickle.load(f)
    if D["cfg"] != cfg_hash():
        print(f"note: config/intraday.json has changed since the cache ({D['cfg']}) was built; "
              f"using the cache (rerun with --regen to rebuild)\n")
    return D


def main():
    D = load()
    P, DAYS, HALT = D["P"], D["days"], D["halt"]
    di = {d: k for k, d in enumerate(DAYS)}

    def run(ts):  # == fixes_test.run(ts, side_cooldown=30), halt frozen at cache time
        return bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=COOL, halt_pct=HALT)

    def half(t):
        return 0 if t["m"] < HALF else 1

    def passes(t, x):
        return gate_halves(t, x, x)

    print(f"data {DAYS[0]} .. {DAYS[-1]} ({len(DAYS)} trading days), split {SPLIT}; raw candidates "
          f"{D['raw']}, priced + 4h trend {len(P)}; capital Rs {D['capital']:,.0f}; playbooks {D['playbooks']}")
    print(f"config at cache time {D['cfg']}: atr_by_index "
          + ", ".join(f"{u} {D['atr_by_index'][u]}" for u in INDICES) + f"; live halt {HALT:.3f} (bt units)")
    assert all(tuple(D["atr_by_index"][u]) == LIVE[u] for u in INDICES), "live thresholds differ from the task"
    nar = sum(1 for t in P if t["ar"] <= 0)
    print(f"candidates with no ar (gate passes them at any threshold): {nar}\n")

    # ---- training table: day P&L / trades of run() per (index, half, threshold) -------------
    pnl = {}   # (u, h, x) -> {day: (pnl, n)}
    for u in INDICES:
        for h in (0, 1):
            sub = [t for t in P if t["u"] == u and half(t) == h]
            for x in GRID:
                agg = defaultdict(lambda: [0.0, 0])
                for t in run([t for t in sub if passes(t, x)]):
                    agg[t["day"]][0] += t["net2"]; agg[t["day"]][1] += 1
                pnl[(u, h, x)] = dict(agg)

    def score(u, h, x, lo, hi):  # training days DAYS[lo:hi]
        s, n = 0.0, 0
        for d, (p, k) in pnl[(u, h, x)].items():
            if lo <= di[d] < hi:
                s += p; n += k
        return s, n

    def choose(u, h, lo, hi, plateau, rule="R1"):
        """R1: a threshold needs >= 5 training trades of its own to be eligible.
        R2: the window needs >= 5 trades with the gate off; then every threshold is eligible
        (one that takes no trade scores 0, i.e. 'stay out' is a valid choice)."""
        sc = [score(u, h, x, lo, hi) for x in GRID]
        live = LIVE[u][h]
        best, bkey = live, None
        if rule == "R2" and sc[0][1] < MIN_TRAIN:
            return live
        for j, x in enumerate(GRID):
            if rule == "R1" and sc[j][1] < MIN_TRAIN:
                continue
            if plateau:
                nb = [sc[k][0] for k in (j - 1, j, j + 1) if 0 <= k < len(GRID)]
                v = float(np.mean(nb))
            else:
                v = sc[j][0]
            key = (round(v, 6), -abs(x - live))
            if bkey is None or key > bkey:
                best, bkey = x, key
        return best

    def rebalance_dates(freq):
        out, seen = [], set()
        for d in DAYS:
            dd = date.fromisoformat(d)
            k = dd.isocalendar()[:2] if freq == "weekly" else d[:7]
            if k not in seen:
                seen.add(k); out.append(d)
        return out

    def walk(freq, W, plateau, rule):
        """-> {day: {u: (a, b)}} for every out-of-sample day, and the list of (date, thresholds)."""
        rb = rebalance_dates(freq)
        need = 40 if W is None else W
        thr_by_day, timeline = {}, []
        for j, r in enumerate(rb):
            k = di[r]
            if k < need:
                continue
            lo = 0 if W is None else k - W
            th = {u: (choose(u, 0, lo, k, plateau, rule), choose(u, 1, lo, k, plateau, rule)) for u in INDICES}
            timeline.append((r, th))
            end = di[rb[j + 1]] if j + 1 < len(rb) else len(DAYS)
            for d in DAYS[k:end]:
                thr_by_day[d] = th
        return thr_by_day, timeline

    def apply(thr_by_day, days=None):
        keep = []
        for t in P:
            if t["day"] not in thr_by_day or (days is not None and t["day"] not in days):
                continue
            a, b = thr_by_day[t["day"]][t["u"]]
            if gate_halves(t, a, b):
                keep.append(t)
        return run(keep)

    def fixed_on(days):
        return apply({d: LIVE for d in days})

    # sanity: frozen run() == fixes_test.run on the full-year live gate
    from fixes_test import run as ft_run
    full_live = [t for t in P if gate_halves(t, *LIVE[t["u"]])]
    a_, b_ = run(full_live), ft_run(full_live, side_cooldown=COOL)
    print(line("FIXED live, whole year", a_))
    if [t["net2"] for t in a_] != [t["net2"] for t in b_]:
        print("  (note: fixes_test.run differs: the config changed since the cache; the frozen halt is used)")
    print()

    VAR = []
    for rule in ("R1", "R2"):
        for freq in ("weekly", "monthly"):
            for W in (40, 60, 120, None):
                for plateau in (False, True):
                    VAR.append((freq, W, plateau, rule))

    def vname(v):
        return f"{v[3]} {v[0]} W={'all' if v[1] is None else v[1]}{' plateau' if v[2] else ''}"
    print("training rule R1: a threshold needs >= 5 training trades of its own (else not eligible; none -> live)")
    print("training rule R2: >= 5 gate-off trades in the window (else live); any threshold eligible, 0 trades = 0\n")
    res = {}
    common = set(DAYS[120:])
    print(f"=== each variant on ITS OWN out-of-sample days vs FIXED live on the same days ===")
    print("    (BETTER BOTH = beats fixed in the in-sample AND the held-out half of those days)\n")
    for v in VAR:
        freq, W, plateau, rule = v
        tbd, tl = walk(freq, W, plateau, rule)
        days = set(tbd)
        wf, fx = apply(tbd), fixed_on(days)
        cm, cfx = apply(tbd, common), fixed_on(common & days)
        res[v] = dict(tbd=tbd, tl=tl, wf=wf, fx=fx, cm=cm, cfx=cfx, first=min(days))
        name = vname(v)
        print(f"{name}: trades from {min(days)} ({len(days)} days, {len(tl)} rebalances)")
        print("  " + line("walk-forward", wf, fx))
        print("  " + line("FIXED live, same days", fx))
    print(f"\n=== COMMON window {DAYS[120]} .. {DAYS[-1]} ({len(common)} days), all variants ===\n")
    print("  " + line("FIXED live", fixed_on(common)))
    cbase = fixed_on(common)
    for v in VAR:
        print("  " + line(vname(v), res[v]["cm"], cbase))

    # ---- how the choices moved -----------------------------------------------------------
    def tline(v):
        tl = res[v]["tl"]
        print(f"\n  {vname(v)}: {len(tl)} rebalances")
        for u in INDICES:
            for h, hn in ((0, "<12:15"), (1, ">=12:15")):
                seq = [(r, th[u][h]) for r, th in tl]
                ch = [(r, x) for k, (r, x) in enumerate(seq) if k == 0 or x != seq[k - 1][1]]
                cnt = defaultdict(int)
                for _, x in seq:
                    cnt[x] += 1
                mode = max(cnt, key=cnt.get)
                print(f"    {u:9s} {hn:7s} changes {len(ch) - 1:2d}/{len(seq) - 1:2d}  most often {mode:g} "
                      f"({cnt[mode]}/{len(seq)}), live {LIVE[u][h]:g}  | "
                      + " ".join(f"{r[2:]}:{x:g}" for r, x in ch))

    s = lambda v: sum(t["net2"] for t in res[v]["cm"])
    best = max(VAR, key=s)
    print("\n=== chosen thresholds over time (date:value at each change; 0 = gate off) ===")
    print(f"  best variant on the common window by total P&L: {vname(best)}")
    tline(best)
    for r in ("R1", "R2"):
        if best != ("weekly", 60, False, r):
            tline(("weekly", 60, False, r))
    print("\n  stability (changes / rebalances, summed over 3 indices x 2 halves):")
    for v in VAR:
        tl = res[v]["tl"]
        c = sum(1 for u in INDICES for h in (0, 1) for k in range(1, len(tl)) if tl[k][1][u][h] != tl[k - 1][1][u][h])
        tot = 6 * max(len(tl) - 1, 0)
        print(f"    {v[3]} {v[0]:7s} W={'all' if v[1] is None else v[1]!s:4s} {'plateau' if v[2] else 'argmax ':7s} "
              f"{c:3d}/{tot:3d} ({100 * c / max(tot, 1):3.0f}%)")

    # ---- current-value sanity: the last 60 trading days alone (IN-SAMPLE) ------------------
    lo, hi = len(DAYS) - 60, len(DAYS)
    print(f"\n=== IN-SAMPLE: best FIXED threshold on the last 60 trading days alone ({DAYS[lo]} .. {DAYS[-1]}) ===")
    print("    per index and half, run() on that index/half only: P&L (trades) for each grid value\n")
    for u in INDICES:
        for h, hn in ((0, "<12:15"), (1, ">=12:15")):
            sc = [score(u, h, x, lo, hi) for x in GRID]
            print(f"  {u:9s} {hn:7s} live {LIVE[u][h]:g} -> R1 {choose(u, h, lo, hi, False, 'R1'):g}"
                  f" R2 {choose(u, h, lo, hi, False, 'R2'):g} | "
                  + "  ".join(f"{x:g}:{p:+.0f}({n})" for x, (p, n) in zip(GRID, sc)))
    last = set(DAYS[lo:])
    print()
    print("  " + line("FIXED live, last 60 days", fixed_on(last)))
    for r in ("R1", "R2"):
        th = {u: (choose(u, 0, lo, hi, False, r), choose(u, 1, lo, hi, False, r)) for u in INDICES}
        print("  " + line(f"best-of-last-60 {r} (IN-SAMPLE)", apply({d: th for d in last})))
        print("    thresholds: " + ", ".join(f"{u} {th[u][0]:g}/{th[u][1]:g}" for u in INDICES))


if __name__ == "__main__":
    main()
