#!/usr/bin/env python3
"""The owner's multi-timeframe model (7 Oct 2026) on a year of real 1-minute
index data (NIFTY, BANKNIFTY, SENSEX; split 2026-05-25):

  DIRECTION     daily (1D) and 4h trend + DAILY LIQUIDITY: the previous K days'
                highs/lows that are still unswept (not traded through by any
                later day, nor yet today). Bias = toward the nearest unswept
                level in the trend direction, or reversal after a daily-level
                sweep that a 5/15m candle closes back inside.
  CONFIRMATION  a 5m or 15m BOS/CHoCH in the bias direction (arb/smc.py's
                swing walk on clock-resampled bars), its order block, and for
                'disp' a displacement candle + FVG inside the impulse.
  ENTRY         'break' = the 5/15m break bar's close; 'retest' = price comes
                back into the OB/FVG zone and a 1m bar closes up through the
                previous 1m high (mirror for shorts), within 60/120 min.
  STOP          the OB edge (or the low since the touch) - 0.1 ATR; refused if
                tighter than min_stop_atr x ATR20(1m), as arb/intraday.py does.
  TARGET        liq = the unswept daily level (- 0.1 ATR; must be >= 1R away),
                2R, 3R, or a 3-ATR chandelier trail; squareoff 15:14,
                no entry after 15:10.

Every trade is priced by research/lab/realistic.py:reprice (the contract the
engine would buy, costs inside the 8% cap, next expiry on expiry day) at
Rs 10,743, and run through research/fixes_test.run(..., side_cooldown=30):
one position at a time across indices, F4, the live daily halt.

Also: the 1D/4h bias + daily-liquidity draw as a FILTER on the live OB trades,
on top of and instead of the 4h-trend filter.

A variant PASSES only if positive in both halves alone and, added to the OB +
LHM book, better than that book in both halves, with every one-parameter
neighbour (K, dist) in its family better in both halves too.

    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/mtf_test.py > research/mtf_result.txt
"""
import os, sys, pickle, time, math
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
OUT = os.path.join(ROOT, "research_out", "mtf_1007")
PKL = os.path.join(ROOT, "research_out", "sahil_1006", "cands.pkl")

import screen as S                                   # noqa: E402
import realistic as rz                               # noqa: E402
from common import Features, gate_halves, summ, SPLIT  # noqa: E402
from arb.config import IntradayLimits                # noqa: E402
from arb import smc                                  # noqa: E402
import sahil_rules_test as SR                        # noqa: E402
from fixes_test import priced, run as fx_run        # noqa: E402

L = IntradayLimits.load(); rz.L = L
CAPITAL = 10743.0
OLD_CAPITAL = 10672.74
IDX = ["NIFTY", "BANKNIFTY", "SENSEX"]
T = SR.T
FIRST, ENTRY_LAST, LAST = T("09:30"), T("15:10"), T("15:14")
KS, DISTS = (2, 3, 5), (0.5, 1.0, 2.0)
MIN_N = 20
cur = dict(L.atr_by_index)


def pace_ok(t):
    return gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))


_RC = {}


def reprice_all(C, IX, cap=CAPITAL, slip=None):
    ss, sf = slip or (L.exit_slip_spreads, L.exit_slip_frac)
    out = []
    for t in C:
        key = (cap, ss, sf, t["u"], t["i"], t["j"], t["side"], round(t["risk"], 6), round(t["net"], 6))
        if key not in _RC:
            _RC[key] = rz.reprice(t, IX[t["u"]], cap, next_expiry_on_expiry_day=True, costs_inside=True,
                                  slip_spreads=ss, slip_frac=sf)
        r = _RC[key]
        if r:
            u = dict(t); u.update(r); out.append(u)
    return out


