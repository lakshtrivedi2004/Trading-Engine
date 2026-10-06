#!/usr/bin/env python3
"""Star Sahil's testable rules (research/SCALPER_RESEARCH.md section 3 and 4.1)
against the current best book, OB + LHM (research/portfolio_test.py):

  OB   ob_fvg_retest, the live playbook (playbook_lab candidates, live pace gate)
  LHM  last-half-hour momentum, NIFTY 15:00 -> 15:13

Each rule is tried as a FILTER on that book and/or as a NEW entry source added to
it, priced the same way (realistic.reprice: the contract the engine would buy,
costs inside the cap, next expiry on expiry day), 4h trend with the trade unless
the rule itself sets direction, one position at a time across everything, F4 30
min and the live daily halt (fixes_test.run(..., side_cooldown=30)).

  1. Rule C  NIFTY / BANKNIFTY divergence no-trade filter
  2. Rule H  chop filters (big candle + opposite big candle; big candle + 2 inside bars)
  3. Rule A  gap-day direction lock (filter) and gap-day Fib pocket entry
  4. Rule B  NIFTY / BANKNIFTY divergence (SMT) entry
  5. Rule D  sweep-and-reclaim at PDH/PDL, prev close, round numbers, first-candle
             extremes (+ a NIFTY-constituent volume proxy, Jun-Sep 2026 only)
  6. exits   breakeven at 1R, 80% partial at 2R + trail, daily profit cap = 2 x loss limit

A variant COUNTS only if it beats the baseline in BOTH halves (split 2026-05-25)
and every parameter neighbour in its family does too (plateau). Every variant run
is printed; nothing is dropped.

    # once (heavy, ~minutes): candidates -> research_out/sahil_1006/cands.pkl
    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/sahil_rules_test.py --build
    # every variant (seconds to a few minutes)
    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/sahil_rules_test.py > research/sahil_rules_result.txt
"""
import os, sys, pickle, json, glob, math
from datetime import date
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
OUT = os.path.join(ROOT, "research_out", "sahil_1006")
PKL = os.path.join(OUT, "cands.pkl")

import screen as S                                   # noqa: E402
import realistic as rz                               # noqa: E402
from common import Features, gate_halves, summ, SPLIT  # noqa: E402
from playbook_lab import INDICES                     # noqa: E402
from arb.config import IntradayLimits                # noqa: E402

L = IntradayLimits.load(); rz.L = L
CAPITAL = 10672.74
cur = dict(L.atr_by_index)


def pace_ok(t):
    return gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))


def reprice(C, IX, **kw):
    out = []
    for t in C:
        r = rz.reprice(t, IX[t["u"]], CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                       slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac, **kw)
        if r:
            u = dict(t); u.update(r); out.append(u)
    return out


def lhm_gen(name, ix, P, at="15:00", adr_frac=0.5, minr=1.5, last="15:13"):
    """portfolio_test.lhm_gen, unchanged."""
    rows, out = ix.rows, []; c = P["c"]; atr = P["atr"]
    for d in P["keys"]:
        dd = P["D"][d]
        if not dd["sig"]:
            continue
        for i in dd["idx"]:
            if S.hm(rows[i][0]) + 1 != S.T(at):
                continue
            mv = c[i] - dd["pc"]
            if abs(mv) < adr_frac * dd["adr"]:
                continue
            side = 1 if mv > 0 else -1; a = atr[i]; stop = c[i] - side * minr * a
            j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), a, last=S.T(last))
            out.append(S.cand(name, ix, i, j, px, side, abs(c[i] - stop), why, "LHM"))
    return out


def build():
    from playbook_lab import candidates
    from fixes_test import priced
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 2)
    ALL = priced(C, IX, CAPITAL, True)                 # exactly as portfolio_test
    OB = [dict(t, strat="OB") for t in ALL if F.h4_ret(t, 5) * t["side"] > 0 and pace_ok(t)]
    PP = {"NIFTY": S.prep(IX["NIFTY"])}
    LHM_RAW = lhm_gen("NIFTY", IX["NIFTY"], PP["NIFTY"])
    LHM = [dict(t, strat="LHM") for t in reprice(LHM_RAW, IX) if F.h4_ret(t, 5) * t["side"] > 0]
    os.makedirs(OUT, exist_ok=True)
    with open(PKL, "wb") as f:
        pickle.dump(dict(C=C, ALL=ALL, OB=OB, LHM_RAW=LHM_RAW, LHM=LHM, capital=CAPITAL,
                         risk=L.max_risk_pct_per_trade), f)
    print(f"raw {len(C)} priced {len(ALL)} OB {len(OB)} LHM raw {len(LHM_RAW)} LHM {len(LHM)} -> {PKL}")


if __name__ == "__main__" and "--build" in sys.argv:
    build()
    sys.exit(0)


# ----------------------------------------------------------------------------
# shared plumbing
# ----------------------------------------------------------------------------
from arb.costs import leg_cost                        # noqa: E402
from bsmodel import bs, delta as bs_delta, T_years    # noqa: E402
import backtest as bt                                 # noqa: E402

def hm(iso): return int(iso[11:13]) * 60 + int(iso[14:16])
def T(s): return int(s[:2]) * 60 + int(s[3:])


def prep(ix):
    """Arrays + per-day context for one index (prior-bar ATR20 and ATR14)."""
    rows = ix.rows; n = len(rows)
    o = np.array([r[1] for r in rows]); h = np.array([r[2] for r in rows])
    l = np.array([r[3] for r in rows]); c = np.array([r[4] for r in rows])
    pc_ = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc_), abs(l - pc_)))
    cs = np.concatenate([[0], np.cumsum(tr)])
    atr = np.zeros(n); atr14 = np.zeros(n)
    for i in range(21, n):
        atr[i] = (cs[i] - cs[i - 20]) / 20
        atr14[i] = (cs[i] - cs[i - 14]) / 14
    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r[0][:10], []).append(i)
    keys = sorted(days); D = {}
    for k, d in enumerate(keys):
        idx = days[d]
        prev = days[keys[k - 1]] if k else None
        D[d] = dict(idx=idx, o=o[idx[0]], pc=c[prev[-1]] if prev else o[idx[0]],
                    pdh=h[prev].max() if prev else None, pdl=l[prev].min() if prev else None,
                    adr=np.mean([h[days[p]].max() - l[days[p]].min() for p in keys[max(0, k - 10):k]]) if k else 0.0)
    pos = {r[0]: i for i, r in enumerate(rows)}
    lo_b = np.full(n, np.nan); hi_b = np.full(n, np.nan)    # day low / high BEFORE bar i
    for d in keys:
        idx = days[d]
        lo_b[idx[1:]] = np.minimum.accumulate(l[idx])[:-1]
        hi_b[idx[1:]] = np.maximum.accumulate(h[idx])[:-1]
    return dict(o=o, h=h, l=l, c=c, atr=atr, atr14=atr14, D=D, keys=keys, pos=pos, lo_b=lo_b, hi_b=hi_b,
                iso=[r[0] for r in rows], day=[r[0][:10] for r in rows])


