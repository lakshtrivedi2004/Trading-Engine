#!/usr/bin/env python3
"""Which stocks move, and does it last? Daily-bar study of the F&O universe (research/PAPERS.md 0i).

    python tools/fetch_stocks.py daily 370     # once
    python tools/stock_study.py

Every statistic is computed separately in-sample (before --split) and held-out,
and a finding counts only if it has the same sign and is material in both.
Cross-sectional statistics are computed per day and averaged, with a t-stat
over days, so one wild day cannot carry the answer.

  1  persistence  - does what is known before the open (ATR%, yesterday's range,
                    the opening gap) rank today's range?
  2  direction    - do opening gaps and yesterday's extreme movers continue or
                    reverse from the open to the close?
  3  beta         - is a constituent's sensitivity to its index stable enough to
                    pick "the bank that falls more than the others"?
  4  delivery     - does a big move on low delivery (speculative) behave
                    differently from one on high delivery (positional money)?
"""
import argparse, glob, json, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ap = argparse.ArgumentParser()
ap.add_argument("--split", default="2026-05-25")
ap.add_argument("--min-turnover-cr", type=float, default=50.0,
                help="median daily turnover floor, Rs crore (liquidity for a scalp)")
a = ap.parse_args()

D = {}
for p in glob.glob(os.path.join(ROOT, ".cache/history/stocks/*_1d.json")):
    rows = json.load(open(p))
    if len(rows) >= 120:
        D[os.path.basename(p)[:-8]] = rows
days = sorted({r[0] for rows in D.values() for r in rows})
ix = {d: i for i, d in enumerate(days)}
S = sorted(D)
n, m = len(days), len(S)
O, H, Lo, C, V, T, DV = (np.full((n, m), np.nan) for _ in range(7))
for j, s in enumerate(S):
    for r in D[s]:
        i = ix[r[0]]
        O[i, j], H[i, j], Lo[i, j], C[i, j], V[i, j], T[i, j], DV[i, j] = r[1:8]
PC = np.vstack([np.full((1, m), np.nan), C[:-1]])
# A corporate action (split/bonus) shows up as a huge overnight "gap"; blank those days.
ca = np.abs(O / PC - 1) > 0.25
for X in (O, H, Lo, C, PC):
    X[ca] = np.nan
TR = np.fmax(H - Lo, np.fmax(np.abs(H - PC), np.abs(Lo - PC))) / PC
RNG = (H - Lo) / PC                 # today's range, % of previous close
GAP = O / PC - 1
OC = C / O - 1                      # open to close
RET = C / PC - 1
ATR = np.full((n, m), np.nan)       # 14-day ATR%, known BEFORE day i
for i in range(15, n):
    ATR[i] = np.nanmean(TR[i - 14:i], axis=0)
TURN = np.full((n, m), np.nan)
for i in range(20, n):
    TURN[i] = np.nanmedian(T[i - 20:i], axis=0) / 1e7      # Rs crore, trailing
LIQ = TURN >= a.min_turnover_cr
ins = np.array([d < a.split for d in days])
print(f"{m} stocks, {n} sessions {days[0]} .. {days[-1]}; in-sample < {a.split} "
      f"({ins.sum()} d), held-out {(~ins).sum()} d; liquid = median turnover >= Rs {a.min_turnover_cr:g} cr "
      f"(~{np.nanmean(LIQ[20:].sum(1)):.0f} stocks/day)")


def rank(x):
    o = np.argsort(np.argsort(x)); return o.astype(float)


def xs_corr(X, Y, mask):
    """Mean over days of the cross-sectional Spearman correlation, and its t."""
    out = []
    for i in range(20, n):
        k = mask[i] & np.isfinite(X[i]) & np.isfinite(Y[i])
        if k.sum() >= 20:
            out.append(np.corrcoef(rank(X[i][k]), rank(Y[i][k]))[0, 1])
    out = np.array(out)
    return out


def both(label, fn, fmt="{:+.3f}"):
    r = []
    for per in (ins, ~ins):
        v = fn(per)
        r.append(v)
    def f(v):
        mu, t, k = v
        return (fmt.format(mu) + f" (t {t:+.1f}, n {k})") if k else "n/a"
    print(f"   {label:58s} IN {f(r[0]):28s} OUT {f(r[1])}")


def summ(vals):
    vals = np.asarray(vals, float); vals = vals[np.isfinite(vals)]
    if len(vals) < 3:
        return 0.0, 0.0, len(vals)
    return vals.mean(), vals.mean() / (vals.std(ddof=1) / np.sqrt(len(vals))), len(vals)


def per_day(X, Y, per):
    c = []
    for i in range(20, n):
        if not per[i]:
            continue
        k = LIQ[i] & np.isfinite(X[i]) & np.isfinite(Y[i])
        if k.sum() >= 20:
            c.append(np.corrcoef(rank(X[i][k]), rank(Y[i][k]))[0, 1])
    return summ(c)


print("\n1. PERSISTENCE - rank correlation with today's range (liquid names)")
YR = np.vstack([np.full((1, m), np.nan), RNG[:-1]])
both("14-day ATR% (known before the open)", lambda p: per_day(ATR, RNG, p))
both("yesterday's range%", lambda p: per_day(YR, RNG, p))
both("|opening gap| (known at 09:15)", lambda p: per_day(np.abs(GAP), RNG, p))
both("|opening gap| / ATR", lambda p: per_day(np.abs(GAP) / ATR, RNG / ATR, p))