# ----------------------------------------------------------------------------
# per-index context: daily liquidity, 4h / 1D trend, 5/15m structure setups
# ----------------------------------------------------------------------------
class Ctx:
    def __init__(self, u, ix, F):
        self.u, self.ix = u, ix
        P = self.P = SR.prep(ix)
        iso = P["iso"]; n = len(iso)
        self.n = n
        self.o, self.h, self.l, self.c, self.atr = P["o"], P["h"], P["l"], P["c"], P["atr"]
        self.mins = np.array([SR.hm(s) for s in iso])
        keys = self.keys = P["keys"]
        self.st = np.array([P["D"][d]["idx"][0] for d in keys])
        self.en = np.array([P["D"][d]["idx"][-1] for d in keys])
        self.dk = np.empty(n, int)
        for k in range(len(keys)):
            self.dk[self.st[k]:self.en[k] + 1] = k
        self.DH = np.array([self.h[self.st[k]:self.en[k] + 1].max() for k in range(len(keys))])
        self.DL = np.array([self.l[self.st[k]:self.en[k] + 1].min() for k in range(len(keys))])
        self.DC = np.array([self.c[self.en[k]] for k in range(len(keys))])
        self.adr = np.array([P["D"][d]["adr"] for d in keys])
        self.hs = np.empty(n); self.ls = np.empty(n); self.ex = np.empty(n, int)
        for k in range(len(keys)):
            a, b = self.st[k], self.en[k] + 1
            self.hs[a:b] = np.maximum.accumulate(self.h[a:b])
            self.ls[a:b] = np.minimum.accumulate(self.l[a:b])
            ok = np.nonzero(self.mins[a:b] < LAST)[0]
            self.ex[a:b] = a + (ok[-1] if len(ok) else b - a - 1)
        B = F.H4[u]
        last = np.array([b[5] for b in B]); cl = np.array([b[4] for b in B])
        nb = np.searchsorted(last, np.array(iso), side="left")
        self.h4 = np.zeros(n, int)
        v = nb > 5
        self.h4[v] = np.sign(cl[nb[v] - 1] - cl[nb[v] - 6]).astype(int)
        self._lv, self._sw, self._bias, self._setups, self._tf = {}, {}, {}, {}, {}

    def d1(self, N):
        k = self.dk; out = np.zeros(self.n, int); v = k > N
        out[v] = np.sign(self.DC[k[v] - 1] - self.DC[k[v] - 1 - N]).astype(int)
        return out

    def open_levels(self, k, K):
        """Previous-day highs / lows (lag <= K) not traded through by any later
        day before day k: the unswept daily liquidity at day k's open."""
        his, los = [], []
        for lag in range(1, K + 1):
            p = k - lag
            if p < 0:
                break
            later = slice(p + 1, k)
            if p + 1 >= k or self.DH[p] > self.DH[later].max():
                his.append(self.DH[p])
            if p + 1 >= k or self.DL[p] < self.DL[later].min():
                los.append(self.DL[p])
        return his, los

    def levels(self, K):
        """up[i] / dn[i]: nearest unswept daily high above / low below, with
        today's high / low through bar i (inclusive) taken into account."""
        if K in self._lv:
            return self._lv[K]
        up = np.full(self.n, np.nan); dn = np.full(self.n, np.nan)
        for k in range(1, len(self.keys)):
            a, b = self.st[k], self.en[k] + 1
            his, los = self.open_levels(k, K)
            for H in his:
                up[a:b] = np.where(self.hs[a:b] < H, np.fmin(up[a:b], H), up[a:b])
            for Lw in los:
                dn[a:b] = np.where(self.ls[a:b] > Lw, np.fmax(dn[a:b], Lw), dn[a:b])
        self._lv[K] = (up, dn)
        return up, dn

    def sweeps(self, K, tf):
        """Reversal bias after a daily-level sweep: a bar trades through an
        unswept previous-day high (low) and, within the sweep's tf bucket or
        the next two, a tf candle closes back below (above) it -> short (long)
        from that close until a tf close beyond the sweep extreme."""
        key = (K, tf)
        if key in self._sw:
            return self._sw[key]
        sw = np.zeros(self.n, int); ev = []
        for k in range(1, len(self.keys)):
            a, b = self.st[k], self.en[k] + 1
            ends = np.arange(a, b)[((self.mins[a:b] - 555 + 1) % tf) == 0]
            his, los = self.open_levels(k, K)
            for lv, side in [(H, -1) for H in his] + [(Lw, 1) for Lw in los]:
                hit = (self.h[a:b] > lv) if side < 0 else (self.l[a:b] < lv)
                if not hit.any():
                    continue
                s = a + int(np.argmax(hit))
                e_after = ends[ends >= s]
                rec = None
                for e in e_after[:3]:
                    if (side < 0 and self.c[e] < lv) or (side > 0 and self.c[e] > lv):
                        rec = e; break
                if rec is None:
                    continue
                ext = self.h[s:rec + 1].max() if side < 0 else self.l[s:rec + 1].min()
                stop_at = b - 1
                for e2 in ends[ends > rec]:
                    if (side < 0 and self.c[e2] > ext) or (side > 0 and self.c[e2] < ext):
                        stop_at = e2 - 1; break
                ev.append((rec, stop_at, side))
        for rec, stop_at, side in sorted(ev):
            sw[rec:stop_at + 1] = side
        self._sw[key] = sw
        return sw

    def bias(self, mode, K, dist, tf):
        key = (mode, K, dist, tf if mode.startswith("SWEEP") else 0)
        if key in self._bias:
            return self._bias[key]
        up, dn = self.levels(K); c = self.c
        lim = dist * self.adr[self.dk]
        okU = ~np.isnan(up) & (np.nan_to_num(up, nan=1e18) - c <= lim) & (lim > 0)
        okD = ~np.isnan(dn) & (c - np.nan_to_num(dn, nan=-1e18) <= lim) & (lim > 0)
        def toward(s):
            return np.where((s > 0) & okU, 1, np.where((s < 0) & okD, -1, 0))
        if mode == "H4draw":
            out = toward(self.h4)
        elif mode == "D1draw":
            out = toward(self.d1(3))
        elif mode == "D1H4draw":
            d = self.d1(3); out = toward(np.where(d == self.h4, d, 0))
        elif mode in ("NEAR", "NEAR4h"):
            du = np.where(np.isnan(up), np.inf, up - c); dd = np.where(np.isnan(dn), np.inf, c - dn)
            out = np.where((du < dd) & okU, 1, np.where((dd < du) & okD, -1, 0))
            if mode == "NEAR4h":
                out = np.where(out == self.h4, out, 0)
        elif mode == "SWEEPcont":                         # placebo: WITH the sweep (continuation)
            out = toward(-self.sweeps(K, tf))
        elif mode in ("SWEEP", "SWEEP4h"):
            out = toward(self.sweeps(K, tf))
            if mode == "SWEEP4h":
                out = np.where(out == self.h4, out, 0)
        else:
            raise ValueError(mode)
        self._bias[key] = out
        return out

    def tf_bars(self, tf):
        """Clock-bucketed tf candles (09:15-aligned, complete buckets only) and
        the 1m index of each bucket's last minute."""
        if tf in self._tf:
            return self._tf[tf]
        rows = self.ix.rows; cand, e1 = [], []
        for k in range(len(self.keys)):
            a, b = self.st[k], self.en[k] + 1
            bk = (self.mins[a:b] - 555) // tf
            for q in np.unique(bk):
                idx = a + np.nonzero(bk == q)[0]
                if len(idx) < tf:
                    continue
                cand.append([rows[idx[0]][0], self.o[idx[0]], self.h[idx].max(), self.l[idx].min(),
                             self.c[idx[-1]], 0.0])
                e1.append(int(idx[-1]))
        self._tf[tf] = (cand, np.array(e1))
        return self._tf[tf]

    def events(self, tf):
        """arb/smc.py:analyse's BOS / CHoCH walk, run once over the whole tf
        series (causal: a swing counts only once `strength` bars confirm it)."""
        cand, _ = self.tf_bars(tf)
        strength = L.swing_strength
        highs, lows = smc._swings(cand, strength)
        trend, last_event, event_at, out = 0, "", -1, []
        hi_i = lo_i = 0
        for i in range(min(highs[0], lows[0]) + 1, len(cand)):
            close = cand[i][4]
            while hi_i < len(highs) and highs[hi_i] < i - strength:
                hi_i += 1
            while lo_i < len(lows) and lows[lo_i] < i - strength:
                lo_i += 1
            ph = cand[highs[hi_i - 1]][2] if hi_i > 0 else None
            pl = cand[lows[lo_i - 1]][3] if lo_i > 0 else None
            if ph is not None and close > ph:
                ev = "BOS_UP" if trend in (1, 0) else "CHOCH_UP"
                if ev != last_event or i - event_at > strength:
                    last_event, event_at, trend = ev, i, 1; out.append((i, ev, ph))
            elif pl is not None and close < pl:
                ev = "BOS_DOWN" if trend in (-1, 0) else "CHOCH_DOWN"
                if ev != last_event or i - event_at > strength:
                    last_event, event_at, trend = ev, i, -1; out.append((i, ev, pl))
        return out

    def setups(self, tf, conf, entry):
        """[(ie, j, side, stop)]: ie = 1m bar the tf break closes on, j = entry bar."""
        key = (tf, conf, entry)
        if key in self._setups:
            return self._setups[key]
        cand, e1 = self.tf_bars(tf)
        o, h, l, c, atr, mins = self.o, self.h, self.l, self.c, self.atr, self.mins
        W = 60 if tf == 5 else 120
        seen, out = set(), []
        for b, name, _lvl in self.events(tf):
            side = 1 if name.endswith("_UP") else -1
            ob = smc._order_block(cand, b, side)
            if ob is None or (side, ob.at) in seen:
                continue
            seen.add((side, ob.at))
            ie = int(e1[b])
            if not (FIRST <= mins[ie] + 1 <= ENTRY_LAST) or atr[ie] <= 0:
                continue
            if cand[ob.at][0][:10] != cand[b][0][:10]:
                continue                                   # OB from an earlier session
            zl, zh = ob.low, ob.high
            if conf == "disp":
                rng = [cand[q][2] - cand[q][3] for q in range(max(0, b - 14), b)]
                avg = float(np.mean(rng)) if rng else 0.0
                imp = range(ob.at + 1, b + 1)
                big = max(cand[q][2] - cand[q][3] for q in imp) if len(imp) else 0.0
                fvg = None
                for m in range(ob.at + 1, b):
                    if side > 0 and cand[m][4] > cand[m][1] and cand[m - 1][2] < cand[m + 1][3]:
                        fvg = (cand[m - 1][2], cand[m + 1][3])
                    if side < 0 and cand[m][4] < cand[m][1] and cand[m - 1][3] > cand[m + 1][2]:
                        fvg = (cand[m + 1][2], cand[m - 1][3])
                if fvg is None or avg <= 0 or big < 1.5 * avg:
                    continue
                zl, zh = min(zl, fvg[0]), max(zh, fvg[1])
            if entry == "break":
                j = ie
                stop = (zl if side > 0 else zh) - side * 0.1 * atr[j]
            else:
                j, touched, day_end = None, None, self.en[self.dk[ie]]
                for q in range(ie + 1, min(ie + W, day_end) + 1):
                    if mins[q] + 1 > ENTRY_LAST:
                        break
                    if (side > 0 and c[q] < zl) or (side < 0 and c[q] > zh):
                        break                               # zone failed
                    if touched is None and ((side > 0 and l[q] <= zh) or (side < 0 and h[q] >= zl)):
                        touched = q
                    if touched is not None and ((side > 0 and c[q] > o[q] and c[q] > h[q - 1]) or
                                                (side < 0 and c[q] < o[q] and c[q] < l[q - 1])):
                        j = q; break
                if j is None:
                    continue
                if side > 0:
                    stop = min(l[touched:j + 1].min(), zl) - 0.1 * atr[j]
                else:
                    stop = max(h[touched:j + 1].max(), zh) + 0.1 * atr[j]
            risk = (c[j] - stop) * side
            if risk <= 0 or risk < L.min_stop_atr * atr[j]:
                continue                                    # the engine refuses this stop
            out.append((ie, j, side, stop))
        self._setups[key] = out
        return out

    def walk(self, i, side, stop, tgt):
        """Index exit: stop (gap-through at the open; checked first), target,
        squareoff at the close of the 15:13 bar (SR.walk with last = 15:14)."""
        e = self.ex[i]
        if e <= i:
            return i, self.c[i], "none"
        h, l = self.h[i + 1:e + 1], self.l[i + 1:e + 1]
        sm = (l <= stop) if side > 0 else (h >= stop)
        tm = ((h >= tgt) if side > 0 else (l <= tgt)) if tgt is not None else np.zeros(len(h), bool)
        js = int(np.argmax(sm)) if sm.any() else 10**9
        jt = int(np.argmax(tm)) if tm.any() else 10**9
        if js == 10**9 and jt == 10**9:
            return e, self.c[e], "squareoff"
        if js <= jt:
            j = i + 1 + js
            return j, (min(self.o[j], stop) if side > 0 else max(self.o[j], stop)), "stop"
        j = i + 1 + jt
        return j, (max(self.o[j], tgt) if side > 0 else min(self.o[j], tgt)), "target"