def walk(P, i, side, stop, tgt, last=T("15:00")):
    """Index exit: stop (gap-through at the open), target, squareoff at `last`."""
    o, h, l, c, day = P["o"], P["h"], P["l"], P["c"], P["day"]
    for j in range(i + 1, len(c)):
        if day[j] != day[i] or hm(P["iso"][j]) >= last:
            return j - 1, c[j - 1], "squareoff"
        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
            return j, (min(o[j], stop) if side > 0 else max(o[j], stop)), "stop"
        if tgt is not None and ((side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt)):
            return j, (max(o[j], tgt) if side > 0 else min(o[j], tgt)), "target"
    return len(c) - 1, c[-1], "eod"


def mk(name, ix, P, i, side, stop, rmult, play, tgt=None, floor_atr=True):
    """A new-source candidate: stop widened to the engine's min_stop_atr x ATR20
    if tighter (the engine refuses a tighter stop), target rmult x R unless an
    explicit index target is given."""
    c0 = P["c"][i]; a = P["atr"][i]
    risk = abs(c0 - stop)
    if a <= 0 or risk <= 0:
        return None
    if floor_atr and risk < L.min_stop_atr * a:
        risk = L.min_stop_atr * a; stop = c0 - side * risk
    if tgt is None:
        tgt = c0 + side * rmult * risk
    if (tgt - c0) * side <= 0:
        return None
    j, px, why = walk(P, i, side, stop, tgt)
    if j <= i:
        return None
    t = S.cand(name, ix, i, j, px, side, risk, why, play)
    return t


def run_book(ts, profit_cap=None, max_trades=15, side_cooldown=30):
    """tools/backtest.py:run with the live halt (common.run), plus an optional
    daily PROFIT cap (rule 4.1: stop for the day at +P)."""
    from common import live_halt_pct
    halt_pct = live_halt_pct()
    out = []
    days = {}
    for t in ts:
        days.setdefault(t["day"], []).append(t)
    for d in sorted(days):
        tsd = sorted(days[d], key=lambda t: (t["m"], t["u"]))
        busy_until, n, pnl, streak, pause, last_loss, plays = -1, 0, 0.0, 0, -1, -10**9, {}
        loss_side = 0
        for t in tsd:
            if t["m"] <= busy_until or t["m"] < pause or t["m"] - last_loss < 3: continue
            if side_cooldown and t["side"] == loss_side and t["m"] - last_loss < side_cooldown: continue
            if n >= max_trades or pnl <= -halt_pct * 100: continue
            if profit_cap is not None and pnl >= profit_cap: continue
            key = (t["u"], t["plays"][0] if t["plays"] else "", t["side"])
            if plays.get(key, 0) >= 2: continue
            net = t["net"]
            out.append(dict(t, net2=net)); n += 1; pnl += net
            plays[key] = plays.get(key, 0) + 1
            busy_until = t["m"] + t["held"]
            if net < 0:
                streak += 1; last_loss = busy_until; loss_side = t["side"]
                if streak >= 3: pause, streak = busy_until + 30, 0
            else:
                streak = 0
    return out


# ----------------------------------------------------------------------------
# reporting: every variant, baseline comparison, plateau
# ----------------------------------------------------------------------------
ROWS = []          # (family, label, params, stats)
SESS = None
BASE = None


def stat(tr):
    s = summ(tr)
    return dict(n=s["n"], win=s["win"], i=s["i"], o=s["o"], y=s["y"], dd=s["dd"],
                ps=len(tr) / SESS if SESS else 0.0,
                by={k: sum(1 for t in tr if t.get("strat") == k) for k in sorted({t.get("strat", "?") for t in tr})})


def fmt(label, s, base=None):
    flag = ""
    if base is not None:
        both = s["i"] > base["i"] and s["o"] > base["o"]
        flag = (f" | dIN {s['i'] - base['i']:+7,.0f} dOUT {s['o'] - base['o']:+7,.0f}"
                + ("  BETTER BOTH" if both else ""))
    return (f"{label:46s} n={s['n']:3d} win={s['win']:3.0f}% IN {s['i']:+7,.0f} OUT {s['o']:+7,.0f} "
            f"tot {s['y']:+7,.0f} DD {s['dd']:6,.0f} {s['ps']:.2f}/s{flag}")


def rec(family, label, params, tr, show_by=False):
    s = stat(tr)
    ROWS.append((family, label, params, s))
    print(fmt(label, s, BASE) + (f" {s['by']}" if show_by else ""), flush=True)
    return s


def better(s):
    return s["i"] > BASE["i"] and s["o"] > BASE["o"]


def neighbours(family, params):
    fam = [(lab, p, s) for f, lab, p, s in ROWS if f == family]
    out = []
    for lab, p, s in fam:
        diff = [k for k in params if p.get(k) != params.get(k)]
        if len(diff) != 1:
            continue
        k = diff[0]
        vals = []
        for _, q, _ in fam:
            if q.get(k) not in vals:
                vals.append(q.get(k))
        if all(isinstance(v, (int, float)) for v in vals):
            vs = sorted(vals); a, b = vs.index(params[k]), vs.index(p[k])
            if abs(a - b) != 1:
                continue
        out.append((lab, s))
    return out


def plateau_table():
    print("\n" + "=" * 120)
    print("ALL VARIANTS vs BASELINE (OB + LHM).  BOTH = better IN and OUT;  PASS = BOTH and every "
          "one-parameter neighbour in its family BOTH too")
    print("=" * 120)
    print(fmt("BASELINE OB + LHM", BASE))
    last = None
    passed = []
    for f, lab, p, s in ROWS:
        if f.startswith("info"):
            continue
        if f != last:
            print(f"-- {f}"); last = f
        nb = neighbours(f, p)
        both = better(s)
        ok = both and nb and all(better(x) for _, x in nb)
        tag = "PASS" if ok else ("both, no plateau" if both else "")
        if ok:
            passed.append((f, lab))
        print(fmt("  " + lab, s, BASE) + f"  [{len(nb)} nb] {tag}")
    print("\nPASSING VARIANTS:", passed or "none")
    return passed


