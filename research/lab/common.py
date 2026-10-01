"""Shared helpers: gates, summaries, multi-day features."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from realistic import bt, INDICES

SPLIT = "2026-05-25"
HALF = 12 * 60 + 15
USER = {"NIFTY": (1.3, 1.5), "BANKNIFTY": (1.5, 1.5), "SENSEX": (1.2, 1.2),
        "MIDCPNIFTY": (0.8, 0.8), "FINNIFTY": (1.5, 1.7)}

def midday(m): return 10 * 60 + 15 <= m < 14 * 60 + 30

def gate_current(t, X=1.3, M=1.5):
    ar = t["ar"]
    if ar > 0 and ar < X: return False
    return not (midday(t["m"]) and ar < M)

def gate_halves(t, a, b):
    X = a if t["m"] < HALF else b
    return X <= 0 or t["ar"] <= 0 or t["ar"] >= X

def run(ts):
    return bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])))

def summ(tr):
    ins = [t["net2"] for t in tr if t["day"] < SPLIT]; out = [t["net2"] for t in tr if t["day"] >= SPLIT]
    p = np.array(ins + out)
    if not len(p): return dict(n=0, win=0, i=0, o=0, y=0, dd=0, avg=0, mpos=0)
    eq = np.cumsum([t["net2"] for t in tr]); pk = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    months = {}
    for t in tr: months[t["day"][:7]] = months.get(t["day"][:7], 0) + t["net2"]
    return dict(n=len(p), win=100 * (p > 0).mean(), i=sum(ins), o=sum(out), y=p.sum(),
                dd=float((pk - eq).max()), avg=p.mean(), mpos=100 * np.mean([v > 0 for v in months.values()]))

def fmt(s):
    return (f"n={s['n']:3d} w={s['win']:3.0f}% {s['y']:+8,.0f} ({s['i']:+,.0f}/{s['o']:+,.0f}) "
            f"avg {s['avg']:+5.0f} dd {s['dd']:,.0f} m+ {s['mpos']:.0f}%")

class Features:
    """Daily and 4-hour bars folded from the 1-minute index series."""
    def __init__(self, IX):
        self.IX, self.D, self.K, self.H4 = IX, {}, {}, {}
        for n, ix in IX.items():
            D = {}
            for r in ix.rows:
                d = D.setdefault(r[0][:10], [r[1], r[2], r[3], r[4]])
                d[1], d[2], d[3] = max(d[1], r[2]), min(d[2], r[3]), r[4]
            self.D[n], self.K[n] = D, sorted(D)
            B, cur = [], None
            for r in ix.rows:
                key = r[0][:10] + ("A" if r[0][11:16] < "13:15" else "B")
                if cur is None or cur[0] != key:
                    if cur: B.append(cur)
                    cur = [key, r[1], r[2], r[3], r[4], r[0]]
                else:
                    cur[2], cur[3], cur[4], cur[5] = max(cur[2], r[2]), min(cur[3], r[3]), r[4], r[0]
            if cur: B.append(cur)
            self.H4[n] = B

    def h4_ret(self, t, N=5):
        bars = [b for b in self.H4[t["u"]] if b[5] < self.IX[t["u"]].rows[t["i"]][0]]
        return int(np.sign(bars[-1][4] - bars[-1 - N][4])) if len(bars) > N else 0

    def d_ret(self, t, N):
        keys = self.K[t["u"]]; k = keys.index(t["day"])
        return int(np.sign(self.D[t["u"]][keys[k - 1]][3] - self.D[t["u"]][keys[k - 1 - N]][3])) if k > N else 0
