#!/usr/bin/env python3
# Tracked copy of research_out/momentum/screen.py (research_out/ is git-ignored), so the VM
# can run tools/shadow_research.py. Keep the two in step.
"""First-look screen of momentum-capture signals (research agent, 5 Oct 2026).

Signals are read on COMPLETED 1-minute index bars; entry at the signal bar's
close. Index exits are computed here; each trade is then priced by
research/lab/realistic.py:reprice (contract the engine would buy, BS premium,
measured decay, spreads, exact costs, premium stop + 1R breakeven, costs inside
the cap as live) and run through bt.run (one position at a time, daily halt).
Pre-declared variants only; every one is reported.

    venv/bin/python research_out/momentum/screen.py
"""
import os, sys, math
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features, run, summ, SPLIT

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
def hm(iso): return int(iso[11:13]) * 60 + int(iso[14:16])
T = lambda s: int(s[:2]) * 60 + int(s[3:])

def prep(ix):
    rows = ix.rows; n = len(rows)
    c = np.array([r[4] for r in rows]); h = np.array([r[2] for r in rows]); l = np.array([r[3] for r in rows])
    pc_ = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc_), abs(l - pc_)))
    atr = np.zeros(n)                                   # ATR20 of bars i-20..i-1 (prior bars)
    cs = np.concatenate([[0], np.cumsum(tr)])
    for i in range(21, n): atr[i] = (cs[i] - cs[i - 20]) / 20
    days = {}
    for i, r in enumerate(rows): days.setdefault(r[0][:10], []).append(i)
    keys = sorted(days)
    D = {}
    for k, d in enumerate(keys):
        idx = days[d]; o = rows[idx[0]][1]
        prev = keys[max(0, k - 14):k]
        pc = rows[days[keys[k - 1]][-1]][4] if k else o
        adr = np.mean([max(rows[j][2] for j in days[p]) - min(rows[j][3] for j in days[p]) for p in keys[max(0, k-10):k]]) if k else 0
        # sigma(t): mean |close_t/open - 1| over the previous 14 sessions, by minute of day (bar END time)
        sig, todtr = {}, {}
        if len(prev) >= 10:
            acc, acc_tr = {}, {}
            for p in prev:
                po = rows[days[p][0]][1]
                for j in days[p]:
                    t = hm(rows[j][0]) + 1
                    acc.setdefault(t, []).append(abs(rows[j][4] / po - 1))
                    acc_tr.setdefault(t, []).append(tr[j] if j != days[p][0] else rows[j][2] - rows[j][3])
            sig = {t: float(np.mean(v)) for t, v in acc.items()}
            raw = {t: float(np.mean(v)) for t, v in acc_tr.items()}
            for t in raw:   # smooth +-2 minutes
                todtr[t] = float(np.mean([raw[u] for u in range(t - 2, t + 3) if u in raw]))
        D[d] = dict(idx=idx, o=o, pc=pc, adr=adr, sig=sig, todtr=todtr)
    return dict(c=c, h=h, l=l, tr=tr, atr=atr, D=D, keys=keys)

def exit_walk(rows, P, i, side, stop, mode, ent_atr, extra=None, last=T("15:25")):
    """Index exit. mode: dict(trail='chand'|'noise', k=3.0, ts=bars, ts_r=R, maxh=bars)."""
    c, h, l = P["c"], P["h"], P["l"]; S0 = c[i]; risk = abs(S0 - stop); day = rows[i][0][:10]
    best = S0; j = i
    for j in range(i + 1, len(rows)):
        if rows[j][0][:10] != day: return j - 1, c[j - 1], "eod"
        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
            return j, (min(rows[j][1], stop) if side > 0 else max(rows[j][1], stop)), "stop"
        if hm(rows[j][0]) + 1 >= last: return j, c[j], "eod"
        r_now = (c[j] - S0) * side / risk
        held = j - i
        if mode.get("ts") and held >= mode["ts"] and r_now < mode["ts_r"]: return j, c[j], "time_stop"
        if mode.get("maxh") and held >= mode["maxh"]: return j, c[j], "max_hold"
        if mode["trail"] == "chand":
            best = max(best, h[j]) if side > 0 else min(best, l[j])
            lvl = best - side * mode["k"] * ent_atr
            stop = max(stop, lvl) if side > 0 else min(stop, lvl)
        elif mode["trail"] == "noise" and extra is not None:
            t = hm(rows[j][0]) + 1
            if (t - T("09:15")) % 30 == 0:          # Zarattini: stop moves on the half-hour grid
                lvl = extra(j, side)
                if lvl is not None:
                    stop = max(stop, lvl) if side > 0 else min(stop, lvl)
    return j, c[j], "eod"

