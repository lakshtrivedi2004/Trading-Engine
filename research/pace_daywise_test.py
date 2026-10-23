#!/usr/bin/env python3
"""Day-wise / fast / premium-style pace gates vs the live ATR20 gate (owner, 5 Oct 2026).

research/premium_pace_test.py showed, on the recorded option books, that the ATM
call+put premium pace is the FAST index pace (corr +0.99, no lead in either
direction): a premium "spike" is the index moving, seen without the 20-minute
ATR's lag. So "premium ATR" is tested here on the year of index bars as a fast
pace, and "a decent value set day-wise" as thresholds that adapt to each day.

Same candidates and pricing as research/pace_test.py (live playbook, pace gate
off at generation, costs inside the cap, exact costs, live 4h trend, F4 30 min,
one position at a time, live daily halt). Only the pace rule changes. Every
variant is printed; a variant is interesting only if it beats the live gate in
BOTH halves and its neighbours do too.

    venv/bin/python research/pace_daywise_test.py > research/pace_daywise_result.txt
"""
import os, sys
from collections import defaultdict
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features, gate_halves, HALF
from playbook_lab import INDICES, candidates
from fixes_test import priced, run, line
from arb.config import IntradayLimits

L = IntradayLimits.load(); rz.L = L
capital = float(L.capital_ceiling or L.starting_capital)


def measures(rows):
    """Per bar k (bar k closed): ATR20/3/5 against the median TR of the 200 bars before
    the ATR window starts... exactly regime.atr_ratio's baseline: median TR over the
    last 201 candles' 200 true ranges (incl. the current bar)."""
    h = np.array([r[2] for r in rows], float); l = np.array([r[3] for r in rows], float)
    c = np.array([r[4] for r in rows], float)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    n = len(tr)
    def mean_last(w):
        cs = np.r_[0, np.cumsum(tr)]
        out = np.full(n, np.nan)
        out[w - 1:] = (cs[w:] - cs[:-w]) / w
        return out
    a20, a5, a3 = mean_last(20), mean_last(5), mean_last(3)
    base = np.full(n, np.nan)
    base[199:] = np.median(swv(tr, 200), axis=1)
    return dict(tr=tr, a20=a20, a5=a5, a3=a3, base=base,
                day=[r[0][:10] for r in rows], hm=[r[0][11:16] for r in rows])


def tod_ratio(M, lookback=20):
    """ATR20 now / median ATR20 at the same minute of day over the previous `lookback` days."""
    by = defaultdict(dict)
    for k, (d, hm) in enumerate(zip(M["day"], M["hm"])):
        by[d][hm] = M["a20"][k]
    days = sorted(by)
    out = np.full(len(M["tr"]), np.nan)
    pos = {d: i for i, d in enumerate(days)}
    for k, (d, hm) in enumerate(zip(M["day"], M["hm"])):
        i = pos[d]
        if i < lookback:
            continue
        v = [by[x].get(hm) for x in days[i - lookback:i]]
        v = [x for x in v if x is not None and not np.isnan(x)]
        if len(v) >= lookback // 2:
            out[k] = M["a20"][k] / float(np.median(v))
    return out


def prevday_quantile(M, ar, q, days_back=5):
    """Threshold for day D = q-quantile of the live ratio over the 09:30-15:00 bars of the
    previous `days_back` days: the gate is set each morning from recent real data."""
    by = defaultdict(list)
    for k, (d, hm) in enumerate(zip(M["day"], M["hm"])):
        if "09:30" <= hm < "15:00" and not np.isnan(ar[k]):
            by[d].append(ar[k])
    days = sorted(by)
    thr = {}
    for i, d in enumerate(days):
        if i >= days_back:
            thr[d] = float(np.quantile(np.concatenate([by[x] for x in days[i - days_back:i]]), q))
    return thr


def today_quantile_ok(M, ar, k, q):
    """ar at k is in the top (1-q) of today's readings so far (from 09:30)."""
    d = M["day"][k]; j = k
    vals = []
    while j >= 0 and M["day"][j] == d:
        if M["hm"][j] >= "09:30" and not np.isnan(ar[j]):
            vals.append(ar[j])
        j -= 1
    if len(vals) < 15:
        return False
    return ar[k] >= np.quantile(vals, q)