# ----------------------------------------------------------------------------
# Rule C: NIFTY / BANKNIFTY divergence no-trade filter
# ----------------------------------------------------------------------------
def divergence_block(PN, PB, fc, mode, block):
    """Set of timestamps (iso) at which new entries are blocked.
    fc: first-candle minutes (5 or 15). mode 'close': a bar is divergent when
    NIFTY closes beyond one first-candle extreme and BANKNIFTY beyond the
    opposite one; 'wick': the bar on which the second of two opposite wick
    breaks first happens. block: minutes after a divergent bar (inclusive), or
    'until' = until a bar on which both close beyond the same-side extreme."""
    out = set()
    for d in PN["keys"]:
        if d not in PB["D"]:
            continue
        iN, iB = PN["D"][d]["idx"], PB["D"][d]["idx"]
        isoN = [PN["iso"][k] for k in iN]
        mB = {PB["iso"][k]: k for k in iB}
        first = [k for k in iN if hm(PN["iso"][k]) < T("09:15") + fc]
        firstB = [k for k in iB if hm(PB["iso"][k]) < T("09:15") + fc]
        if not first or not firstB:
            continue
        nh, nl = PN["h"][first].max(), PN["l"][first].min()
        bh, bl = PB["h"][firstB].max(), PB["l"][firstB].min()
        sticky = dict(nl=False, nh=False, bl=False, bh=False); fired = False
        until_on = False; last_div = None
        for k, iso in zip(iN, isoN):
            if hm(iso) < T("09:15") + fc:
                continue
            kb = mB.get(iso)
            if kb is None:
                continue
            cn, cb = PN["c"][k], PB["c"][kb]
            if mode == "close":
                div = (cn < nl and cb > bh) or (cn > nh and cb < bl)
            else:
                sticky["nl"] |= PN["l"][k] < nl; sticky["nh"] |= PN["h"][k] > nh
                sticky["bl"] |= PB["l"][kb] < bl; sticky["bh"] |= PB["h"][kb] > bh
                now = (sticky["nl"] and sticky["bh"]) or (sticky["nh"] and sticky["bl"])
                div = now and not fired
                fired |= now
            if block == "until":
                if div:
                    until_on = True
                elif until_on and ((cn > nh and cb > bh) or (cn < nl and cb < bl)):
                    until_on = False
                if until_on:
                    out.add(iso)
            else:
                if div:
                    last_div = hm(iso)
                if last_div is not None and hm(iso) - last_div < block:
                    out.add(iso)
    return out


# ----------------------------------------------------------------------------
# Rule H: chop filters
# ----------------------------------------------------------------------------
def chop_state(P, tf, k, pattern):
    """Per 1-minute row: None, or the active chop zone (lo, hi) known at that
    bar's close. tf 1 or 5 (minute bars, 5m bars on the 09:15 grid); 'big' =
    range >= k x ATR14 of that timeframe (prior bars).
      a: big bar, then an opposite-colour big bar within 2 bars -> zone = their
         range, until 2 consecutive big same-direction bars;
      b: big bar followed by 2 inside bars -> zone = its range, until a close
         beyond it."""
    n = len(P["c"])
    # build tf bars: (o, h, l, c, last 1m row)
    B = []
    for d in P["keys"]:
        idx = P["D"][d]["idx"]
        grp = {}
        for i in idx:
            grp.setdefault((hm(P["iso"][i]) - T("09:15")) // tf, []).append(i)
        for g in sorted(grp):
            ii = grp[g]
            B.append((P["o"][ii[0]], P["h"][ii].max(), P["l"][ii].min(), P["c"][ii[-1]], ii[-1], d))
    o = np.array([b[0] for b in B]); h = np.array([b[1] for b in B])
    l = np.array([b[2] for b in B]); c = np.array([b[3] for b in B])
    pc_ = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc_), abs(l - pc_)))
    cs = np.concatenate([[0], np.cumsum(tr)])
    atr = np.zeros(len(B))
    for q in range(15, len(B)):
        atr[q] = (cs[q] - cs[q - 14]) / 14
    big = (h - l) >= k * atr
    big &= atr > 0
    col = np.sign(c - o)
    st_after = [None] * len(B)
    za = zb = None; run_dir = 0; run_n = 0
    for q in range(len(B)):
        if q and B[q][5] != B[q - 1][5]:
            za = zb = None; run_dir = 0; run_n = 0
        # pattern a
        if pattern in ("a", "ab"):
            if big[q] and col[q] != 0:
                run_n = run_n + 1 if col[q] == run_dir else 1; run_dir = col[q]
            else:
                run_dir, run_n = 0, 0
            if za is not None and run_n >= 2:
                za = None
            for back in (1, 2):
                p = q - back
                if (p >= 0 and B[p][5] == B[q][5] and big[q] and big[p] and col[p] != 0
                        and col[q] == -col[p]):
                    za = (l[p:q + 1].min(), h[p:q + 1].max()); run_dir, run_n = col[q], 1
                    break
        # pattern b
        if pattern in ("b", "ab"):
            if zb is not None and (c[q] > zb[1] or c[q] < zb[0]):
                zb = None
            p = q - 2
            if (p >= 0 and B[p][5] == B[q][5] and big[p] and h[p + 1] <= h[p] and l[p + 1] >= l[p]
                    and h[q] <= h[p] and l[q] >= l[p]):
                zb = (l[p], h[p])
        st_after[q] = (za, zb)
    # map to 1-minute rows: state of the last completed tf bar
    out = [None] * n
    q = -1
    ends = [b[4] for b in B]
    for i in range(n):
        while q + 1 < len(B) and ends[q + 1] <= i:
            q += 1
        if q < 0 or B[q][5] != P["day"][i]:
            continue
        out[i] = st_after[q]
    return out


def chop_blocked(t, P, st):
    s = st[t["i"]]
    if s is None:
        return False
    za, zb = s
    cpx = P["c"][t["i"]]
    if za is not None and za[0] <= cpx <= za[1]:
        return True
    if zb is not None:
        return True
    return False