# ----------------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------------
SESS = None
BASE = None
ROWS = []


def stat(tr):
    s = summ(tr)
    return dict(n=s["n"], win=s["win"], i=s["i"], o=s["o"], y=s["y"], dd=s["dd"],
                ps=len(tr) / SESS, pd=100.0 * len({t["day"] for t in tr}) / SESS)


def f1(s):
    return (f"n={s['n']:4d} w={s['win']:3.0f}% IN {s['i']:+7,.0f} OUT {s['o']:+7,.0f} tot {s['y']:+7,.0f} "
            f"DD {s['dd']:6,.0f} {s['ps']:.2f}/s {s['pd']:3.0f}%d")


def better(s):
    return s["i"] > BASE["i"] and s["o"] > BASE["o"]


def rec(family, label, params, alone, book):
    a = stat(alone) if alone is not None else None
    b = stat(book)
    if not ROWS or ROWS[-1][0] != family:
        print(f"-- {family}")
    ROWS.append((family, label, params, a, b))
    flag = "  BETTER BOTH" if better(b) else ""
    pos = "" if a is None else ("  alone+both" if a["i"] > 0 and a["o"] > 0 else "")
    left = f"alone {f1(a)} | " if a is not None else ""
    print(f"{label:34s} {left}+book {f1(b)} dIN {b['i'] - BASE['i']:+6,.0f} dOUT {b['o'] - BASE['o']:+6,.0f}"
          f"{flag}{pos}", flush=True)


