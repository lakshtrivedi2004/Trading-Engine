#!/usr/bin/env python3
"""Noise-area breakout (Zarattini, Aziz & Barbon 2024) - the one no-retest entry
that survived the live gates (research_out/no_retest/flow_noise_robust_result.txt:
vm 1.0 + 4h trend + pace, positive in both halves in all 6 window/minr cells).

Question here: the live engine's candle store holds ~5 sessions (lookback_days 7),
the paper's sigma(t) uses 14. Does a sigma from the last 5 (or 8) sessions keep
the result? Same generator, pricer and live gates as flow_noise_robust.py.

    venv/bin/python research/noise_breakout_test.py > research/noise_breakout_result.txt
"""
import os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research_out", "no_retest"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S
import realistic as rz
from common import Features, run, summ, gate_halves
from flow_noise_robust import atr_ratio_series, INDICES


def prep_n(ix, ndays):
    """screen.prep with sigma(t) from the previous `ndays` sessions (paper: 14, min 10)."""
    P = S.prep(ix)
    rows = ix.rows; hm = S.hm
    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r[0][:10], []).append(i)
    keys = P["keys"]
    for k, d in enumerate(keys):
        prev = keys[max(0, k - ndays):k]
        sig = {}
        if len(prev) >= max(3, int(0.7 * ndays)):
            acc = {}
            for p in prev:
                po = rows[days[p][0]][1]
                for j in days[p]:
                    acc.setdefault(hm(rows[j][0]) + 1, []).append(abs(rows[j][4] / po - 1))
            sig = {t: float(np.mean(v)) for t, v in acc.items()}
        P["D"][d]["sig"] = sig
    return P


def main():
    L = rz.L
    capital = float(L.capital_ceiling or L.starting_capital)
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    halves = dict(L.atr_by_index or {})
    print(f"capital {capital:,.0f} risk {L.max_risk_pct_per_trade}%  costs inside, exact costs, F4 30 min, live halt; "
          f"vm 1.0, exit noise, 4h trend + live pace gate\n")
    for nd in (14, 8, 5, 4):
        PP = {n: prep_n(IX[n], nd) for n in INDICES}
        AR = {n: atr_ratio_series(PP[n]) for n in INDICES}
        for win in ("09:45-14:45", "10:15-14:15"):
            for minr in (1.0, 1.5, 2.0):
                lo, hi = win.split("-")
                var = dict(name="N1", kind="noise", lo=lo, hi=hi, grid=30, vm=1.0, minr=minr, exit=dict(trail="noise"))
                P = []
                for n in INDICES:
                    for t in S.gen(n, IX[n], PP[n], var):
                        r = rz.reprice(t, IX[t["u"]], capital, next_expiry_on_expiry_day=True, costs_inside=True,
                                       slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
                        if r is None: continue
                        u = dict(t); u.update(r); u["ar"] = float(AR[t["u"]][t["i"]]); P.append(u)
                keep = [t for t in P if F.h4_ret(t, 5) * t["side"] > 0 and
                        gate_halves(t, *halves.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
                tr = run(keep, side_cooldown=30); s = summ(tr)
                per = " ".join(f"{u[:2]}{sum(t['net2'] for t in tr if t['u'] == u):+7,.0f}" for u in INDICES)
                ex = {}
                for t in tr: ex[t.get("why", "?")] = ex.get(t.get("why", "?"), 0) + 1
                print(f"sigma {nd:2d}d {win} minr {minr:.1f}  n={s['n']:3d} win={s['win']:3.0f}% IN {s['i']:+8,.0f} "
                      f"OUT {s['o']:+8,.0f} yr {s['y']:+8,.0f} dd {s['dd']:6,.0f} m+ {s['mpos']:3.0f}% | {per} | {ex}",
                      flush=True)


if __name__ == "__main__":
    main()