def main():
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    M = {u: measures(IX[u].rows) for u in INDICES}
    for u in INDICES:
        M[u]["ar"] = np.clip(M[u]["a20"] / M[u]["base"], 0.5, 2.0)
        M[u]["f5"] = M[u]["a5"] / M[u]["base"]
        M[u]["f3"] = M[u]["a3"] / M[u]["base"]
        M[u]["tod"] = tod_ratio(M[u])
    C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 3)
    P = [t for t in priced(C, IX, capital, True) if F.h4_ret(t, 5) * t["side"] > 0]
    # sanity: our ar must reproduce the generator's (same definition, same bars)
    d = [abs(M[t["u"]]["ar"][t["i"]] - t["ar"]) for t in P if t["ar"] > 0]
    print(f"candidates priced + 4h trend: {len(P)}; ar reproduction median |diff| {np.median(d):.3f}, "
          f"95% {np.quantile(d, 0.95):.3f}\n")
    cur = dict(L.atr_by_index)

    def cur_ok(t):
        return gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))

    def g(t, key):
        return M[t["u"]][key][t["i"]]

    V = [("LIVE: ATR20 ratio, N 1.3/1.5 BN 1.5 SX 1.2", cur_ok)]
    for x in (1.5, 2.0, 2.5, 3.0):
        V.append((f"fast spike only: ATR5 ratio >= {x}", lambda t, x=x: g(t, "f5") >= x))
    for x in (2.0, 2.5, 3.0):
        V.append((f"fast spike only: ATR3 ratio >= {x}", lambda t, x=x: g(t, "f3") >= x))
    for x in (2.0, 2.5, 3.0):
        V.append((f"LIVE OR ATR3 spike >= {x}", lambda t, x=x: cur_ok(t) or g(t, "f3") >= x))
    for x in (1.0, 1.15):
        for y in (2.0, 2.5):
            V.append((f"ATR20 >= {x} AND ATR3 spike >= {y}", lambda t, x=x, y=y: g(t, "ar") >= x and g(t, "f3") >= y))
    for x in (1.0, 1.1, 1.2, 1.3, 1.5):
        V.append((f"time-of-day pace (ATR20 / same minute, 20 days) >= {x}", lambda t, x=x: g(t, "tod") >= x))
    for x in (1.1, 1.2, 1.3):
        V.append((f"LIVE AND time-of-day >= {x}", lambda t, x=x: cur_ok(t) and g(t, "tod") >= x))
    for q in (0.6, 0.7, 0.8, 0.9):
        thr = {u: prevday_quantile(M[u], M[u]["ar"], q) for u in INDICES}
        V.append((f"day-wise: ATR20 >= {int(q*100)}th pct of last 5 days",
                  lambda t, thr=thr: t["day"] in thr[t["u"]] and g(t, "ar") >= thr[t["u"]][t["day"]]))
    for q in (0.7, 0.8, 0.9):
        V.append((f"day-wise: ATR20 in top {int(round((1-q)*100))}% of today so far",
                  lambda t, q=q: today_quantile_ok(M[t["u"]], M[t["u"]]["ar"], t["i"], q)))
    base = None
    for name, ok in V:
        keep = [t for t in P if ok(t)]
        tr = run(keep, side_cooldown=30)
        print(line(name[:60], tr, base), flush=True)
        base = base or tr
    # how often is each day-wise gate open (minutes 09:30-15:00, median per day)?
    print("\nminutes per day the gate is open (median over days, 09:30-15:00, 330 max):")
    for u in INDICES:
        Mu = M[u]; a, b = cur.get(u, (L.min_atr_ratio, L.min_atr_ratio))
        thr8 = prevday_quantile(Mu, Mu["ar"], 0.8)
        cnt = defaultdict(lambda: defaultdict(int))
        for k, (dd, hm) in enumerate(zip(Mu["day"], Mu["hm"])):
            if not ("09:30" <= hm < "15:00") or np.isnan(Mu["ar"][k]):
                continue
            need = a if hm < "12:15" else b
            cnt["live"][dd] += Mu["ar"][k] >= need
            cnt["tod>=1.2"][dd] += (Mu["tod"][k] >= 1.2) if not np.isnan(Mu["tod"][k]) else 0
            cnt["prev5 p80"][dd] += (dd in thr8 and Mu["ar"][k] >= thr8[dd])
            cnt["ATR3>=2.5"][dd] += Mu["f3"][k] >= 2.5
        print(f"  {u:10s} " + "  ".join(f"{k} {int(np.median(list(v.values())))}" for k, v in cnt.items()))


if __name__ == "__main__":
    main()