def neighbours(family, params):
    fam = [(p, b) for f, _, p, a, b in ROWS if f == family]
    out = []
    for p, b in fam:
        diff = [k for k in params if p.get(k) != params.get(k)]
        if len(diff) != 1:
            continue
        k = diff[0]
        vals = sorted({q[k] for q, _ in fam})
        if abs(vals.index(params[k]) - vals.index(p[k])) == 1:
            out.append(b)
    return out


def plateau():
    print("\n" + "=" * 150)
    print("PLATEAU CHECK.  BOTH = +book better than baseline IN and OUT.  PASS = alone positive in both halves, "
          "BOTH, and every one-parameter neighbour (K / dist / N) BOTH too.  Only rows that are BOTH are listed.")
    print("=" * 150)
    passed, nboth = [], 0
    for f, lab, p, a, b in ROWS:
        if not better(b):
            continue
        nboth += 1
        nb = neighbours(f, p)
        alone_ok = a is None or (a["i"] > 0 and a["o"] > 0)
        big = a is None or a["n"] >= MIN_N
        ok = alone_ok and big and nb and all(better(x) for x in nb)
        tag = "PASS" if ok else ("alone not +/+" if not alone_ok else (f"n < {MIN_N}" if not big else "no plateau"))
        if ok:
            passed.append((f, lab))
        print(f"  [{f}] {lab:34s} +book {f1(b)}  [{sum(better(x) for x in nb)}/{len(nb)} nb BOTH] {tag}")
    print(f"\n{nboth} of {len(ROWS)} variants beat the baseline in both halves.")
    print("PASSING VARIANTS:", passed or "none")
    return passed


