#!/usr/bin/env python3
"""Adversarial check of research/pace_walkforward.py (5 Oct 2026). Uses only the cached
priced candidates research_out/pace_wf_P.pkl (no candidate regeneration, no API).

  1. independent re-implementation of the walk-forward: training P&L recomputed by running
     run() on the raw training-window candidates (not the per-day cache table), with
     look-ahead assertions; must reproduce every number in pace_walkforward_result.txt
  2. common-window table on exactly matched days
  3. is the FIXED baseline itself a hindsight choice? rank of the live set among random
     fixed combinations of the grid, full year / IN / OUT
  4. non-hindsight fixed baselines on each variant's own days (gate off, uniform values,
     'fit once' = the variant's first choice held for ever)
  5. selection bias / noise: spread of the 32 variants, and a block bootstrap over ISO
     weeks of the daily P&L difference walk-forward - fixed

    nice -n 10 venv/bin/python research/pace_walkforward_check.py > research/pace_walkforward_check_result.txt
"""
import os
import pickle
import random
import re
import sys
from collections import defaultdict
from datetime import date

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
from common import HALF, SPLIT, gate_halves, summ       # noqa: E402

D = pickle.load(open(os.path.join(ROOT, "research_out", "pace_wf_P.pkl"), "rb"))
P, DAYS, HALT = D["P"], D["days"], D["halt"]
DI = {d: k for k, d in enumerate(DAYS)}
GRID = [0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8]
IDX = ["NIFTY", "BANKNIFTY", "SENSEX"]
LIVE = {"NIFTY": (1.3, 1.5), "BANKNIFTY": (1.5, 1.5), "SENSEX": (1.2, 1.2)}


def run(ts):
    return bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=30, halt_pct=HALT)


def tot(tr):
    return sum(t["net2"] for t in tr)


def gate_set(th, days=None):
    return [t for t in P if (days is None or t["day"] in days) and gate_halves(t, *th[t["u"]])]


def fmt(label, tr):
    s = summ(tr)
    return (f"{label:40s} n={s['n']:3d} win={s['win']:3.0f}% Rs{s['y']:+8,.0f} "
            f"(IN {s['i']:+7,.0f} / OUT {s['o']:+7,.0f}) maxDD {s['dd']:6,.0f}")


# ---------------------------------------------------------------- 1. re-implementation
SUB = {(u, h): [t for t in P if t["u"] == u and (t["m"] >= HALF) == bool(h)] for u in IDX for h in (0, 1)}


def train_score(u, h, x, lo, hi):
    tr = [t for t in SUB[(u, h)] if lo <= DI[t["day"]] < hi and gate_halves(t, x, x)]
    assert all(DI[t["day"]] < hi for t in tr)          # no look-ahead into the trade period
    out = run(tr)
    return tot(out), len(out)


def pick(u, h, lo, hi, plateau, rule):
    sc = [train_score(u, h, x, lo, hi) for x in GRID]
    live = LIVE[u][h]
    if rule == "R2" and sc[0][1] < 5:
        return live
    best, bk = live, None
    for j, x in enumerate(GRID):
        if rule == "R1" and sc[j][1] < 5:
            continue
        v = float(np.mean([sc[k][0] for k in (j - 1, j, j + 1) if 0 <= k < len(GRID)])) if plateau else sc[j][0]
        key = (round(v, 6), -abs(x - live))
        if bk is None or key > bk:
            best, bk = x, key
    return best


def rebal(freq):
    seen, out = set(), []
    for d in DAYS:
        k = date.fromisoformat(d).isocalendar()[:2] if freq == "weekly" else d[:7]
        if k not in seen:
            seen.add(k); out.append(d)
    return out


def walk(freq, W, plateau, rule):
    rb = rebal(freq); need = 40 if W is None else W
    tbd, tl = {}, []
    for j, r in enumerate(rb):
        k = DI[r]
        if k < need:
            continue
        lo = 0 if W is None else k - W
        th = {u: (pick(u, 0, lo, k, plateau, rule), pick(u, 1, lo, k, plateau, rule)) for u in IDX}
        end = DI[rb[j + 1]] if j + 1 < len(rb) else len(DAYS)
        for d in DAYS[k:end]:
            assert d not in tbd and DI[d] >= k        # thresholds only used after their fit date
            tbd[d] = th
        tl.append((r, th))
    return tbd, tl


