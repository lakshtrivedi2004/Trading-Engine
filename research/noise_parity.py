#!/usr/bin/env python3
"""Parity: arb/noise_breakout.py (the live shadow's pure rule), run bar by bar
with only the data up to each bar, against the backtest the evidence comes
from - research_out/momentum/screen.py:gen (kind "noise", vm 1.0, minr 1.5,
09:45-14:45, 30-minute grid, exit trail "noise", 14-session sigma) - BEFORE
pricing.

Compared, per index over the whole year of .cache/history rows:
  entries  (bar, side, stop)        exits  (bar, reason, index exit price)
and, on every common entry, the two live gates the evidence applied on top:
  4h trend   arb/regime.py:trend_4h on the bars up to the entry vs
             research/lab/common.py Features.h4_ret(t, 5)
  pace       arb/noise_breakout.py:pace_ratio vs
             research_out/no_retest/flow_noise_robust.py:atr_ratio_series

    nice venv/bin/python research/noise_parity.py > research/noise_parity_result.txt
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research_out", "no_retest"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S                                   # noqa: E402
import realistic as rz                               # noqa: E402
from common import Features                          # noqa: E402
from flow_noise_robust import atr_ratio_series       # noqa: E402
from arb import noise_breakout as nb                 # noqa: E402
from arb.regime import trend_4h                      # noqa: E402

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
VM, MINR, LO, HI, GRID, NDAYS, MIN_DAYS = 1.0, 1.5, "09:45", "14:45", 30, 14, 10
LAST = S.T("15:25")                                  # exit_walk's eod for kind "noise"
TOL = 1e-6


def module_trades(rows):
    """The pure module, bar by bar: sigma from the previous sessions only, the
    signal on each completed bar, the trail on each later bar."""
    days = nb.by_day(rows)
    keys = sorted(days)
    first = {}
    k0 = 0
    for d in keys:
        first[d] = k0
        k0 += len(days[d])
    out = []
    for k, d in enumerate(keys):
        sigma = nb.sigma_profile({p: days[p] for p in keys[max(0, k - NDAYS):k]}, NDAYS, MIN_DAYS)
        if not sigma:
            continue
        today = days[d]
        o = today[0][1]
        pc = days[keys[k - 1]][-1][4] if k else o
        s0 = first[d]
        open_, busy = None, -1
        for n, r in enumerate(today):
            i = s0 + n
            if open_ is not None:
                st = nb.trail_step(open_["state"], today[:n + 1], o, pc, sigma, VM, GRID, LAST)
                open_["state"] = st
                if "exit" in st:
                    open_.update(j=i, why=st["exit"]["reason"], px=st["exit"]["px"])
                    out.append(open_); open_ = None; busy = i
                continue
            if i <= busy:
                continue
            # 450 bars: the slice must hold today's first bar (o) - a session is 375
            sig = nb.signal(rows[max(0, i - 450):i + 1], pc, sigma, VM, MINR, LO, HI, GRID)
            if sig["side"]:
                open_ = dict(i=i, side=sig["side"], stop=sig["stop"], close=sig["close"],
                             state={"side": sig["side"], "stop": sig["stop"]})
        if open_ is not None:                        # the session ended before 15:25
            j = s0 + len(today) - 1
            open_.update(j=j, why="eod", px=today[-1][4]); out.append(open_)
    return out


def main():
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    var = dict(name="N1", kind="noise", lo=LO, hi=HI, grid=GRID, vm=VM, minr=MINR, exit=dict(trail="noise"))
    tot = dict(ref=0, mod=0, ent=0, ext=0, h4=0, pace=0, common=0)
    print(f"noise-area breakout parity: vm {VM} minr {MINR} {LO}-{HI} grid {GRID} sigma {NDAYS}d (min {MIN_DAYS}), "
          f"exit trail noise, eod 15:25; tolerance {TOL}\n")
    for name in INDICES:
        ix = IX[name]; rows = ix.rows
        P = S.prep(ix)
        ref = S.gen(name, ix, P, var)
        AR = atr_ratio_series(P)
        mod = module_trades(rows)
        R = {t["i"]: t for t in ref}; M = {t["i"]: t for t in mod}
        both = sorted(set(R) & set(M))
        only_r = sorted(set(R) - set(M)); only_m = sorted(set(M) - set(R))
        ent_ok = ext_ok = h4_ok = pace_ok = 0
        bad = []
        lot = ix.lot
        for i in both:
            r, m = R[i], M[i]
            r_stop = rows[i][4] - r["side"] * r["risk"]
            r_px = rows[i][4] + r["side"] * (r["net"] + 75.0) / (0.5 * lot)
            e = r["side"] == m["side"] and abs(r_stop - m["stop"]) <= TOL * max(1.0, abs(r_stop))
            x = r["j"] == m["j"] and r["why"] == m["why"] and abs(r_px - m["px"]) <= 1e-4
            ent_ok += e; ext_ok += x
            if not (e and x):
                bad.append(f"  {rows[i][0]} side {r['side']}/{m['side']} stop {r_stop:.4f}/{m['stop']:.4f} "
                           f"exit {rows[r['j']][0][11:16]} {r['why']} {r_px:.2f} / "
                           f"{rows[m['j']][0][11:16]} {m['why']} {m['px']:.2f}")
            t = dict(u=name, i=i)
            h_ref = F.h4_ret(t, 5)
            h_mod = trend_4h(rows[max(0, i - 3000):i + 1], 5)
            h4_ok += h_ref == h_mod
            if h_ref != h_mod:
                bad.append(f"  {rows[i][0]} 4h trend ref {h_ref} module {h_mod}")
            p_ref = float(AR[i]); p_mod = nb.pace_ratio(rows[max(0, i - 400):i + 1])
            pace_ok += abs(p_ref - p_mod) <= 1e-9
            if abs(p_ref - p_mod) > 1e-9:
                bad.append(f"  {rows[i][0]} pace ref {p_ref:.6f} module {p_mod:.6f}")
        print(f"{name:10s} screen.gen {len(ref):4d} entries | module {len(mod):4d} | common {len(both):4d} "
              f"| only screen {len(only_r)} | only module {len(only_m)}")
        print(f"{'':10s} entry (side, stop) match {ent_ok}/{len(both)} | exit (bar, reason, index px) match "
              f"{ext_ok}/{len(both)} | 4h trend match {h4_ok}/{len(both)} | pace match {pace_ok}/{len(both)}")
        for i in only_r:
            print(f"  only screen: {rows[i][0]} side {R[i]['side']}")
        for i in only_m:
            print(f"  only module: {rows[i][0]} side {M[i]['side']}")
        for b in bad[:40]:
            print(b)
        why = {}
        for t in mod:
            why[t["why"]] = why.get(t["why"], 0) + 1
        print(f"{'':10s} exits {why}\n")
        tot["ref"] += len(ref); tot["mod"] += len(mod); tot["common"] += len(both)
        tot["ent"] += ent_ok; tot["ext"] += ext_ok; tot["h4"] += h4_ok; tot["pace"] += pace_ok
    c = max(1, tot["common"])
    print(f"ALL        screen.gen {tot['ref']} | module {tot['mod']} | common {tot['common']} | "
          f"entries {tot['ent']}/{tot['common']} ({100 * tot['ent'] / c:.1f}%) | exits {tot['ext']}/{tot['common']} "
          f"({100 * tot['ext'] / c:.1f}%) | 4h {tot['h4']}/{tot['common']} | pace {tot['pace']}/{tot['common']}")


if __name__ == "__main__":
    main()