def gen(CX, IX, mode, tf, conf, entry, tg, K, dist, delay=0, slip=None, only=None):
    """MTF candidates for one variant, priced. delay: enter `delay` 1m bars
    after the signal bar (latency check); slip: (spreads, frac) exit slip;
    only: restrict to these indices."""
    C = []
    for u in (only or IDX):
        cx = CX[u]; side_arr = cx.bias(mode, K, dist, tf); up, dn = cx.levels(K)
        for ie, j, side, stop in cx.setups(tf, conf, entry):
            if side_arr[ie] != side or side_arr[j] != side:
                continue
            if delay:
                j0, j = j, j + delay
                if j >= cx.n or cx.dk[j] != cx.dk[j0] or cx.mins[j] + 1 > ENTRY_LAST:
                    continue
                if (side > 0 and cx.l[j0 + 1:j + 1].min() <= stop) or (side < 0 and cx.h[j0 + 1:j + 1].max() >= stop):
                    continue
                if (cx.c[j] - stop) * side < L.min_stop_atr * cx.atr[j]:
                    continue
            c0 = cx.c[j]; risk = (c0 - stop) * side
            if tg == "trail":
                jx, px, why = S.exit_walk(IX[u].rows, cx.P, j, side, stop, dict(trail="chand", k=3.0),
                                          cx.atr[j], last=LAST)
            else:
                if tg == "liq":
                    lv = up[j] if side > 0 else dn[j]
                    tgt = lv - side * 0.1 * cx.atr[j]
                    if (tgt - c0) * side < 1.0 * risk:
                        continue
                else:
                    tgt = c0 + side * float(tg[0]) * risk
                jx, px, why = cx.walk(j, side, stop, tgt)
            if jx <= j:
                continue
            t = S.cand(u, IX[u], j, jx, px, side, risk, why, "MTF")
            t["strat"] = "MTF"; t["ie"] = ie
            C.append(t)
    seen, U_ = set(), []
    for t in C:
        kk = (t["u"], t["i"], t["side"])
        if kk not in seen:
            seen.add(kk); U_.append(t)
    return reprice_all(U_, IX, slip=slip)