# ----------------------------------------------------------------------------
# Rule A: gap-day direction lock (filter) and gap Fib-pocket entry
# ----------------------------------------------------------------------------
def gap_sign(PP, u, d, Gn, Gb, adr_x=None):
    P = PP[u]; dd = P["D"].get(d)
    if dd is None:
        return 0
    g = dd["o"] - dd["pc"]
    if adr_x is not None:
        G = adr_x * dd["adr"] if dd["adr"] else 1e9
    elif u == "NIFTY":
        G = Gn
    elif u == "BANKNIFTY":
        G = Gb
    else:                                    # SENSEX: NIFTY threshold scaled by price
        nd = PP["NIFTY"]["D"].get(d)
        G = Gn * dd["pc"] / nd["pc"] if nd else 1e9
    return int(np.sign(g)) if abs(g) >= G else 0


def gap_entries(IX, PP, Gn, Gb, latest="14:45"):
    out = []
    for u in INDICES:
        P = PP[u]; ix = IX[u]
        for d in P["keys"][1:]:
            g = gap_sign(PP, u, d, Gn, Gb)
            if not g:
                continue
            dd = P["D"][d]; idx = dd["idx"]; pc = dd["pc"]
            ext = None; filled = False
            for i in idx:
                m = hm(P["iso"][i])
                if ext is not None and m >= T("09:30") and m <= T(latest) and not filled:
                    f50 = ext - 0.5 * (ext - pc); f618 = ext - 0.618 * (ext - pc)
                    o_, h_, l_, c_ = P["o"][i], P["h"][i], P["l"][i], P["c"][i]
                    if g > 0 and l_ <= f50 and c_ >= f618 and c_ > o_:
                        t = mk(u, ix, P, i, 1, l_ - 0.1 * P["atr"][i], 2.0, "GAP")
                        if t: out.append(t)
                        break
                    if g < 0 and h_ >= f50 and c_ <= f618 and c_ < o_:
                        t = mk(u, ix, P, i, -1, h_ + 0.1 * P["atr"][i], 2.0, "GAP")
                        if t: out.append(t)
                        break
                # update extreme / gap-fill AFTER the decision on this bar
                if g > 0:
                    ext = P["h"][i] if ext is None else max(ext, P["h"][i]); filled |= P["l"][i] <= pc
                else:
                    ext = P["l"][i] if ext is None else min(ext, P["l"][i]); filled |= P["h"][i] >= pc
    return out


# ----------------------------------------------------------------------------
# Rule B: NIFTY / BANKNIFTY divergence (SMT) entry
# ----------------------------------------------------------------------------
def smt_entries(IX, PP, sl, tp, refs=("pc", "fc", "dl"), fc=15, wait=10, look=30):
    """One index breaks a shared reference (prev close / first-15m-candle low /
    the day's low so far), the other holds its own -> buy the one that broke on
    its first green 1-min close within `wait` bars while the other still holds.
    Mirror for highs. sl: NIFTY points (20/30; BANKNIFTY scaled by price) or
    'candle' (the break-to-entry extreme). tp: 40 NIFTY points (scaled) or
    'swing50' (50% of the prior `look`-bar swing)."""
    PN, PB = PP["NIFTY"], PP["BANKNIFTY"]
    out = []
    for d in PN["keys"][1:]:
        if d not in PB["D"]:
            continue
        pair = {"NIFTY": PN, "BANKNIFTY": PB}
        ID = {u: pair[u]["D"][d]["idx"] for u in pair}
        common_iso = sorted(set(PN["iso"][k] for k in ID["NIFTY"]) & set(PB["iso"][k] for k in ID["BANKNIFTY"]))
        if len(common_iso) < 60:
            continue
        k_of = {u: {pair[u]["iso"][k]: k for k in ID[u]} for u in pair}
        ratio = {"NIFTY": 1.0, "BANKNIFTY": PB["D"][d]["pc"] / PN["D"][d]["pc"]}
        fcx = {}
        for u, P in pair.items():
            ff = [k for k in ID[u] if hm(P["iso"][k]) < T("09:15") + fc]
            fcx[u] = (P["h"][ff].max(), P["l"][ff].min()) if ff else None
        if None in fcx.values():
            continue
        done = set()
        for side in (1, -1):
            for ref in refs:
                for X, Y in (("NIFTY", "BANKNIFTY"), ("BANKNIFTY", "NIFTY")):
                    PX, PY = pair[X], pair[Y]
                    def lvl(P, u, kk):
                        if ref == "pc":
                            return P["D"][d]["pc"]
                        if ref == "fc":
                            return fcx[u][1] if side > 0 else fcx[u][0]
                        # day extreme so far (bars before kk)
                        v = P["lo_b"][kk] if side > 0 else P["hi_b"][kk]
                        return None if np.isnan(v) else v
                    def broke(P, u, kk):
                        lv = lvl(P, u, kk)
                        if lv is None:
                            return False
                        return P["l"][kk] < lv if side > 0 else P["h"][kk] > lv
                    held_all = True       # Y has not broken since the reference was set
                    ev = None
                    for n_, iso in enumerate(common_iso):
                        m = hm(iso)
                        if m < T("09:15") + fc:
                            continue
                        kx, ky = k_of[X][iso], k_of[Y][iso]
                        if broke(PY, Y, ky) and ref != "dl":
                            held_all = False
                        if m > T("14:45"):
                            break
                        if ev is None:
                            if not held_all:
                                break
                            if broke(PX, X, kx) and not broke(PY, Y, ky):
                                prevc = PX["c"][kx - 1]
                                lv = lvl(PX, X, kx)
                                fresh = (prevc >= lv) if side > 0 else (prevc <= lv)
                                if ref == "dl" or fresh:
                                    ev = (n_, kx)
                            continue
                        # waiting for the confirming close
                        if n_ - ev[0] > wait or broke(PY, Y, ky):
                            ev = None
                            if ref != "dl":
                                break
                            continue
                        cx, ox = PX["c"][kx], PX["o"][kx]
                        if (side > 0 and cx > ox) or (side < 0 and cx < ox):
                            if (X, kx, side) in done:
                                break
                            seg = range(ev[1], kx + 1)
                            ext = PX["l"][list(seg)].min() if side > 0 else PX["h"][list(seg)].max()
                            if sl == "candle":
                                stop = ext - side * 0.1 * PX["atr"][kx]
                            else:
                                stop = cx - side * sl * ratio[X]
                            if tp == "swing50":
                                pre = list(range(max(ID[X][0], ev[1] - look), ev[1]))
                                if not pre:
                                    break
                                sw = PX["h"][pre].max() if side > 0 else PX["l"][pre].min()
                                tgt = ext + 0.5 * (sw - ext)
                            else:
                                tgt = cx + side * tp * ratio[X]
                            t = mk(X, IX[X], PX, kx, side, stop, None, "SMT", tgt=tgt)
                            if t:
                                t["ref"] = ref; out.append(t); done.add((X, kx, side))
                            break
    return out