def decile_range(p):
    top, rest = [], []
    for i in range(20, n):
        if not p[i]:
            continue
        k = LIQ[i] & np.isfinite(ATR[i]) & np.isfinite(RNG[i])
        if k.sum() < 30:
            continue
        cut = np.nanpercentile(ATR[i][k], 90)
        top.append(np.nanmean(RNG[i][k & (ATR[i] >= cut)])); rest.append(np.nanmean(RNG[i][k & (ATR[i] < cut)]))
    return np.mean(top) * 100, np.mean(rest) * 100, len(top)
for per, nm in ((ins, "IN "), (~ins, "OUT")):
    t_, r_, k = decile_range(per)
    print(f"   {nm} top-decile ATR stocks range {t_:.2f}%/day vs the rest {r_:.2f}%/day ({k} days)")

print("\n2. DIRECTION - open-to-close, signed with the signal (bp; + = continuation)")


def signed(sig, thr, p, top=None):
    vals = []
    for i in range(20, n):
        if not p[i]:
            continue
        k = LIQ[i] & np.isfinite(sig[i]) & np.isfinite(OC[i])
        if top:
            idx = np.where(k)[0]
            if len(idx) < 2 * top:
                continue
            o = idx[np.argsort(sig[i][idx])]
            pick = list(o[:top]) + list(o[-top:])
        else:
            pick = np.where(k & (np.abs(sig[i]) >= thr))[0]
        vals += [np.sign(sig[i][j]) * OC[i][j] * 1e4 for j in pick]
    return summ(vals)
YRET = np.vstack([np.full((1, m), np.nan), RET[:-1]])
both("gap >= 1%: continue the gap?", lambda p: signed(GAP, 0.01, p), "{:+.1f}")
both("gap >= 2%", lambda p: signed(GAP, 0.02, p), "{:+.1f}")
both("gap / ATR >= 0.5", lambda p: signed(GAP / ATR, 0.5, p), "{:+.1f}")
both("yesterday's top/bottom 10 movers: continue?", lambda p: signed(YRET, 0, p, top=10), "{:+.1f}")
# Relative to the day: did it keep going versus the cross-section (removes market-wide drift)?
OCx = OC - np.nanmean(np.where(LIQ, OC, np.nan), axis=1, keepdims=True)
_OC = OC
OC = OCx
both("gap >= 1%, market-adjusted", lambda p: signed(GAP, 0.01, p), "{:+.1f}")
both("yesterday's top/bottom 10, market-adjusted", lambda p: signed(YRET, 0, p, top=10), "{:+.1f}")
OC = _OC

print("\n3. BETA - is 'this stock moves more than its index' stable? (60-day betas, disjoint windows)")
idx_close = {}
for name in ("NIFTY", "BANKNIFTY"):
    pth = os.path.join(ROOT, ".cache/history", f"{name}.json")
    if os.path.exists(pth):
        dd = {}
        for r in json.load(open(pth)):
            dd[r[0][:10]] = r[4]
        idx_close[name] = np.array([dd.get(d, np.nan) for d in days])
try:
    from arb.nse import NseClient
    members = {k: NseClient().index_members(k) for k in ("NIFTY", "BANKNIFTY")}
except Exception:
    members = {}
for name, cl in idx_close.items():
    mem = [s for s in members.get(name, []) if s in S]
    if len(mem) < 5:
        print(f"   {name}: members unavailable"); continue
    ir = cl[1:] / cl[:-1] - 1
    betas = []
    for w0 in range(1, n - 60, 60):
        b = {}
        for s in mem:
            j = S.index(s); y = RET[w0:w0 + 60, j]; x = ir[w0 - 1:w0 + 59]
            k = np.isfinite(x) & np.isfinite(y)
            if k.sum() > 40:
                b[s] = np.cov(x[k], y[k])[0, 1] / np.var(x[k])
        betas.append(b)
    cors = []
    for b1, b2 in zip(betas, betas[1:]):
        common = [s for s in b1 if s in b2]
        if len(common) > 4:
            cors.append(np.corrcoef(rank(np.array([b1[s] for s in common])),
                                    rank(np.array([b2[s] for s in common])))[0, 1])
    last = betas[-1] if betas else {}
    top = sorted(last.items(), key=lambda kv: -kv[1])
    print(f"   {name}: {len(mem)} members; rank corr of beta, one 60-day window to the next: "
          f"{', '.join(f'{c:+.2f}' for c in cors)}")
    print(f"      latest 60-day betas: " + ", ".join(f"{s} {b:.2f}" for s, b in top))

print("\n4. DELIVERY - big movers (|ret| top 10 each side): next-day open-to-close continuation, bp")
YDV = np.vstack([np.full((1, m), np.nan), DV[:-1]])
for lo_, hi_, nm in ((0, 35, "delivery < 35% (speculative)"), (50, 101, "delivery >= 50% (positional)")):
    def fn(p, lo_=lo_, hi_=hi_):
        vals = []
        for i in range(21, n):
            if not p[i]:
                continue
            k = LIQ[i] & np.isfinite(YRET[i]) & np.isfinite(OC[i]) & np.isfinite(YDV[i])
            idx = np.where(k)[0]
            if len(idx) < 30:
                continue
            o = idx[np.argsort(YRET[i][idx])]
            for j in list(o[:10]) + list(o[-10:]):
                if lo_ <= YDV[i][j] < hi_:
                    vals.append(np.sign(YRET[i][j]) * OC[i][j] * 1e4)
        return summ(vals)
    both(nm, fn, "{:+.1f}")
print("\nDaily bars cannot say what happens inside the day; the intraday replay "
      "(tools/stock_backtest.py) needs 1-minute bars fetched after the close.")