# ----------------------------------------------------------------------------
def main():
    global SESS, BASE
    t0 = time.time()
    K_ = pickle.load(open(PKL, "rb"))
    assert abs(K_["capital"] - OLD_CAPITAL) < 1e-6 and K_["risk"] == L.max_risk_pct_per_trade
    IX = {n: rz.Index(n) for n in IDX}
    F = Features(IX)
    CX = {u: Ctx(u, IX[u], F) for u in IDX}
    SESS = len(CX["NIFTY"].keys)
    os.makedirs(OUT, exist_ok=True)

    # ---- the book: tie-out at the 6 Oct capital, then rebuilt at Rs 10,743
    tie = stat(fx_run(K_["OB"] + K_["LHM"], side_cooldown=30))
    ALL = priced(K_["C"], IX, CAPITAL, True)
    PACE = [dict(t, strat="OB") for t in ALL if pace_ok(t)]
    OB = [t for t in PACE if F.h4_ret(t, 5) * t["side"] > 0]
    LHM = [dict(t, strat="LHM") for t in reprice_all(K_["LHM_RAW"], IX) if F.h4_ret(t, 5) * t["side"] > 0]
    BOOK = OB + LHM
    BASE = stat(fx_run(BOOK, side_cooldown=30))
    print(f"capital Rs {CAPITAL:,.0f}, risk cap {L.max_risk_pct_per_trade:g}% (costs inside), daily halt "
          f"{L.max_daily_loss_pct:g}% of Rs {float(L.capital_ceiling or L.starting_capital):,.0f}, split {SPLIT}, "
          f"sessions {SESS}, min stop {L.min_stop_atr:g} ATR20(1m), squareoff 15:14, entries <= 15:10")
    print(f"tie-out at Rs {OLD_CAPITAL:,.2f} (cached OB {len(K_['OB'])} + LHM {len(K_['LHM'])}): {f1(tie)}  "
          f"[research/portfolio_result.txt: 159 trades, +48,248, IN +27,094 / OUT +21,154, DD 3,649]")
    print(f"rebuilt at Rs {CAPITAL:,.0f}: OB {len(OB)} (pace-ok before 4h: {len(PACE)}), LHM {len(LHM)}")
    print(f"BASELINE OB + LHM  {f1(BASE)}")
    print(f"OB alone           {f1(stat(fx_run(OB, side_cooldown=30)))}")
    print("Columns: n, win%, IN (< split), OUT (>= split), total, max DD, trades/session, % sessions with a trade;"
          " dIN/dOUT = +book minus baseline.\n")

    # ------------------------------------------------------------ 0. unpriced
    print("== 0. Is unswept daily liquidity a draw? (unpriced, index only). At 09:30, nearest unswept previous-day"
          " high above (U) and low below (D), lag <= K days.\n"
          "   Which is touched first by 15:14? Random-walk (gambler's ruin) null: P(U first) = dD / (dU + dD).")
    for K in (1, 2, 3, 5):
        rows = []
        for u in IDX:
            cx = CX[u]; up, dn = cx.levels(K)
            for k in range(6, len(cx.keys)):
                a, b = cx.st[k], cx.en[k] + 1
                i0 = a + int(np.argmax(cx.mins[a:b] >= T("09:29")))
                U, D_ = up[i0], dn[i0]
                if np.isnan(U) or np.isnan(D_):
                    continue
                c0 = cx.c[i0]; du, dd = U - c0, c0 - D_
                e = cx.ex[i0]
                hu = np.nonzero(cx.h[i0 + 1:e + 1] >= U)[0]; hd = np.nonzero(cx.l[i0 + 1:e + 1] <= D_)[0]
                fu = hu[0] if len(hu) else 10**9; fd = hd[0] if len(hd) else 10**9
                res = 0 if fu == fd else (1 if fu < fd else -1)
                rows.append((cx.keys[k] >= SPLIT, du / cx.adr[k], dd / cx.adr[k], res, cx.h4[i0],
                             int(np.sign(cx.DC[k - 1] - cx.DC[k - 4]))))
        R = np.array([(r[1], r[2], r[3], r[4], r[5]) for r in rows])
        if not len(R):
            continue
        res = R[:, 2]; nul = R[:, 1] / (R[:, 0] + R[:, 1])
        m = res != 0
        outh = np.array([r[0] for r in rows])
        print(f"   K={K}: days with both U and D = {len(R)}; resolved {m.sum()} "
              f"(median dU {np.median(R[:, 0]):.2f} ADR, dD {np.median(R[:, 1]):.2f} ADR)")
        print(f"      P(U first) actual {np.mean(res[m] > 0):.3f}  null {np.mean(nul[m]):.3f}")
        for nm, col in (("4h trend", 3), ("1D 3-day trend", 4)):
            tr = R[:, col]; mm = m & (tr != 0)
            hit = np.where(tr[mm] > 0, res[mm] > 0, res[mm] < 0)
            nl = np.where(tr[mm] > 0, nul[mm], 1 - nul[mm])
            oh = outh[mm]
            print(f"      trend-side level first ({nm}): actual {hit.mean():.3f}  null {nl.mean():.3f}  n={mm.sum()}"
                  f"   [IN {hit[~oh].mean():.3f} vs {nl[~oh].mean():.3f} | OUT {hit[oh].mean():.3f} vs {nl[oh].mean():.3f}]")
        near = np.where(R[:, 0] < R[:, 1], 1, -1)
        hitn = np.where(near[m] > 0, res[m] > 0, res[m] < 0)
        nln = np.where(near[m] > 0, nul[m], 1 - nul[m])
        print(f"      nearer level first: actual {hitn.mean():.3f}  null {nln.mean():.3f}")
    print()

    # ------------------------------------------------------------ 1. filters on OB
    print("== 1. FILTERS on the live OB trades (LHM unchanged). draw = an unswept previous-day level (lag <= K) "
          "within dist x ADR in the trade's direction")
    def bias_at(t, mode, K, dist, tf=15):
        return CX[t["u"]].bias(mode, K, dist, tf)[t["i"]]
    def up_dn_ok(t, K, dist):
        cx = CX[t["u"]]; up, dn = cx.levels(K); i = t["i"]; lim = dist * cx.adr[cx.dk[i]]
        lv = up[i] if t["side"] > 0 else dn[i]
        return (not np.isnan(lv)) and abs(lv - cx.c[i]) <= lim
    def near_side(t, K, dist):
        return CX[t["u"]].bias("NEAR", K, dist, 15)[t["i"]]
    for base_set, tag in ((OB, "+4h"), (PACE, "no 4h")):
        fam = f"F draw-exists ({tag})"
        for K in KS:
            for dist in DISTS:
                keep = [t for t in base_set if up_dn_ok(t, K, dist)]
                rec(fam, f"{tag} K{K} d{dist:g} ({len(keep)} OB)", dict(K=K, dist=dist),
                    fx_run(keep, side_cooldown=30), fx_run(keep + LHM, side_cooldown=30))
    for base_set, tag in ((OB, "+4h"), (PACE, "no 4h")):
        fam = f"F nearest-draw side ({tag})"
        for K in KS:
            for dist in DISTS:
                keep = [t for t in base_set if near_side(t, K, dist) == t["side"]]
                rec(fam, f"{tag} K{K} d{dist:g} ({len(keep)} OB)", dict(K=K, dist=dist),
                    fx_run(keep, side_cooldown=30), fx_run(keep + LHM, side_cooldown=30))
    for base_set, tag in ((OB, "+4h"), (PACE, "no 4h")):
        for tf in (5, 15):
            fam = f"F not-into-a-swept-daily-level {tf}m ({tag})"
            for K in KS:
                keep = [t for t in base_set if CX[t["u"]].sweeps(K, tf)[t["i"]] != -t["side"]]
                rec(fam, f"{tag} K{K} ({len(keep)} OB)", dict(K=K),
                    fx_run(keep, side_cooldown=30), fx_run(keep + LHM, side_cooldown=30))
            fam = f"F with-the-sweep-reversal only {tf}m ({tag})"
            for K in KS:
                keep = [t for t in base_set if CX[t["u"]].sweeps(K, tf)[t["i"]] == t["side"]]
                rec(fam, f"{tag} K{K} ({len(keep)} OB)", dict(K=K),
                    fx_run(keep, side_cooldown=30), fx_run(keep + LHM, side_cooldown=30))
    for tag, extra in (("1D replaces 4h", None), ("1D and 4h", "h4")):
        fam = f"F {tag}"
        for N in (2, 3, 5):
            keep = [t for t in PACE if F.d_ret(t, N) * t["side"] > 0 and
                    (extra is None or F.h4_ret(t, 5) * t["side"] > 0)]
            rec(fam, f"{tag} N{N} ({len(keep)} OB)", dict(N=N),
                fx_run(keep, side_cooldown=30), fx_run(keep + LHM, side_cooldown=30))
    print()

    # ------------------------------------------------------------ 2. new source
    print("== 2. NEW SOURCE 'MTF' (bias -> 5/15m BOS/CHoCH + OB [+disp+FVG] -> 1m entry) alone and added to "
          "OB + LHM.\n   bias: H4draw = 4h trend toward an unswept daily level within dist ADR; D1draw = 3-day "
          "trend ditto; D1H4draw = both agree; NEAR = toward the nearer unswept level;\n   NEAR4h = NEAR and "
          "4h agree; SWEEP = reversal after a daily-level sweep reclaimed on the tf close (draw the other side); "
          "SWEEP4h = SWEEP and 4h agree")
    modes = ("H4draw", "D1draw", "D1H4draw", "NEAR", "NEAR4h", "SWEEP", "SWEEP4h")
    tgts = ("liq", "2R", "3R", "trail")
    allc = {}
    for mode in modes:
        for tf in (5, 15):
            for conf in ("bos", "disp"):
                for entry in ("break", "retest"):
                    for tg in tgts:
                        fam = f"{mode} {tf}m {conf} {entry} {tg}"
                        for K in KS:
                            for dist in DISTS:
                                G = gen(CX, IX, mode, tf, conf, entry, tg, K, dist)
                                allc[(fam, K, dist)] = G
                                rec(fam, f"K{K} d{dist:g} [{len(G)} c]", dict(K=K, dist=dist),
                                    fx_run(G, side_cooldown=30), fx_run(BOOK + G, side_cooldown=30))
    print(f"\n(reprice cache {len(_RC)} contracts; {time.time() - t0:.0f} s so far)")

    # ------------------------------------------------------------ 3. post-hoc probe
    print("\n== 3. POST-HOC PROBE (chosen after seeing section 2): the SWEEP families on a finer draw-distance grid "
          "(dist = 0.3..0.75 ADR, and 99 = no draw needed).\n   SWEEPcont = placebo, the same sweeps traded WITH "
          "the sweep (continuation) toward the unswept level beyond it.\n   Section 2 showed SWEEP 5m bos break 2R positive at "
          "dist 0.5 for every K and negative at 1 and 2. A real effect should hold on 0.4-0.6 and fade smoothly.")
    for mode in ("SWEEP", "SWEEP4h", "SWEEPcont"):
        for tf in (5, 15):
            for entry in ("break", "retest"):
                for tg in tgts:
                    if mode == "SWEEPcont" and (tg != "2R" or tf != 5):
                        continue
                    fam = f"probe {mode} {tf}m bos {entry} {tg}"
                    for K in KS:
                        for dist in (0.3, 0.4, 0.5, 0.6, 0.75, 99):
                            G = gen(CX, IX, mode, tf, "bos", entry, tg, K, dist)
                            allc[(fam, K, dist)] = G
                            rec(fam, f"K{K} d{dist:g} [{len(G)} c]", dict(K=K, dist=dist),
                                fx_run(G, side_cooldown=30), fx_run(BOOK + G, side_cooldown=30))

    # ------------------------------------------------------------ 4. the best cell, taken apart
    for fam_, K, dist in (("SWEEP 5m bos break 2R", 3, 0.5), ("SWEEP 5m bos break 2R", 5, 0.5)):
        G = allc[(fam_, K, dist)]
        tr = fx_run(G, side_cooldown=30)
        print(f"\n== 4. {fam_} K{K} d{dist:g} taken apart (alone, {len(tr)} trades)")
        for u in IDX:
            sub = [t for t in tr if t["u"] == u]
            if sub:
                print(f"   {u:10s} {f1(stat(sub))}")
        for sd in (1, -1):
            print(f"   {'long ' if sd > 0 else 'short'}      {f1(stat([t for t in tr if t['side'] == sd]))}")
        mon = {}
        for t in tr:
            mon.setdefault(t["day"][:7], []).append(t["net2"])
        print("   per month: " + "  ".join(f"{m} {len(v)}:{sum(v):+,.0f}" for m, v in sorted(mon.items())))
        why = {}
        for t in tr:
            why[t["why"]] = why.get(t["why"], 0) + 1
        print(f"   exits: {why}")
        srt = sorted(t["net2"] for t in tr)
        print(f"   total without the best 3 trades: {sum(srt[:-3]):+,.0f}; best 3: {[round(x) for x in srt[-3:]]}")
        bk = fx_run(BOOK + G, side_cooldown=30)
        dropped = len([t for t in fx_run(BOOK, side_cooldown=30)]) - len([t for t in bk if t.get("strat") != "MTF"])
        print(f"   in the book: {sum(1 for t in bk if t.get('strat') == 'MTF')} MTF trades taken, "
              f"{dropped} book trades displaced; {f1(stat(bk))}")
    print("\n== FAMILY SUMMARY (sections 1-3): cells alone +/+ (positive IN and OUT), cells whose +book beats the "
          "baseline in both halves, and the K3 / dist-1 (or K3 / N3) cell")
    fams = []
    for f, *_ in ROWS:
        if f not in fams:
            fams.append(f)
    for f in fams:
        rs = [(p, a, b) for g, _, p, a, b in ROWS if g == f]
        pp = sum(1 for p, a, b in rs if a is not None and a["i"] > 0 and a["o"] > 0)
        bb = sum(1 for p, a, b in rs if better(b))
        mid = next(((p, a, b) for p, a, b in rs if p.get("K") == 3 and p.get("dist", 1.0) == 1.0), None) or \
            next(((p, a, b) for p, a, b in rs if p.get("N") == 3), rs[len(rs) // 2])
        p, a, b = mid
        print(f"  {f:46s} +/+ {pp:2d}/{len(rs):2d}  BOTH {bb:2d}/{len(rs):2d} | {p} alone n={a['n']:3d} "
              f"w={a['win']:3.0f}% {a['i']:+7,.0f}/{a['o']:+7,.0f} | +book {b['i']:+7,.0f}/{b['o']:+7,.0f} DD {b['dd']:5,.0f}")
    passed = plateau()

    # ------------------------------------------------------------ 5. robustness of every pass
    print("\n== 5. ROBUSTNESS of every passing cell (alone | +book): harsher exit slip (2 spreads + 2% of premium), "
          "entry 1 and 2 bars late, per index, without SENSEX")
    for fam_, lab in passed:
        mode, tf, conf, entry, tg = fam_.split()[-5:]
        tf = int(tf[:-1])
        p_ = next(p for f, l_, p, a, b in ROWS if f == fam_ and l_ == lab)
        K, dist = p_["K"], p_["dist"]
        print(f"-- {fam_} K{K} d{dist:g}")
        for nm, kw in (("as tested", {}), ("slip 2sp+2%", dict(slip=(2.0, 0.02))), ("entry +1 bar", dict(delay=1)),
                       ("entry +2 bars", dict(delay=2)), ("NIFTY only", dict(only=["NIFTY"])),
                       ("BANKNIFTY only", dict(only=["BANKNIFTY"])), ("SENSEX only", dict(only=["SENSEX"])),
                       ("without SENSEX", dict(only=["NIFTY", "BANKNIFTY"]))):
            G = gen(CX, IX, mode, tf, conf, entry, tg, K, dist, **kw)
            a, b = stat(fx_run(G, side_cooldown=30)), stat(fx_run(BOOK + G, side_cooldown=30))
            print(f"   {nm:16s} alone {f1(a)} | +book IN {b['i']:+7,.0f} OUT {b['o']:+7,.0f} DD {b['dd']:6,.0f}"
                  f"{'  BETTER BOTH' if better(b) else ''}")
    with open(os.path.join(OUT, "rows.pkl"), "wb") as f:
        pickle.dump(dict(rows=ROWS, base=BASE, passed=passed), f)
    with open(os.path.join(OUT, "mtf_cands.pkl"), "wb") as f:
        pickle.dump(allc, f)
    print(f"\nrun time {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