# ----------------------------------------------------------------------------
# Rule D: sweep and reclaim
# ----------------------------------------------------------------------------
ROUND = {"NIFTY": 100.0, "BANKNIFTY": 500.0, "SENSEX": 500.0}


def sweep_entries(IX, PP, k, rmult, need=None, vol=None, vol_x=None, only=None, levels=None,
                  overshoot=1.5, min_over=0.1):
    """Bullish: price trades below a level after a close at/above it, and within
    k bars (the sweep bar itself included) a bar closes back above it -> enter on
    that close; SL beyond the sweep extreme (min_stop_atr floor), target rmult R.
    Levels: PDH/PDL, prev close, round numbers, first-15m-candle high/low.
    A sweep must overshoot the level by 0.1-1.5 x ATR20 (a 0.15-pt poke is noise).
    need: None | 'stretched' (3 same-colour bars into the sweep) | 'touched'
    (level touched earlier in the day without a close through it).
    vol/vol_x: NIFTY-constituent volume proxy ratio >= vol_x on the sweep bar."""
    out = []
    for u in (only or INDICES):
        P = PP[u]; ix = IX[u]; o, h, l, c, a = P["o"], P["h"], P["l"], P["c"], P["atr"]
        for d in P["keys"][1:]:
            dd = P["D"][d]; idx = dd["idx"]
            ff = [q for q in idx if hm(P["iso"][q]) < T("09:30")]
            fch, fcl = (h[ff].max(), l[ff].min()) if ff else (None, None)
            named = dict(pdh=dd["pdh"], pdl=dd["pdl"], pc=dd["pc"], fch=fch, fcl=fcl)
            used = set()
            for s in idx:
                m = hm(P["iso"][s])
                if m < T("09:30") or m > T("14:45") or s - 1 < idx[0]:
                    continue
                pcl = c[s - 1]
                for side in (1, -1):
                    cand_lv = []
                    for nm, lv in named.items():
                        if levels and nm not in levels:
                            continue
                        cand_lv.append((nm, lv))
                    if not levels or "round" in levels:
                        st = ROUND[u]
                        r = math.floor(pcl / st) * st if side > 0 else math.ceil(pcl / st) * st
                        cand_lv.append(("round", r))
                    for nm, lv in cand_lv:
                        if lv is None:
                            continue
                        if side > 0 and not (pcl >= lv > l[s]):
                            continue
                        if side < 0 and not (pcl <= lv < h[s]):
                            continue
                        ov = lv - l[s] if side > 0 else h[s] - lv
                        if ov > overshoot * a[s] or ov < min_over * a[s]:
                            continue
                        key = (nm, round(lv, 2), side)
                        if key in used:
                            continue
                        if need == "stretched":
                            prev3 = [q for q in (s - 3, s - 2, s - 1) if q >= idx[0]]
                            if len(prev3) < 3 or not all((c[q] < o[q]) if side > 0 else (c[q] > o[q]) for q in prev3):
                                continue
                        if need == "touched":
                            early = [q for q in idx if q < s - 5]
                            tol = 0.15 * a[s]
                            if not any(abs((l[q] if side > 0 else h[q]) - lv) <= tol and
                                       ((c[q] >= lv) if side > 0 else (c[q] <= lv)) for q in early):
                                continue
                        if vol is not None:
                            vr = vol.get(P["iso"][s])
                            if vr is None or vr < vol_x:
                                continue
                        e = None
                        for q in range(s, min(s + k + 1, idx[-1] + 1)):
                            if (side > 0 and c[q] > lv) or (side < 0 and c[q] < lv):
                                e = q; break
                        if e is None or hm(P["iso"][e]) > T("14:45"):
                            continue
                        used.add(key)
                        ext = l[s:e + 1].min() if side > 0 else h[s:e + 1].max()
                        t = mk(u, ix, P, e, side, ext - side * 0.1 * a[e], rmult, "SWEEP")
                        if t:
                            t["lvl"] = nm; out.append(t)
    # one candidate per (index, bar, side)
    seen, uniq = set(), []
    for t in out:
        kk = (t["u"], t["i"], t["side"])
        if kk not in seen:
            seen.add(kk); uniq.append(t)
    return uniq


NIFTY50 = ("ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL "
           "BHARTIARTL CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE "
           "HINDALCO HINDUNILVR ICICIBANK INDIGO INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI "
           "MAXHEALTH NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA "
           "TATACONSUM TMPV TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO").split()


def volume_proxy(days_back=10):
    """Per minute: rupee volume summed over the NIFTY 50 names present in
    .cache/history/stocks (1-min, Jun-Sep 2026), divided by the median of the
    same minute over the previous `days_back` sessions."""
    tot, used = {}, 0
    for s in NIFTY50:
        p = os.path.join(ROOT, ".cache", "history", "stocks", f"{s}_1m.json")
        if not os.path.exists(p):
            continue
        used += 1
        for r in json.load(open(p)):
            tot[r[0]] = tot.get(r[0], 0.0) + r[4] * r[5]
    by_day = {}
    for iso, v in tot.items():
        by_day.setdefault(iso[:10], {})[iso[11:16]] = v
    days = sorted(by_day); ratio = {}
    for k, d in enumerate(days):
        if k < days_back:
            continue
        for mm, v in by_day[d].items():
            hist = [by_day[p].get(mm) for p in days[k - days_back:k] if by_day[p].get(mm)]
            if len(hist) >= days_back // 2:
                med = float(np.median(hist))
                if med > 0:
                    ratio[f"{d}T{mm}:00+05:30"] = v / med
    return ratio, used, days[days_back] if len(days) > days_back else None