def apply(tbd, days=None):
    return run([t for t in P if t["day"] in tbd and (days is None or t["day"] in days)
                and gate_halves(t, *tbd[t["day"]][t["u"]])])


VAR = [(f, W, p, r) for r in ("R1", "R2") for f in ("weekly", "monthly") for W in (40, 60, 120, None)
       for p in (False, True)]
vn = lambda v: f"{v[3]} {v[0]} W={'all' if v[1] is None else v[1]}{' plateau' if v[2] else ''}"

# numbers printed by the original script, in order
orig = []
txt = open(os.path.join(ROOT, "research", "pace_walkforward_result.txt")).read()
for m in re.finditer(r"walk-forward\s+n=\s*(\d+).*?year Rs\s*([+-][\d,]+)", txt):
    orig.append((int(m.group(1)), int(m.group(2).replace(",", ""))))

fullfix = run(gate_set(LIVE))
print(fmt("FIXED live, whole year (frozen halt)", fullfix))
print(f"cache halt {HALT}, cfg {D['cfg']}\n")
print("=== 1. independent re-implementation vs the original result file ===")
R = {}
mism = 0
for k, v in enumerate(VAR):
    tbd, tl = walk(*v)
    wf = apply(tbd)
    R[v] = dict(tbd=tbd, tl=tl, wf=wf, days=set(tbd))
    o = orig[k] if k < len(orig) else None
    ok = o == (len(wf), round(tot(wf)))
    mism += not ok
    print(f"  {vn(v):26s} re-run n={len(wf):3d} Rs{tot(wf):+8,.0f}   original {o}  {'OK' if ok else 'MISMATCH'}")
print(f"  mismatches: {mism}\n")

# ---------------------------------------------------------------- 2. common window, exact days
common = set(DAYS[120:])
print(f"=== 2. common window {DAYS[120]}.. : fixed on EXACTLY the days each variant trades ===")
for v in VAR:
    dd = common & R[v]["days"]
    wf, fx = apply(R[v]["tbd"], dd), run(gate_set(LIVE, dd))
    if len(dd) != len(common):
        print(f"  {vn(v):26s} trades {len(dd)}/{len(common)} common days: WF Rs{tot(wf):+7,.0f} vs fixed on same "
              f"days Rs{tot(fx):+7,.0f} (original compared with fixed on all {len(common)}: Rs"
              f"{tot(run(gate_set(LIVE, common))):+,.0f})")
print()

# ---------------------------------------------------------------- 3. is FIXED hindsight?
print("=== 3. rank of the LIVE fixed set among random fixed grid combinations (6 values from the grid) ===")
rng = random.Random(7)
pre = {}   # per (u, h, x): candidate list passing, so combos are quick
for u in IDX:
    for h in (0, 1):
        for x in GRID:
            pre[(u, h, x)] = [t for t in SUB[(u, h)] if gate_halves(t, x, x)]
def combo_run(th):
    return run([t for u in IDX for h in (0, 1) for t in pre[(u, h, th[u][h])]])
S = []
for _ in range(3000):
    th = {u: (rng.choice(GRID), rng.choice(GRID)) for u in IDX}
    s = summ(combo_run(th)); S.append((s["y"], s["i"], s["o"]))
S = np.array(S); ls = summ(fullfix)
for j, nm, val in ((0, "year", ls["y"]), (1, "IN", ls["i"]), (2, "OUT", ls["o"])):
    print(f"  {nm:4s}: live Rs{val:+8,.0f} beats {100 * (S[:, j] < val).mean():5.1f}% of 3000 random combos "
          f"(median Rs{np.median(S[:, j]):+,.0f}, 90th pct Rs{np.percentile(S[:, j], 90):+,.0f}, max Rs{S[:, j].max():+,.0f})")
print("  uniform settings, whole year:")
for x in GRID:
    print("    " + fmt(f"all indices {x:g}/{x:g}", combo_run({u: (x, x) for u in IDX})))
print()