def twap_at(rows, d, j):
    xs = [(rows[k][2] + rows[k][3] + rows[k][4]) / 3 for k in d["idx"] if k <= j]
    return sum(xs) / len(xs)

def cand(name, ix, i, j, px, side, risk, why, play):
    S0 = ix.rows[i][4]; lot = ix.lot
    return dict(u=name, day=ix.rows[i][0][:10], m=hm(ix.rows[i][0]) + 1, i=i, j=j, side=side, risk=risk,
                delta=0.5, net=(px - S0) * side * 0.5 * lot - 75.0, why=why, held=j - i, plays=[play], ar=0.0, R=0.0)

def gen(name, ix, P, var):
    rows, out = ix.rows, []
    c, h, l, atr = P["c"], P["h"], P["l"], P["atr"]
    for d in P["keys"]:
        dd = P["D"][d]
        if not dd["sig"]: continue
        idx = dd["idx"]; o, pc = dd["o"], dd["pc"]
        busy = -1
        def band(j, side):
            t = hm(rows[j][0]) + 1; s = dd["sig"].get(t)
            if s is None: return None
            ub, lb = max(o, pc) * (1 + var.get("vm", 1.0) * s), min(o, pc) * (1 - var.get("vm", 1.0) * s)
            tw = twap_at(rows, dd, j)
            return max(ub, tw) if side > 0 else min(lb, tw)
        for i in idx:
            t = hm(rows[i][0]) + 1                              # bar end time
            if i <= busy or atr[i] <= 0: continue
            a = atr[i]; kind = var["kind"]; sig = None
            if kind == "noise":
                if not (T(var["lo"]) <= t <= T(var["hi"])) or (t - T("09:15")) % var["grid"]: continue
                s = dd["sig"].get(t)
                if s is None: continue
                ub, lb = max(o, pc) * (1 + var.get("vm", 1.0) * s), min(o, pc) * (1 - var.get("vm", 1.0) * s)
                side = 1 if c[i] > ub else -1 if c[i] < lb else 0
                if not side: continue
                lvl = band(i, side)
                stop = min(lvl, c[i] - var["minr"] * a) if side > 0 else max(lvl, c[i] + var["minr"] * a)
                sig = (side, stop)
            elif kind == "od":
                if t != T(var["at"]): continue
                first = idx[0]; win = [k for k in idx if k <= i]
                s = dd["sig"].get(t)
                if s is None: continue
                mv = c[i] / o - 1; side = 1 if mv > s * var.get("vm", 1.0) else -1 if mv < -s * var.get("vm", 1.0) else 0
                if not side: continue
                after = [k for k in win if k >= first + 3]
                if side > 0 and min(l[k] for k in after) <= o: continue          # traded back through the open
                if side < 0 and max(h[k] for k in after) >= o: continue
                hi_, lo_ = max(h[k] for k in win), min(l[k] for k in win)
                pos = (c[i] - lo_) / (hi_ - lo_) if hi_ > lo_ else .5
                if (side > 0 and pos < 0.7) or (side < 0 and pos > 0.3): continue
                if var.get("gap") and (o - pc) * side < 0: continue
                rec = win[-8:]
                stop = min(min(l[k] for k in rec) - 0.1 * a, c[i] - var["minr"] * a) if side > 0 else \
                       max(max(h[k] for k in rec) + 0.1 * a, c[i] + var["minr"] * a)
                sig = (side, stop)
            elif kind in ("reb", "reb_pb"):
                if not (T(var["lo"]) <= t <= T(var["hi"])): continue
                base = dd["todtr"].get(t)
                if not base: continue
                rng = h[i] - l[i]; body = c[i] - rows[i][1]
                if rng < var["k"] * base or abs(body) < 0.6 * rng: continue
                side = 1 if body > 0 else -1
                pos = (c[i] - l[i]) / rng
                if (side > 0 and pos < 0.75) or (side < 0 and pos > 0.25): continue
                if var.get("with_open") and (c[i] - o) * side <= 0: continue
                if kind == "reb":
                    stop = min(l[i] - 0.1 * a, c[i] - var["minr"] * a) if side > 0 else max(h[i] + 0.1 * a, c[i] + var["minr"] * a)
                    sig = (side, stop)
                else:
                    mid = (h[i] + l[i]) / 2; pb = False; got = None
                    for k in range(i + 1, min(i + 11, idx[-1])):
                        if (side > 0 and c[k] < mid) or (side < 0 and c[k] > mid): break
                        if not pb:
                            pb = (c[k] < c[k - 1]) if side > 0 else (c[k] > c[k - 1])
                            continue
                        if (side > 0 and c[k] > h[k - 1]) or (side < 0 and c[k] < l[k - 1]):
                            got = k; break
                    if got is None: continue
                    seg = range(i + 1, got + 1)
                    stop = min(min(l[q] for q in seg) - 0.1 * a, c[got] - var["minr"] * a) if side > 0 else \
                           max(max(h[q] for q in seg) + 0.1 * a, c[got] + var["minr"] * a)
                    i = got; sig = (side, stop); a = atr[i]
            elif kind == "lh30":
                if t != T("15:00"): continue
                mv = c[i] - pc
                if abs(mv) < var["adr_frac"] * dd["adr"]: continue
                side = 1 if mv > 0 else -1
                stop = c[i] - side * var["minr"] * a
                sig = (side, stop)
            if sig is None: continue
            side, stop = sig
            j, px, why = exit_walk(rows, P, i, side, stop, var["exit"], a, extra=band if var["exit"]["trail"] == "noise" else None,
                                   last=T("15:29") if kind == "lh30" else T("15:25"))
            out.append(cand(name, ix, i, j, px, side, abs(c[i] - stop), why, var["name"]))
            busy = j
    return out