# ----------------------------------------------------------------------------
# Rule 6: exits (copy of realistic.reprice with the exit made a parameter)
# ----------------------------------------------------------------------------
def reprice_x(t, ix, P, be_r=1.0, part=None):
    """realistic.reprice (costs inside, next expiry on expiry day, exact costs)
    with: be_r = premium R at which the stop moves to entry + costs (1.0 is the
    engine's and the baseline's; None = never); part = dict(frac, at_r, mode,
    step): book `frac` of the position when the bid is at_r premium-R up, stop
    the rest at +1R premium and trail it `step` NIFTY points (scaled by price)
    behind the best index close; mode 'orig' = the rest also leaves at the
    strategy's own exit, 'run' = the rest runs to its trail / 15:15."""
    from realistic import when, THETA_F, CM, _exact_rt
    rows, lot, step = ix.rows, ix.lot, ix.step
    i, j, side = t["i"], t["j"], t["side"]
    S0 = rows[i][4]; opt = "CE" if side > 0 else "PE"
    d0 = date.fromisoformat(t["day"])
    exp = ix.cal.get(d0)
    if exp is None: return None
    expiry_today = exp == d0
    if expiry_today:
        later = [x for x in ix.days if x > d0]
        exp = ix.cal.get(later[0]) if later else exp
        if exp == d0: return None
    t0 = when(rows[i][0]); T0 = T_years(t0, exp, ix.days)
    sig = ix.iv(d0, exp == d0)
    cap_inr = CAPITAL * L.max_risk_pct_per_trade / 100.0
    idx_risk = t["risk"]
    atm = round(S0 / step) * step
    best = None
    ss, sf = L.exit_slip_spreads, L.exit_slip_frac
    for off in range(-L.max_strike_offset, L.max_strike_offset + 1):
        K = atm + off * step
        mid = bs(S0, K, T0, sig, opt)
        if mid <= 0.05: continue
        sp = ix.spread(mid); ask = mid + sp / 2
        dlt = abs(bs_delta(S0, K, T0, sig, opt))
        extra = _exact_rt(ask, lot) + ss * sp + sf * ask
        md = (cap_inr / lot - extra) / idx_risk
        if ask * lot > CAPITAL or dlt < L.min_delta or dlt > md or sp > L.max_spread_points:
            continue
        if best is None or ask > best[2]:
            best = (K, mid, ask, dlt, sp)
    if best is None: return None
    K, mid0, ask0, dlt, sp = best
    prem_risk = min(max(idx_risk * dlt, 2 * sp), ask0 * L.max_risk_frac_of_premium)
    fee_pts = _exact_rt(ask0, lot)
    extra = fee_pts + ss * sp + sf * ask0
    if (prem_risk + extra) * lot > cap_inr + 1e-6: return None
    cost_pts = fee_pts
    if 2 * prem_risk - cost_pts < L.min_net_points: return None
    frac_c = float(getattr(L, "max_cost_frac_of_risk", 0.0) or 0.0)
    if frac_c > 0 and cost_pts > frac_c * (prem_risk + extra): return None
    stop = ask0 - prem_risk
    if stop <= 0.05: return None
    S_exit = S0 + side * (t["net"] + bt.FEE) / (t["delta"] * lot)
    def bid_at(k, S):
        Tk = T0 - THETA_F * (T0 - T_years(when(rows[k][0]), exp, ix.days))
        return bs(S, K, max(Tk, 0.0), sig, opt) - sp / 2
    trailed, why = False, t["why"]
    fills = []                      # (fraction, price)
    rem = 1.0; part_on = False; best_S = None; idx_trail = None; exit_k = None
    stepp = part["step"] * P["D"][t["day"]]["pc"] / PP_NIFTY_PC(t["day"]) if part else 0
    k = i
    last_k = j
    if part and part["mode"] == "run":
        last_k = max(k2 for k2 in P["D"][t["day"]]["idx"] if hm(rows[k2][0]) < T("15:15"))
    for k in range(i + 1, max(j, last_k) + 1):
        if not part_on and k > j:
            break
        S = S_exit if (k == j and not part_on) else rows[k][4]
        bid = bid_at(k, S)
        if be_r is not None and not trailed and bid - ask0 >= be_r * prem_risk:
            stop, trailed = max(stop, ask0 + cost_pts), True
        if part and not part_on and k < j and bid - ask0 >= part["at_r"] * prem_risk:
            fills.append((part["frac"], bid)); rem = 1.0 - part["frac"]; part_on = True
            stop = max(stop, ask0 + prem_risk); best_S = S; idx_trail = S - side * stepp
            why = "partial"
            if rem <= 1e-9:
                exit_k = k; break
            continue
        if part_on:
            best_S = max(best_S, S) if side > 0 else min(best_S, S)
            idx_trail = max(idx_trail, best_S - stepp) if side > 0 else min(idx_trail, best_S + stepp)
            hit_trail = (S <= idx_trail) if side > 0 else (S >= idx_trail)
            if bid <= stop or hit_trail or k == last_k or (part["mode"] == "orig" and k == j):
                px = bid if part["mode"] == "run" or k != j else bid_at(j, S_exit)
                fills.append((rem, px)); exit_k = k; why = "partial+rest"; break
            continue
        if bid <= stop and k < j:
            exit_k = k; fills.append((1.0, bid)); why = "prem_trail" if trailed else "prem_stop"; break
    if exit_k is None:
        if part_on:
            kk = min(max(j, last_k), len(rows) - 1)
            fills.append((rem, bid_at(kk, rows[kk][4]))); exit_k = kk
        else:
            exit_k = j; fills.append((1.0, bid_at(j, S_exit)))
    fees = leg_cost(ask0, lot, "BUY", "OPT", CM) + sum(
        leg_cost(max(px, 0.05), lot * f, "SELL", "OPT", CM) for f, px in fills)
    net = sum((px - ask0) * lot * f for f, px in fills) - fees
    return dict(net=net, held=exit_k - i, why=why, expiry_day=expiry_today, dte=(exp - d0).days,
                prem=ask0, lot_cost=ask0 * lot, strike=K, delta=dlt)


_PCN = {}
def PP_NIFTY_PC(d):
    return _PCN[d]