# ---------------------------------------------------------------- 4. non-hindsight baselines
print("=== 4. each variant vs NON-hindsight fixed baselines on its own days (year Rs / IN / OUT) ===")
print("    fit-once = the variant's FIRST rebalance choice, held for every later day\n")
base_names = ["live", "gate off", "all 1.2", "fit-once"]
rows = []
for v in VAR:
    dd, tl = R[v]["days"], R[v]["tl"]
    cand = {"live": LIVE, "gate off": {u: (0, 0) for u in IDX}, "all 1.2": {u: (1.2, 1.2) for u in IDX},
            "fit-once": tl[0][1]}
    s_wf = summ(R[v]["wf"])
    out = [f"WF {s_wf['y']:+7,.0f} ({s_wf['i']:+6,.0f}/{s_wf['o']:+6,.0f})"]
    bs = {}
    for nm in base_names:
        s = summ(run(gate_set(cand[nm], dd))); bs[nm] = s
        out.append(f"{nm} {s['y']:+7,.0f} ({s['i']:+6,.0f}/{s['o']:+6,.0f})")
    rows.append((v, s_wf, bs))
    print(f"  {vn(v):26s} " + " | ".join(out))
wins = {nm: sum(1 for _, w, b in rows if w["i"] > b[nm]["i"] and w["o"] > b[nm]["o"]) for nm in base_names}
print("\n  variants beating the baseline in BOTH halves: " + ", ".join(f"{nm} {wins[nm]}/32" for nm in base_names))
yw = {nm: sum(1 for _, w, b in rows if w["y"] > b[nm]["y"]) for nm in base_names}
print("  variants beating the baseline on the total:   " + ", ".join(f"{nm} {yw[nm]}/32" for nm in base_names))
print()

# ---------------------------------------------------------------- 5. noise / selection bias
print("=== 5. noise: spread of variants and week-block bootstrap of (walk-forward - fixed live) ===")
diffs = np.array([w["y"] - b["live"]["y"] for _, w, b in rows])
print(f"  WF - fixed over the 32 variants (own days): median Rs{np.median(diffs):+,.0f}, "
      f"IQR Rs{np.percentile(diffs, 25):+,.0f} .. {np.percentile(diffs, 75):+,.0f}, "
      f"best Rs{diffs.max():+,.0f}, worst Rs{diffs.min():+,.0f}; 0/32 positive" if diffs.max() < 0 else
      f"  WF - fixed: {(diffs > 0).sum()}/32 positive")


def daily(tr):
    g = defaultdict(float)
    for t in tr:
        g[t["day"]] += t["net2"]
    return g


brng = np.random.default_rng(11)
print("  bootstrap (5000 resamples of ISO weeks, run() has no cross-day state so day P&L is additive):")
for v in VAR:
    dd = sorted(R[v]["days"])
    a, b = daily(R[v]["wf"]), daily(run(gate_set(LIVE, set(dd))))
    wk = defaultdict(float)
    for d in dd:
        wk[date.fromisoformat(d).isocalendar()[:2]] += a.get(d, 0.0) - b.get(d, 0.0)
    x = np.array(list(wk.values()))
    bsum = np.array([brng.choice(x, len(x)).sum() for _ in range(5000)])
    print(f"    {vn(v):26s} weeks {len(x):2d} diff Rs{x.sum():+8,.0f}  90% CI Rs{np.percentile(bsum, 5):+8,.0f} .. "
          f"{np.percentile(bsum, 95):+8,.0f}  P(diff>0) {100 * (bsum > 0).mean():4.1f}%")
# the same bootstrap against the non-hindsight 'gate off' and 'fit-once' baselines, pooled view
for nm in ("gate off", "fit-once"):
    pos = []
    for v, w, bsx in rows:
        dd = R[v]["days"]
        cand = {u: (0, 0) for u in IDX} if nm == "gate off" else R[v]["tl"][0][1]
        a, b = daily(R[v]["wf"]), daily(run(gate_set(cand, dd)))
        wk = defaultdict(float)
        for d in dd:
            wk[date.fromisoformat(d).isocalendar()[:2]] += a.get(d, 0.0) - b.get(d, 0.0)
        x = np.array(list(wk.values()))
        bsum = np.array([brng.choice(x, len(x)).sum() for _ in range(2000)])
        pos.append(((bsum > 0).mean(), x.sum()))
    print(f"  vs {nm}: WF ahead in {sum(1 for p, s in pos if s > 0)}/32 variants; "
          f"P(diff>0) median {100 * np.median([p for p, _ in pos]):.0f}%, "
          f"variants with P>95%: {sum(1 for p, _ in pos if p > 0.95)}, with P<5%: {sum(1 for p, _ in pos if p < 0.05)}")
