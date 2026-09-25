#!/usr/bin/env python3
"""Screen entry families on a year of index history before building any of them.

    python tools/scan_signals.py [theta_atr_per_30min]

Fifteen families (opening-range break/fade, gap go/fade, PDH/PDL break/reject,
60-minute momentum, 15-minute burst/reversal, NR7, trend day, trend pullback,
TWAP reclaim, 5-minute EMA cross). Each event is scored by the index move in its
direction after 15/30/60 minutes, net of fees+spread at a 0.5 delta, in ATR
units, in-sample vs held-out. Results as of 25 Sep 2026: research/PAPERS.md 0f.
"""
import json, os, sys, numpy as np
from collections import defaultdict
S = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
HMAX = 90
def load(u):
    D = defaultdict(list)
    for x in json.load(open(f"{S}/history/{u}.json")):
        if "09:15" <= x[0][11:16] <= "15:29": D[x[0][:10]].append(x)
    return {k: v for k, v in D.items() if len(v) >= 300}
def mins(iso): return int(iso[11:13]) * 60 + int(iso[14:16])
def days(u):
    D = load(u); keys = sorted(D); out = []
    for n, k in enumerate(keys):
        r = D[k]; o = np.array([x[1] for x in r]); h = np.array([x[2] for x in r]); l = np.array([x[3] for x in r]); c = np.array([x[4] for x in r])
        m = np.array([mins(x[0]) for x in r])
        prev = [D[keys[j]] for j in range(max(0, n - 10), n)]
        if len(prev) < 5: continue
        adr = np.mean([max(x[2] for x in p) - min(x[3] for x in p) for p in prev])
        p = prev[-1]; pdh, pdl = max(x[2] for x in p), min(x[3] for x in p)
        pc = [x for x in p if x[0][11:16] <= "15:14"][-1][4]
        fh = [max(x[2] for x in q if x[0][11:16] < "10:15") - min(x[3] for x in q if x[0][11:16] < "10:15") for q in prev[-7:]]
        tr = np.r_[h[0] - l[0], np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
        out.append(dict(day=k, o=o, h=h, l=l, c=c, m=m, adr=adr, pdh=pdh, pdl=pdl, pc=pc, fh7=fh, tr=tr))
    return out
def path(d, t, side):
    end = min(len(d["c"]) - 1, t + HMAX, int(np.searchsorted(d["m"], 910)))
    if end <= t: return None
    e = d["c"][t]
    if side > 0: hi, lo = d["h"][t + 1:end + 1] - e, d["l"][t + 1:end + 1] - e
    else: hi, lo = e - d["l"][t + 1:end + 1], e - d["h"][t + 1:end + 1]
    cl = (d["c"][t + 1:end + 1] - e) * side
    return hi.astype(np.float32), lo.astype(np.float32), cl.astype(np.float32)
def idx_at(d, hhmm):
    j = int(np.searchsorted(d["m"], hhmm)); return j if j < len(d["m"]) and d["m"][j] == hhmm else None
def signals(d):
    """yield (family, param, t, side)"""
    o, h, l, c, m, adr, tr = d["o"], d["h"], d["l"], d["c"], d["m"], d["adr"], d["tr"]
    gap = (o[0] - d["pc"]) / adr
    atr = np.convolve(tr, np.ones(20) / 20, mode="full")[:len(tr)]
    for ib in (15, 30, 60):
        e = idx_at(d, 555 + ib)
        if e is None: continue
        H, L = h[:e].max(), l[:e].min()
        broke = None
        for t in range(e, len(c)):
            if m[t] > 690: break
            if c[t] > H: broke = (t, 1); break
            if c[t] < L: broke = (t, -1); break
        if broke:
            yield ("orb_break", ib, broke[0], broke[1])
            t0, sd = broke
            for t in range(t0 + 1, min(t0 + 20, len(c))):
                if (sd > 0 and c[t] < H) or (sd < 0 and c[t] > L):
                    yield ("orb_fail_fade", ib, t, -sd); break
    for k in (0.3, 0.6):
        if abs(gap) >= k:
            g = np.sign(gap); t = idx_at(d, 584)
            if t is not None:
                if (c[t] - o[0]) * g > 0: yield ("gap_go", k, t, int(g))
                else: yield ("gap_fade", k, t, int(-g))
    for t in range(15, len(c)):
        if m[t] > 870: break
        if c[t] > d["pdh"] and c[t - 1] <= d["pdh"]: yield ("pdh_pdl_break", 0, t, 1); break
        if c[t] < d["pdl"] and c[t - 1] >= d["pdl"]: yield ("pdh_pdl_break", 0, t, -1); break
    for t in range(15, len(c)):
        if m[t] > 870: break
        if h[t] > d["pdh"] and c[t] < d["pdh"] and c[t - 1] < d["pdh"]: yield ("pdh_pdl_reject", 0, t, -1); break
        if l[t] < d["pdl"] and c[t] > d["pdl"] and c[t - 1] > d["pdl"]: yield ("pdh_pdl_reject", 0, t, 1); break
    for hh in (615, 675, 735, 795, 855):
        t = idx_at(d, hh)
        if t is None or t < 60: continue
        r = c[t] - c[t - 60]
        for k in (0.2, 0.4):
            if abs(r) >= k * adr: yield ("mom60", (hh, k), t, int(np.sign(r)))
    r15 = np.r_[np.zeros(15), c[15:] - c[:-15]]
    sd15 = adr * 0.12
    last = -99
    for t in range(30, len(c)):
        if m[t] > 870: break
        if abs(r15[t]) > 2.0 * sd15 and t - last > 30:
            last = t; yield ("rev15", 2.0, t, int(-np.sign(r15[t])))
            yield ("burst15", 2.0, t, int(np.sign(r15[t])))
    e = idx_at(d, 615)
    if e is not None and len(d["fh7"]) >= 7:
        fr = h[:e].max() - l[:e].min()
        if fr <= min(d["fh7"]):
            H, L = h[:e].max(), l[:e].min()
            for t in range(e, len(c)):
                if m[t] > 780: break
                if c[t] > H: yield ("nr7_break", 0, t, 1); break
                if c[t] < L: yield ("nr7_break", 0, t, -1); break
    for hh in (660, 720, 780):
        t = idx_at(d, hh)
        if t is None: continue
        mv = (c[t] - o[0]) / adr; rng = h[:t + 1].max() - l[:t + 1].min()
        if abs(mv) >= 0.6:
            s = int(np.sign(mv)); ext = h[:t + 1].max() if s > 0 else l[:t + 1].min()
            if abs(ext - c[t]) <= 0.2 * rng: yield ("trend_day", hh, t, s)
            else: yield ("trend_day_pullback", hh, t, s)
    tw = np.cumsum((h + l + c) / 3) / np.arange(1, len(c) + 1)
    below = 0; above = 0
    for t in range(30, len(c)):
        if m[t] > 870: break
        above = above + 1 if c[t] > tw[t] else 0; below = below + 1 if c[t] < tw[t] else 0
        if above == 1 and c[t - 1] < tw[t - 1] and all(c[t - 30:t] < tw[t - 30:t]): yield ("twap_reclaim", 0, t, 1)
        if below == 1 and c[t - 1] > tw[t - 1] and all(c[t - 30:t] > tw[t - 30:t]): yield ("twap_reclaim", 0, t, -1)
    for t in range(30, len(c)):
        if m[t] < 600 or m[t] > 870: continue
        mv = c[t] - o[0]
        if abs(mv) < 0.5 * adr: continue
        s = int(np.sign(mv)); hi, lo = h[:t].max(), l[:t].min(); rng = hi - lo
        ret = (hi - c[t - 1]) / rng if s > 0 else (c[t - 1] - lo) / rng
        if 0.38 <= ret <= 0.62 and (c[t] - o[t]) * s > 0.5 * atr[t] and (c[t] - c[t - 1]) * s > 0:
            yield ("trend_pullback", 0, t, s); break
    e5 = c[::5]
    if len(e5) > 30:
        def ema(x, n):
            a = 2 / (n + 1); y = np.empty_like(x); y[0] = x[0]
            for i in range(1, len(x)): y[i] = a * x[i] + (1 - a) * y[i - 1]
            return y
        f, s_ = ema(e5, 9), ema(e5, 21)
        for i in range(22, len(e5)):
            t = i * 5
            if t >= len(c) or m[t] > 870: break
            if f[i] > s_[i] and f[i - 1] <= s_[i - 1]: yield ("ema5m_cross", 0, t, 1)
            if f[i] < s_[i] and f[i - 1] >= s_[i - 1]: yield ("ema5m_cross", 0, t, -1)
def events():
    ev = []
    for u in ("NIFTY", "BANKNIFTY", "SENSEX"):
        for d in days(u):
            for fam, prm, t, side in signals(d):
                p = path(d, t, side)
                if p is not None:
                    ev.append(dict(u=u, day=d["day"], m=int(d["m"][t]), fam=fam, prm=prm, side=side,
                                   atr=float(np.mean(d["tr"][max(0, t - 19):t + 1])), hi=p[0], lo=p[1], cl=p[2]))
    return ev
COST = {"NIFTY": 3.54, "BANKNIFTY": 9.0, "SENSEX": 13.5}; SPLIT = "2026-05-25"
THETA = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0     # extra cost, in ATR units per 30 min
ev = events()
G = defaultdict(list)
for e in ev: G[(e["fam"], str(e["prm"]))].append(e)
print(f"{'family':20s} {'param':12s} H  | pooled net move in ATR units: IN (n, t) / OUT (n, t) | indices + in both")
rows = []
for (fam, prm), es in sorted(G.items()):
    for H in (15, 30, 60):
        def net(e):
            k = min(H, len(e["cl"])) - 1
            return (e["cl"][k] - COST[e["u"]]) / e["atr"] - THETA * H / 30
        a = np.array([net(e) for e in es if e["day"] < SPLIT]); b = np.array([net(e) for e in es if e["day"] >= SPLIT])
        if len(a) < 20 or len(b) < 10: continue
        ok = 0
        for u in COST:
            ai = [net(e) for e in es if e["u"] == u and e["day"] < SPLIT]; bi = [net(e) for e in es if e["u"] == u and e["day"] >= SPLIT]
            ok += bool(ai and bi and np.mean(ai) > 0 and np.mean(bi) > 0)
        ta, tb = a.mean() / a.std() * len(a) ** .5, b.mean() / b.std() * len(b) ** .5
        rows.append((fam, prm, H, a.mean(), len(a), ta, b.mean(), len(b), tb, ok))
for r in rows:
    flag = "  <== both +" if r[3] > 0 and r[6] > 0 else ""
    print(f"{r[0]:20s} {r[1]:12s} {r[2]:2d} | {r[3]:+.2f} ({r[4]:4d}, t={r[5]:+.1f}) / {r[6]:+.2f} ({r[7]:4d}, t={r[8]:+.1f}) | {r[9]}/3{flag}")