CH = dict(trail="chand", k=3.0, ts=15, ts_r=0.3, maxh=60)
VARIANTS = [
    dict(name="N1_noise30_faithful", kind="noise", lo="09:45", hi="14:45", grid=30, minr=1.5, exit=dict(trail="noise")),
    dict(name="N2_noise5_early", kind="noise", lo="09:20", hi="10:30", grid=5, minr=1.5, exit=dict(trail="noise")),
    dict(name="N3_noise5_early_chand", kind="noise", lo="09:20", hi="10:30", grid=5, minr=1.5, exit=CH),
    dict(name="O1_opendrive_0930", kind="od", at="09:30", minr=1.5, exit=CH),
    dict(name="O2_opendrive_0930_gap", kind="od", at="09:30", minr=1.5, gap=True, exit=CH),
    dict(name="R1_reb2.5", kind="reb", lo="09:20", hi="14:30", k=2.5, minr=1.0, with_open=True, exit=CH),
    dict(name="R2_reb2.5_firstpullback", kind="reb_pb", lo="09:20", hi="14:30", k=2.5, minr=1.0, with_open=True, exit=CH),
    dict(name="L1_lasthalfhour", kind="lh30", adr_frac=0.5, minr=1.5, exit=dict(trail="none")),
]

def main():
    L = rz.L
    capital = float(L.capital_ceiling or L.starting_capital)
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    PP = {n: prep(IX[n]) for n in INDICES}
    print(f"capital {capital:,.0f} risk {L.max_risk_pct_per_trade}% costs_inside=True split {SPLIT}")
    for var in VARIANTS:
        C = [t for n in INDICES for t in gen(n, IX[n], PP[n], var)]
        P = []
        for t in C:
            r = rz.reprice(t, IX[t["u"]], capital, next_expiry_on_expiry_day=True, costs_inside=True,
                           slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
            if r is None: continue
            u = dict(t); u["idx_pts"] = (t["net"] + 75) / (0.5 * IX[t["u"]].lot) * 1.0; u.update(r); P.append(u)
        for lab, ts in (("all", P), ("+4h", [t for t in P if F.h4_ret(t, 5) * t["side"] > 0])):
            tr = run(ts); s = summ(tr)
            gR = np.mean([t["idx_pts"] / t["risk"] for t in tr]) if tr else 0
            print(f"{var['name']:26s} {lab:4s} raw {len(C):4d} priced {len(P):4d} | n={s['n']:3d} win={s['win']:3.0f}% "
                  f"IN {s['i']:+8,.0f} OUT {s['o']:+8,.0f} year {s['y']:+8,.0f} avg {s['avg']:+5.0f} dd {s['dd']:6,.0f} "
                  f"m+ {s['mpos']:3.0f}% grossR {gR:+.2f}", flush=True)
            if lab == "all":
                for u in INDICES:
                    sub = [t for t in tr if t["u"] == u]
                    if sub:
                        ss = summ(sub); print(f"    {u:10s} n={ss['n']:3d} win={ss['win']:3.0f}% IN {ss['i']:+8,.0f} OUT {ss['o']:+8,.0f}")

if __name__ == "__main__":
    main()