# ----------------------------------------------------------------------------
def main():
    global SESS, BASE
    import time
    t_start = time.time()
    K = pickle.load(open(PKL, "rb"))
    assert abs(K["capital"] - CAPITAL) < 1e-6 and K["risk"] == L.max_risk_pct_per_trade, "stale cache"
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    PP = {n: prep(IX[n]) for n in INDICES}
    for d, dd in PP["NIFTY"]["D"].items():
        _PCN[d] = dd["pc"]
    SESS = len(PP["NIFTY"]["keys"])
    OB, LHM = K["OB"], K["LHM"]
    BOOK = OB + LHM
    from fixes_test import run as fx_run
    ref = fx_run(BOOK, side_cooldown=30)
    base = run_book(BOOK)
    assert [round(t["net2"], 6) for t in ref] == [round(t["net2"], 6) for t in base], "run_book != fixes_test.run"
    BASE = stat(base)
    print(f"capital Rs {CAPITAL:,.2f}, risk cap {L.max_risk_pct_per_trade:g}%, daily halt "
          f"{L.max_daily_loss_pct:g}% of Rs {float(L.capital_ceiling or L.starting_capital):,.0f}, "
          f"split {SPLIT}, sessions {SESS}; cached OB {len(OB)} LHM {len(LHM)} candidates")
    print("Columns: n, win%, IN (before split), OUT (after), total, max drawdown, trades/session; "
          "dIN/dOUT vs baseline; BETTER BOTH = above baseline in both halves.\n")
    print(fmt("BASELINE OB + LHM", BASE) + f" {BASE['by']}\n")

    def book_iso(t):
        return IX[t["u"]].rows[t["i"]][0]

    def add_src(C, strat, trend=True):
        out = []
        for t in reprice(C, IX):
            if trend and F.h4_ret(t, 5) * t["side"] <= 0:
                continue
            out.append(dict(t, strat=strat))
        return out

    # ---------------------------------------------------------------- 1. Rule C
    print("== 1. Rule C: NIFTY/BANKNIFTY first-candle divergence -> no new entries (filter on OB + LHM)")
    for scope in ("book", "OB only"):
        for fc in (5, 15):
            for mode in ("close", "wick"):
                for block in (30, 60, "until"):
                    blk = divergence_block(PP["NIFTY"], PP["BANKNIFTY"], fc, mode, block)
                    keep = [t for t in BOOK if book_iso(t) not in blk or (scope == "OB only" and t["strat"] != "OB")]
                    rec(f"C divergence filter ({scope})",
                        f"C {scope} fc{fc}m {mode} block {block} (-{len(BOOK) - len(keep)} cands)",
                        dict(fc=fc, mode=mode, block={30: 1, 60: 2, "until": 3}[block]), run_book(keep))

    # ---------------------------------------------------------------- 2. Rule H
    print("\n== 2. Rule H: chop filters (filter on OB + LHM); big = range >= k x ATR14 of that timeframe")
    for pattern, nm in (("a", "big+opposite big"), ("b", "big+2 inside"), ("ab", "either")):
        for k in (1.5, 2.0):
            for tf in (1, 5):
                st = {u: chop_state(PP[u], tf, k, pattern) for u in INDICES}
                keep = [t for t in BOOK if not chop_blocked(t, PP[t["u"]], st[t["u"]])]
                rec(f"H chop {pattern}", f"H{pattern} {nm} k{k:g} {tf}m (-{len(BOOK) - len(keep)} cands)",
                    dict(k=k, tf=tf), run_book(keep))

    # ---------------------------------------------------------------- 3. Rule A
    print("\n== 3. Rule A: gap-day direction lock")
    gaps = {}
    for u in INDICES:
        gaps[u] = {g: sum(1 for d in PP[u]["keys"][1:] if gap_sign(PP, u, d, *g)) for g in ((80, 220), (100, 300), (120, 300))}
    print("   gap days (|open - prev close| >= G), per index for (N80,B220) (N100,B300) (N120,B300):",
          {u: list(v.values()) for u, v in gaps.items()})
    for scope in ("book", "OB only"):
        for Gn in (80, 100, 120):
            for Gb in (220, 300):
                keep = []
                for t in BOOK:
                    g = gap_sign(PP, t["u"], t["day"], Gn, Gb)
                    if g and t["side"] != g and (scope == "book" or t["strat"] == "OB"):
                        continue
                    keep.append(t)
                rec(f"A gap filter ({scope})", f"A filter {scope} N{Gn} B{Gb} (-{len(BOOK) - len(keep)} cands)",
                    dict(Gn=Gn, Gb=Gb), run_book(keep))
    for x in (0.25, 0.35, 0.5):
        keep = [t for t in BOOK if not (gap_sign(PP, t["u"], t["day"], 0, 0, adr_x=x) not in (0, t["side"]))]
        rec("A gap filter (book, G = x ADR)", f"A filter book G={x:g} ADR (-{len(BOOK) - len(keep)} cands)",
            dict(x=x), run_book(keep))
    print("   -- gap Fib-pocket ENTRY added to the book (pullback into 0.5-0.618 of prev close -> day extreme,"
          " confirming close, SL that candle (min 1 ATR20), 2R, squareoff 15:00)")
    for trend in (True, False):
        for Gn in (80, 100, 120):
            for Gb in (220, 300):
                for latest in ("12:00", "14:45"):
                    G = add_src(gap_entries(IX, PP, Gn, Gb, latest), "GAP", trend)
                    tag = "4h" if trend else "no4h"
                    alone = stat(run_book(G))
                    rec(f"A gap entry ({tag})", f"A entry {tag} N{Gn} B{Gb} <= {latest} [+{len(G)}; alone n={alone['n']} "
                        f"{alone['y']:+,.0f}]", dict(Gn=Gn, Gb=Gb, latest=T(latest)), run_book(BOOK + G),
                        show_by=True)

    # ---------------------------------------------------------------- 4. Rule B
    print("\n== 4. Rule B: NIFTY/BANKNIFTY divergence (SMT) entry added to the book "
          "(refs: prev close, first-15m extreme, day extreme; confirm within 10 bars)")
    keep_smt = None
    for trend in (True, False):
        for sl in ("candle", 20, 30):
            for tp in (40, "swing50"):
                Cs = smt_entries(IX, PP, sl, tp)
                G = add_src(Cs, "SMT", trend)
                tag = "4h" if trend else "no4h"
                alone = stat(run_book(G))
                tr = run_book(BOOK + G)
                rec(f"B SMT entry ({tag})", f"B {tag} SL {sl} TP {tp} [+{len(G)}; alone n={alone['n']} {alone['y']:+,.0f}]",
                    dict(sl={"candle": 0, 20: 1, 30: 2}[sl], tp=str(tp)), tr, show_by=True)
                if trend and sl == 20 and tp == 40:
                    keep_smt = tr
    if keep_smt:
        for r in ("pc", "fc", "dl"):
            sub = [t for t in keep_smt if t.get("strat") == "SMT" and t.get("ref") == r]
            if sub:
                s = stat(sub)
                print(f"     (info) SL20/TP40 SMT trades taken, ref {r}: n={s['n']} win={s['win']:.0f}% "
                      f"IN {s['i']:+,.0f} OUT {s['o']:+,.0f}")

    # ---------------------------------------------------------------- 5. Rule D
    print("\n== 5. Rule D: sweep and reclaim (PDH/PDL, prev close, round numbers N100/B500/S500, "
          "first-15m extremes), entry on the reclaim close, SL beyond the sweep (min 1 ATR20)")
    keep_d = None
    for need, fam in ((None, "D sweep"), ("stretched", "D sweep + stretched approach"),
                      ("touched", "D sweep + level touched earlier (liquidity)")):
        for trend in ((True, False) if need is None else (True,)):
            for k in (1, 3):
                for rm in (2.0, 3.0):
                    Cs = sweep_entries(IX, PP, k, rm, need=need)
                    G = add_src(Cs, "SWEEP", trend)
                    tag = "4h" if trend else "no4h"
                    alone = stat(run_book(G))
                    tr = run_book(BOOK + G)
                    rec(f"{fam} ({tag})", f"D {need or 'plain'} {tag} k{k} {rm:g}R [+{len(G)}; alone n={alone['n']} "
                        f"{alone['y']:+,.0f}]", dict(k=k, rm=rm), tr, show_by=True)
                    if need is None and trend and k == 1 and rm == 2.0:
                        keep_d = tr
    if keep_d:
        for lv in ("pdh", "pdl", "pc", "round", "fch", "fcl"):
            sub = [t for t in keep_d if t.get("strat") == "SWEEP" and t.get("lvl") == lv]
            if sub:
                s = stat(sub)
                print(f"     (info) k1/2R sweep trades taken, level {lv}: n={s['n']} win={s['win']:.0f}% "
                      f"IN {s['i']:+,.0f} OUT {s['o']:+,.0f}")
    print("   -- volume confirmation: NIFTY-constituent rupee-volume proxy (Jun-Sep 2026 ONLY, all of it in the"
          " OUT half; index volume is 0 in the history)")
    vol, used, v0 = volume_proxy()
    print(f"   proxy from {used} NIFTY 50 names in .cache/history/stocks; ratio = this minute / median of the same"
          f" minute over the previous 10 sessions; usable from {v0}")
    W = lambda tr: [t for t in tr if v0 <= t["day"] <= "2026-09-28"]
    bw = stat(W(base))
    print(fmt(f"   window baseline OB+LHM {v0}..09-28", bw))
    for k in (1, 3):
        Cs = [t for t in sweep_entries(IX, PP, k, 2.0, only=["NIFTY"]) if v0 <= t["day"] <= "2026-09-28"]
        G = add_src(Cs, "SWEEP")
        s = stat(W(run_book(BOOK + G)))
        print(fmt(f"   window + D NIFTY k{k} 2R, no volume test [+{len(G)}]", s)
              + f" | vs window base dTOT {s['y'] - bw['y']:+,.0f}")
        for x in (1.2, 1.5, 2.0):
            Cv = sweep_entries(IX, PP, k, 2.0, only=["NIFTY"], vol=vol, vol_x=x)
            G = add_src(Cv, "SWEEP")
            tr = W(run_book(BOOK + G))
            s = stat(tr)
            print(fmt(f"   window + D NIFTY k{k} 2R, sweep-bar vol >= {x:g}x [+{len(G)}]", s)
                  + f" | vs window base dTOT {s['y'] - bw['y']:+,.0f}")

    # ---------------------------------------------------------------- 6. exits
    print("\n== 6. Exit rules on the book (OB + LHM re-priced with the exit changed; entries unchanged)")
    rawC = {(t["u"], t["i"]): t for t in K["C"]}
    rawL = {(t["u"], t["i"]): t for t in K["LHM_RAW"]}
    srcs = [(rawC[(t["u"], t["i"])], t) for t in OB] + [(rawL[(t["u"], t["i"])], t) for t in LHM]

    def rebook(**kw):
        out = []
        for raw, cooked in srcs:
            r = reprice_x(raw, IX[raw["u"]], PP[raw["u"]], **kw)
            if r:
                u = dict(cooked); u.update(r); out.append(u)
        return out
    chk = rebook(be_r=1.0)
    same = run_book(chk)
    assert len(chk) == len(BOOK) and abs(stat(same)["y"] - BASE["y"]) < 1, "reprice_x != realistic.reprice"
    print("   (check: reprice_x with be_r=1.0 reproduces the baseline exactly)")
    for be in (None, 0.75, 1.0, 1.25, 1.5):
        rec("6a breakeven trigger (premium R)", f"6a breakeven at {be if be is not None else 'never'}"
            + (" R (= engine, baseline)" if be == 1.0 else (" R" if be else "")),
            dict(be=be if be is not None else 99.0), run_book(rebook(be_r=be)))
    for mode in ("orig", "run"):
        for frac in (0.5, 0.8, 1.0):
            for stp in (10, 15, 20):
                if frac == 1.0 and stp != 15:
                    continue
                rec(f"6b partial at 2R, rest {mode}", f"6b book {frac:.0%} at 2R, rest +1R stop, trail {stp} pts, {mode}",
                    dict(frac=frac, step=stp), run_book(rebook(be_r=1.0, part=dict(frac=frac, at_r=2.0, mode=mode, step=stp))))
    from common import live_halt_pct
    lim = live_halt_pct() * 100
    dp = {}
    for t in base:
        dp[t["day"]] = dp.get(t["day"], 0) + t["net2"]
    print(f"   (6b: every trade here is ONE lot - NIFTY 65 / BANKNIFTY 30 / SENSEX 20 units - so an 80% or 50% "
          f"partial cannot be placed at this capital; the fractional rows are hypothetical, 100% at 2R is the "
          f"executable version)")
    print(f"   (6c: loss limit Rs {lim:,.0f}; baseline best day Rs {max(dp.values()):,.0f}; days at or above "
          f"Rs {2 * lim:,.0f}: {sum(1 for v in dp.values() if v >= 2 * lim)})")
    for x in (1.5, 2.0, 3.0):
        rec("6c daily profit cap", f"6c stop for the day at +{x:g} x loss limit (Rs {x * lim:,.0f})",
            dict(x=x), run_book(BOOK, profit_cap=x * lim))
    for n in (2, 3, 4):
        rec("6d max trades a day (his N=3)", f"6d at most {n} trades a day", dict(n=n), run_book(BOOK, max_trades=n))

    plateau_table()
    print(f"\n(run time {time.time() - t_start:.0f}s)")


if __name__ == "__main__":
    main()
