#!/usr/bin/env python3
"""More trades per session without more risk per trade (owner, 7 Oct 2026: "at
least 1-2 trades a day, profitable in both halves"). Baseline is the live book,
OB + LHM (research/portfolio_result.txt, research/sahil_rules_result.txt):

  OB   ob_fvg_retest, live pace gate (atr_by_index), 4h trend
  LHM  last-half-hour momentum, NIFTY only, 15:00 entry, exit 15:13

Everything is priced the same way (realistic.reprice: the contract the engine
would buy, costs inside the 8% cap, next expiry on expiry day) and run through
fixes_test.run(..., side_cooldown=30): one position at a time across indices,
F4 30 min, the live daily halt.

  1. LHM extensions: BANKNIFTY / SENSEX alone and with NIFTY (live order: the
     first index in lhm_indices with a signal takes the slot), entry 14:45 /
     15:00 / 15:05 / 15:10, adr_frac 0.3 / 0.4 / 0.5, stop 1.0 / 1.5 / 2.0 ATR
  2. every other playbook in arb/playbooks.py at the live gates, added to the book
     (candidates: research_out/freq_1007/build_pb.py, same pipeline as the OB cache)
  3. opening-session momentum: opening-range breakout and first-hour continuation
     with the 4h trend (with and without the live pace gate)
  4. per-index pace exceptions on OB (one index-half at a time, then combined)
  5. combinations aiming at >= 1 trade/session, both halves positive, max DD <= ~5,000

A variant COUNTS only if it beats the baseline in BOTH halves (split 2026-05-25)
and every one-parameter neighbour in its family does too (plateau). Every variant
is printed.

    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/frequency_test.py \
        > research/frequency_result.txt
"""
import os, sys, pickle, time, itertools
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research_out", "no_retest"),
                os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
OUT = os.path.join(ROOT, "research_out", "freq_1007")

import screen as S                                      # noqa: E402
import sahil_rules_test as M                            # noqa: E402  (no side effects outside main)
from common import Features, gate_halves, summ, SPLIT   # noqa: E402
from playbook_lab import INDICES                        # noqa: E402
from fixes_test import run as fx_run                    # noqa: E402
from flow_noise_robust import atr_ratio_series          # noqa: E402
from arb.playbooks import PLAYBOOKS                     # noqa: E402

L, rz, CAPITAL = M.L, M.rz, M.CAPITAL
hm, T = M.hm, M.T
LIVE_PACE = dict(L.atr_by_index)


def run(ts):
    return fx_run(ts, side_cooldown=30)


# ----------------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------------
ROWS = []
SESS = None
BASE = None


def stat(tr):
    s = summ(tr)
    days = {t["day"] for t in tr}
    return dict(n=s["n"], win=s["win"], i=s["i"], o=s["o"], y=s["y"], dd=s["dd"],
                ps=len(tr) / SESS, cov=100.0 * len(days) / SESS,
                by={k: sum(1 for t in tr if t.get("strat") == k) for k in sorted({t.get("strat", "?") for t in tr})})


def fmt(label, s, base=None):
    flag = ""
    if base is not None:
        both = s["i"] > base["i"] and s["o"] > base["o"]
        flag = (f" | dIN {s['i'] - base['i']:+7,.0f} dOUT {s['o'] - base['o']:+7,.0f}"
                + ("  BETTER BOTH" if both else ""))
    return (f"{label:58s} n={s['n']:3d} win={s['win']:3.0f}% IN {s['i']:+7,.0f} OUT {s['o']:+7,.0f} "
            f"tot {s['y']:+7,.0f} DD {s['dd']:6,.0f} {s['ps']:.2f}/s {s['cov']:3.0f}%days{flag}")


def rec(family, label, params, tr, show_by=False, keep=True):
    s = stat(tr)
    if keep:
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
        vals = sorted({q.get(k) for _, q, _ in fam}, key=lambda v: (str(type(v)), v))
        if all(isinstance(v, (int, float)) for v in vals):
            if abs(vals.index(params[k]) - vals.index(p[k])) != 1:
                continue
        out.append((lab, s))
    return out


def verdict(f, p, s):
    nb = neighbours(f, p)
    both = better(s)
    ok = both and nb and all(better(x) for _, x in nb)
    return ok, both, nb


# ----------------------------------------------------------------------------
# candidate generators
# ----------------------------------------------------------------------------
def lhm_gen(name, ix, P, at="15:00", adr_frac=0.5, minr=1.5, last="15:13"):
    """portfolio_test.lhm_gen with the entry minute, ADR fraction and stop free."""
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


def walk(P, i, side, stop, tgt, last):
    o, h, l, c, day = P["o"], P["h"], P["l"], P["c"], P["day"]
    for j in range(i + 1, len(c)):
        if day[j] != day[i] or hm(P["iso"][j]) + 1 > last:
            return j - 1, c[j - 1], "squareoff"
        if (side > 0 and l[j] <= stop) or (side < 0 and h[j] >= stop):
            return j, (min(o[j], stop) if side > 0 else max(o[j], stop)), "stop"
        if tgt is not None and ((side > 0 and h[j] >= tgt) or (side < 0 and l[j] <= tgt)):
            return j, (max(o[j], tgt) if side > 0 else min(o[j], tgt)), "target"
    return len(c) - 1, c[-1], "eod"


def mk(name, ix, P, i, side, stop, rmult, play, last):
    """Stop floored at the engine's min_stop_atr x ATR20; target rmult x R (None = time exit)."""
    c0 = P["c"][i]; a = P["atr"][i]
    risk = abs(c0 - stop)
    if a <= 0 or risk <= 0:
        return None
    if risk < L.min_stop_atr * a:
        risk = L.min_stop_atr * a; stop = c0 - side * risk
    tgt = c0 + side * rmult * risk if rmult else None
    j, px, why = walk(P, i, side, stop, tgt, last)
    if j <= i:
        return None
    return S.cand(name, ix, i, j, px, side, risk, why, play)


def orb_gen(IX, PP, orm, stop_mode, rmult, until, last="15:00"):
    """Opening-range breakout: range 09:15..09:15+orm; the first bar that CLOSES
    beyond it, at or before `until`, entered at that close. Stop: OR mid / the
    far side / 1.5 ATR; target rmult x R or a time exit."""
    out = []
    for u in INDICES:
        P = PP[u]; ix = IX[u]
        for d in P["keys"]:
            idx = P["D"][d]["idx"]
            end = T("09:15") + orm
            rng = [k for k in idx if hm(P["iso"][k]) < end]
            if len(rng) < orm - 2:
                continue
            hi = P["h"][rng].max(); lo = P["l"][rng].min()
            for k in idx:
                t = hm(P["iso"][k]) + 1
                if t <= end:
                    continue
                if t > T(until):
                    break
                side = 1 if P["c"][k] > hi else -1 if P["c"][k] < lo else 0
                if not side:
                    continue
                a = P["atr"][k]
                stop = {"mid": (hi + lo) / 2, "far": lo if side > 0 else hi,
                        "atr": P["c"][k] - side * 1.5 * a}[stop_mode]
                tt = mk(u, ix, P, k, side, stop, rmult, "ORB", T(last))
                if tt:
                    out.append(tt)
                break                                # first break of the day only
    return out


def fhc_gen(IX, PP, at, frac, minr, rmult, last, ref="o"):
    """First-hour continuation: at the bar ending `at`, if the index is >= frac x
    ADR from the open (or prev close), go with it; stop minr x ATR; target or time exit."""
    out = []
    for u in INDICES:
        P = PP[u]; ix = IX[u]
        for d in P["keys"]:
            dd = P["D"][d]
            if not dd["adr"]:
                continue
            for k in dd["idx"]:
                if hm(P["iso"][k]) + 1 != T(at):
                    continue
                mv = P["c"][k] - dd[ref]
                if abs(mv) < frac * dd["adr"]:
                    break
                side = 1 if mv > 0 else -1
                tt = mk(u, ix, P, k, side, P["c"][k] - side * minr * P["atr"][k], rmult, "FHC", T(last))
                if tt:
                    out.append(tt)
                break
    return out


def orb2_gen(IX, PP, orm, stop_mode, rmult, until, last):
    """orb_gen with the range length free (5 / 15 min) and the exit time free."""
    return orb_gen(IX, PP, orm, stop_mode, rmult, until, last)


def at_bar(P, d, at):
    for k in P["D"][d]["idx"]:
        if hm(P["iso"][k]) + 1 == T(at):
            return k
    return None


def f15_gen(IX, PP, at, frac, minr, rmult, last, ref="o"):
    """First-15-min continuation: fhc_gen at an opening bar (09:30 by default)."""
    return fhc_gen(IX, PP, at, frac, minr, rmult, last, ref)


def gap_gen(IX, PP, mode, g, at, minr, rmult, last):
    """Gap-and-go / gap-fade. Gap = (open - prev close) / ADR(10), |gap| >= g.
    go:   at `at`, if price is still beyond the open in the gap direction, enter WITH the gap.
    fade: at `at`, if price is back inside / at the open side, enter AGAINST the gap,
          target = prev close (gap fill) when rmult is 'fill', else rmult x R."""
    out = []
    for u in INDICES:
        P = PP[u]; ix = IX[u]
        for d in P["keys"]:
            dd = P["D"][d]
            if not dd["adr"] or dd["pdh"] is None:
                continue
            gp = (dd["o"] - dd["pc"]) / dd["adr"]
            if abs(gp) < g:
                continue
            gs = 1 if gp > 0 else -1
            k = at_bar(P, d, at)
            if k is None:
                continue
            c0 = P["c"][k]
            if mode == "go":
                if (c0 - dd["o"]) * gs <= 0:
                    continue
                side = gs
            else:
                if (c0 - dd["o"]) * gs > 0:
                    continue
                side = -gs
            a = P["atr"][k]
            stop = c0 - side * minr * a
            if rmult == "fill":
                risk = max(abs(c0 - stop), L.min_stop_atr * a); stop = c0 - side * risk
                tgt = dd["pc"]
                if (tgt - c0) * side <= 0:
                    continue
                j, px, why = walk(P, k, side, stop, tgt, T(last))
                if j > k:
                    out.append(S.cand(u, ix, k, j, px, side, risk, why, "GAP"))
            else:
                tt = mk(u, ix, P, k, side, stop, rmult, "GAP", T(last))
                if tt:
                    out.append(tt)
    return out


def flow_gen(IX, PP, AR, k5, rmin, minr, exit_mode, levels, lo="09:20", hi="10:30", trail_atr=1.0):
    """Owner's "flow riding": a fast 5-minute move (|c - c[-5]| >= k5 x ATR20, ATR
    ratio >= rmin) TOWARD a level (prev-day high/low and/or prev close) 0.5-3 ATR
    away -> enter with the flow at once. Exit: 'tgt' = the level minus 0.1 ATR;
    'trail' = once the level is touched the stop trails trail_atr x ATR behind the
    best price, else squareoff 15:00. Stop minr x ATR. First signal per index per day."""
    out = []
    for u in INDICES:
        P = PP[u]; ix = IX[u]; o, h, l, c, atr = P["o"], P["h"], P["l"], P["c"], P["atr"]
        ar = AR[u]
        for d in P["keys"]:
            dd = P["D"][d]
            if dd["pdh"] is None:
                continue
            idx = dd["idx"]
            for n, i in enumerate(idx):
                t = hm(P["iso"][i]) + 1
                if t < T(lo) or n < 5:
                    continue
                if t > T(hi):
                    break
                a = atr[i]
                if a <= 0 or ar[i] < rmin:
                    continue
                mv = c[i] - c[i - 5]
                if abs(mv) < k5 * a:
                    continue
                side = 1 if mv > 0 else -1
                lv = []
                if "hl" in levels:
                    lv.append(dd["pdh"] if side > 0 else dd["pdl"])
                if "pc" in levels:
                    lv.append(dd["pc"])
                lv = [x for x in lv if 0.5 * a <= (x - c[i]) * side <= 3.0 * a]
                if not lv:
                    continue
                level = min(lv, key=lambda x: abs(x - c[i]))
                risk = max(minr, L.min_stop_atr) * a
                stop = c[i] - side * risk
                tgt = level - side * 0.1 * a
                touched, best = False, c[i]
                j, px, why = None, None, None
                for q in range(i + 1, len(c)):
                    if P["day"][q] != d or hm(P["iso"][q]) + 1 > T("15:00"):
                        j, px, why = q - 1, c[q - 1], "squareoff"; break
                    if (side > 0 and l[q] <= stop) or (side < 0 and h[q] >= stop):
                        j, px, why = q, (min(o[q], stop) if side > 0 else max(o[q], stop)), \
                            ("trail_stop" if touched else "stop"); break
                    hit = (h[q] >= tgt) if side > 0 else (l[q] <= tgt)
                    if exit_mode == "tgt" and hit:
                        j, px, why = q, (max(o[q], tgt) if side > 0 else min(o[q], tgt)), "target"; break
                    if exit_mode == "trail":
                        touched = touched or hit
                        best = max(best, h[q]) if side > 0 else min(best, l[q])
                        if touched:
                            lvl = best - side * trail_atr * a
                            stop = max(stop, lvl) if side > 0 else min(stop, lvl)
                if j is None or j <= i:
                    break
                out.append(S.cand(u, ix, i, j, px, side, risk, why, "FLOW"))
                break
    return out


def main():
    global SESS, BASE
    t_start = time.time()
    K = pickle.load(open(M.PKL, "rb"))
    assert abs(K["capital"] - CAPITAL) < 1e-6 and K["risk"] == L.max_risk_pct_per_trade, "stale cache"
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    PP = {n: M.prep(IX[n]) for n in INDICES}                 # sahil prep (o/h/l/c, ATR20, day ctx)
    sp_p = os.path.join(OUT, "screen_prep.pkl")
    if os.path.exists(sp_p):
        SP = pickle.load(open(sp_p, "rb"))
    else:
        SP = {n: S.prep(IX[n]) for n in INDICES}
        pickle.dump(SP, open(sp_p, "wb"))
    AR = {n: atr_ratio_series(SP[n]) for n in INDICES}
    SESS = len(PP["NIFTY"]["keys"])
    M.SESS = SESS
    OB, LHM = K["OB"], K["LHM"]
    base_tr = run(OB + LHM)
    BASE = stat(base_tr)
    print(f"capital Rs {CAPITAL:,.2f}, risk cap {L.max_risk_pct_per_trade:g}%, daily halt {L.max_daily_loss_pct:g}%, "
          f"split {SPLIT}, sessions {SESS}; live pace {LIVE_PACE}")
    print("Columns: n, win%, IN (before split), OUT (after), total, max drawdown, trades/session, % of sessions "
          "with >= 1 trade; dIN/dOUT vs baseline.\n")
    print(fmt("BASELINE OB + LHM(NIFTY 15:00 0.5 ADR 1.5 ATR)", BASE) + f" {BASE['by']}\n")

    trend = lambda t: F.h4_ret(t, 5) * t["side"] > 0

    def add_src(C, strat, pace=False, use_trend=True):
        out = []
        for t in M.reprice(C, IX):
            if use_trend and not trend(t):
                continue
            t["ar"] = float(AR[t["u"]][t["i"]])
            if pace and not gate_halves(t, *LIVE_PACE.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio))):
                continue
            out.append(dict(t, strat=strat))
        return out

    # ------------------------------------------------------------- 1. LHM
    print("== 1. LHM extensions. Book = OB + LHM on the listed indices; on a day several fire at the same "
          "minute the FIRST listed index with a priced, trend-agreeing signal takes the slot (live _live_lhm order)")
    LH = {}
    t0 = time.time()
    for u in INDICES:
        for at in ("14:45", "15:00", "15:05", "15:10"):
            for af in (0.3, 0.4, 0.5):
                for mr in (1.0, 1.5, 2.0):
                    LH[(u, at, af, mr)] = [dict(t, strat=f"LHM_{u[0]}") for t in
                                           M.reprice(lhm_gen(u, IX[u], SP[u], at, af, mr), IX) if trend(t)]
    chk = LH[("NIFTY", "15:00", 0.5, 1.5)]
    assert [round(t["net"], 6) for t in chk] == [round(t["net"], 6) for t in LHM], "LHM regen != cache"
    print(f"   (check: NIFTY 15:00/0.5/1.5 regenerated = cached LHM, {len(chk)} candidates; built in "
          f"{time.time() - t0:.0f}s)")

    def prio(sets):
        """One LHM per day: the first index in `sets` that has a candidate."""
        by = {}
        for k, ts in enumerate(sets):
            for t in ts:
                by.setdefault(t["day"], []).append((k, t))
        return [min(v, key=lambda x: x[0])[1] for v in by.values()]

    print("   -- LHM alone per index (no OB), 15:00 / 0.5 ADR / 1.5 ATR and the full grid summary:")
    for u in INDICES:
        for at in ("14:45", "15:00", "15:05", "15:10"):
            for af in (0.3, 0.4, 0.5):
                for mr in (1.0, 1.5, 2.0):
                    rec(f"info LHM alone {u}", f"   LHM alone {u} {at} adr{af:g} stop{mr:g}",
                        dict(at=at, af=af, mr=mr), run(LH[(u, at, af, mr)]), keep=False)
    ISETS = [("N",), ("B",), ("S",), ("N", "B"), ("N", "S"), ("N", "B", "S"), ("N", "S", "B")]
    full = {"N": "NIFTY", "B": "BANKNIFTY", "S": "SENSEX"}
    for iset in ISETS:
        fam = f"1 LHM {'+'.join(iset)}"
        print(f"   -- book = OB + LHM[{'>'.join(iset)}]")
        for at in ("14:45", "15:00", "15:05", "15:10"):
            for af in (0.3, 0.4, 0.5):
                for mr in (1.0, 1.5, 2.0):
                    if iset == ("N",) and (at, af, mr) == ("15:00", 0.5, 1.5):
                        print("      (baseline)"); continue
                    lh = prio([LH[(full[x], at, af, mr)] for x in iset])
                    rec(fam, f"OB+LHM[{'>'.join(iset)}] {at} adr{af:g} stop{mr:g}",
                        dict(at=T(at), af=af, mr=mr), run(OB + lh), show_by=len(iset) > 1)
    print("   -- staggered entry times (needs a per-index lhm_entry_at; not in the engine today)")
    STAG = [(("NIFTY", "15:00"), ("SENSEX", "15:05")), (("NIFTY", "15:00"), ("BANKNIFTY", "15:05")),
            (("NIFTY", "15:00"), ("SENSEX", "15:05"), ("BANKNIFTY", "15:10")),
            (("SENSEX", "14:45"), ("NIFTY", "15:00")), (("BANKNIFTY", "14:45"), ("NIFTY", "15:00"))]
    for st in STAG:
        for af in (0.4, 0.5):
            lh = [t for u, at in st for t in LH[(u, at, af, 1.5)]]
            rec(f"1 LHM staggered {st}", f"OB+LHM stag {' '.join(u[0] + at for u, at in st)} adr{af:g}",
                dict(af=af), run(OB + lh), show_by=True)

    # ------------------------------------------------------------- 2. playbooks
    print("\n== 2. Other playbooks in arb/playbooks.py, live gates (per-index pace + 4h trend), added to the book")
    print(f"   registry: {list(PLAYBOOKS)}  (live: {list(L.playbooks)})")
    PB = {}
    import glob
    names = [n for n in PLAYBOOKS if n not in L.playbooks]
    for n in names:
        if not os.path.exists(os.path.join(OUT, f"pb_{n}.pkl")):
            print(f"   {n}: candidates not built")
    tags = sorted(os.path.basename(q)[3:-4] for q in glob.glob(os.path.join(OUT, "pb_*.pkl")))
    for name in tags:
        Z = pickle.load(open(os.path.join(OUT, f"pb_{name}.pkl"), "rb"))
        assert abs(Z["capital"] - CAPITAL) < 1e-6 and Z["risk"] == L.max_risk_pct_per_trade, "stale cache"
        if not Z["C"]:
            print(f"   {name}: raw 0 candidates at the live config - nothing to add")
            continue
        G = [dict(t, strat=name) for t in Z["ALL"] if trend(t) and
             gate_halves(t, *LIVE_PACE.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
        PB[name] = G
        a = stat(run(G))
        print(f"   {name}: raw {len(Z['C'])} priced {len(Z['ALL'])} after live gates {len(G)}")
        rec("info playbook alone", f"   {name} alone", {}, run(G), keep=False)
        rec(f"2 playbook {name}", f"OB+LHM + {name} [alone n={a['n']} {a['y']:+,.0f}]", {}, run(OB + LHM + G),
            show_by=True)
        for u in INDICES:
            Gu = [t for t in G if t["u"] == u]
            rec(f"2 playbook {name} by index", f"OB+LHM + {name} {u} only", dict(u=u), run(OB + LHM + Gu),
                show_by=True)

    # ------------------------------------------------------------- 3. opening momentum
    print("\n== 3. Opening-session momentum (4h trend with the trade; 'pace' = also the live per-index pace gate)")
    print("   -- ORB: opening range 09:15+orm, first close beyond it by `until`, stop mid/far/1.5ATR (floor "
          f"{L.min_stop_atr:g} ATR), target rR or squareoff 15:00 (r=0)")
    OPEN = {}
    for pace in (False, True):
        for orm in (15, 30):
            for sm in ("mid", "far", "atr"):
                for rm in (1.5, 2.0, 3.0, 0):
                    for until in ("10:30", "11:00"):
                        G = add_src(orb_gen(IX, PP, orm, sm, rm, until), "ORB", pace)
                        tag = "pace" if pace else "4h"
                        OPEN[("ORB", tag, orm, sm, rm, until)] = G
                        a = stat(run(G))
                        rec(f"3 ORB {tag} {sm}", f"OB+LHM + ORB {tag} or{orm} {sm} {rm:g}R <={until} "
                            f"[alone n={a['n']} {a['y']:+,.0f}]", dict(orm=orm, rm=rm, until=T(until)),
                            run(OB + LHM + G), show_by=True)
    print("   -- First-hour continuation: at `at`, move from the open >= frac x ADR(10), with it; stop minr ATR; "
          "target 2R or none; exit by `last`")
    for pace in (False, True):
        for at in ("09:45", "10:00", "10:15", "10:30"):
            for fr in (0.2, 0.3, 0.4):
                for mr in (1.0, 1.5, 2.0):
                    for rm, last in ((2.0, "15:00"), (0, "12:15"), (0, "15:00")):
                        G = add_src(fhc_gen(IX, PP, at, fr, mr, rm, last), "FHC", pace)
                        tag = "pace" if pace else "4h"
                        OPEN[("FHC", tag, at, fr, mr, rm, last)] = G
                        a = stat(run(G))
                        rec(f"3 FHC {tag} {rm:g}R/{last}", f"OB+LHM + FHC {tag} {at} {fr:g}ADR stop{mr:g} "
                            f"{rm:g}R/{last} [alone n={a['n']} {a['y']:+,.0f}]",
                            dict(at=T(at), fr=fr, mr=mr), run(OB + LHM + G), show_by=True)

    # ------------------------------------------------------------- 3b. opening window
    print("\n== 3b. Opening window 09:15-09:30 (owner, 7 Oct 2026: a SEPARATE opening strategy; OB keeps its "
          "15-min warmup). Priced the same way; '4h' = 4h trend with the trade, 'no4h' = no trend filter. "
          "Each row: the strategy ALONE (IN/OUT) and added to OB + LHM.")

    def both_alone(G):
        a = stat(run(G))
        return a, f"[alone n={a['n']} IN {a['i']:+,.0f} OUT {a['o']:+,.0f}{' POS-BOTH' if a['i'] > 0 and a['o'] > 0 else ''}]"
    ALONE_POS = []
    print("   -- ORB: range 09:15+orm (5 -> first entry 09:20+, 15 -> 09:30+), first close beyond it by 10:30, "
          "stop mid/far/1.5ATR (floor 1 ATR), target rR or time exit")
    for tr_on in (True, False):
        tag = "4h" if tr_on else "no4h"
        for orm in (5, 15):
            for sm in ("mid", "far", "atr"):
                for rm, last in ((2.0, "15:00"), (0, "11:00"), (0, "15:00")):
                    G = add_src(orb_gen(IX, PP, orm, sm, rm, "10:30", last), "ORB", use_trend=tr_on)
                    a, lab = both_alone(G)
                    if a["i"] > 0 and a["o"] > 0:
                        ALONE_POS.append((f"ORB {tag} or{orm} {sm} {rm:g}R/{last}", a))
                    rec(f"3b ORB {tag} {sm} {rm:g}R/{last}", f"+ ORB {tag} or{orm} {sm} {rm:g}R/{last} {lab}",
                        dict(orm=orm), run(OB + LHM + G), show_by=False)
    print("   -- first-15-min continuation: at 09:30 (and 09:20), move from the open (or prev close) >= frac x ADR, "
          "with it; stop minr ATR; target 2R or time exit")
    for tr_on in (True, False):
        tag = "4h" if tr_on else "no4h"
        for ref in ("o", "pc"):
            for at in ("09:20", "09:30"):
                for fr in (0.1, 0.15, 0.2, 0.3, 0.4):
                    for mr in (1.0, 1.5):
                        for rm, last in ((2.0, "15:00"), (0, "11:00"), (0, "15:00")):
                            G = add_src(f15_gen(IX, PP, at, fr, mr, rm, last, ref), "F15", use_trend=tr_on)
                            OPEN[("F15", tag, ref, at, fr, mr, rm, last)] = G
                            a, lab = both_alone(G)
                            if a["i"] > 0 and a["o"] > 0:
                                ALONE_POS.append((f"F15 {tag} {ref} {at} {fr:g} s{mr:g} {rm:g}R/{last}", a))
                            rec(f"3b F15 {tag} ref {ref} {rm:g}R/{last}",
                                f"+ F15 {tag} {ref} {at} {fr:g}ADR s{mr:g} {rm:g}R/{last} {lab}",
                                dict(at=T(at), fr=fr, mr=mr), run(OB + LHM + G))
    print("   -- gap-and-go / gap-fade: |open - prev close| >= g x ADR(10); decided at 09:20 or 09:30; "
          "go = still beyond the open with the gap; fade = back through the open, target the gap fill (prev close) "
          "or 2R")
    for tr_on in (True, False):
        tag = "4h" if tr_on else "no4h"
        for mode in ("go", "fade"):
            for g in (0.2, 0.3, 0.5):
                for at in ("09:20", "09:30"):
                    for mr in (1.0, 1.5):
                        for rm, last in ((("fill" if mode == "fade" else 2.0), "15:00"), (0, "11:00"), (0, "15:00")):
                            G = add_src(gap_gen(IX, PP, mode, g, at, mr, rm, last), "GAP", use_trend=tr_on)
                            OPEN[("GAP", mode, tag, g, at, mr, rm, last)] = G
                            a, lab = both_alone(G)
                            if a["i"] > 0 and a["o"] > 0:
                                ALONE_POS.append((f"GAP {mode} {tag} g{g:g} {at} s{mr:g} {rm}/{last}", a))
                            rec(f"3b GAP {mode} {tag} {rm}/{last}",
                                f"+ GAP {mode} {tag} g{g:g} {at} s{mr:g} {rm}/{last} {lab}",
                                dict(g=g, at=T(at), mr=mr), run(OB + LHM + G))

    # ------------------------------------------------------------- 3c. flow riding
    print("\n== 3c. Flow riding (owner's idea): 09:20-10:30, 5-min move >= k5 x ATR and ATR ratio >= r, TOWARD "
          "prev-day high/low ('hl') or prev close ('pc') 0.5-3 ATR away; enter with the flow; exit at the level "
          "-0.1 ATR ('tgt') or trail 1 ATR once the level is touched ('trail', else squareoff 15:00); stop minr ATR")
    for tr_on in (True, False):
        tag = "4h" if tr_on else "no4h"
        for levels in ("hl", "pc", "hl+pc"):
            for ex in ("tgt", "trail"):
                for k5 in (1.5, 2.0, 2.5, 3.0):
                    for rmin in (1.5, 2.0):          # atr_ratio_series is capped at 2.0
                        for mr in (1.0, 1.5):
                            G = add_src(flow_gen(IX, PP, AR, k5, rmin, mr, ex, levels), "FLOW", use_trend=tr_on)
                            OPEN[("FLOW", tag, levels, ex, k5, rmin, mr)] = G
                            a, lab = both_alone(G)
                            if a["i"] > 0 and a["o"] > 0:
                                ALONE_POS.append((f"FLOW {tag} {levels} {ex} k{k5:g} r{rmin:g} s{mr:g}", a))
                            rec(f"3c FLOW {tag} {levels} {ex}", f"+ FLOW {tag} {levels} {ex} k{k5:g} r{rmin:g} "
                                f"s{mr:g} {lab}", dict(k5=k5, rmin=rmin, mr=mr), run(OB + LHM + G))
    print(f"\n   opening-window / flow variants positive in BOTH halves ALONE: {len(ALONE_POS)}")
    for lab, a in ALONE_POS:
        print(f"     {lab:52s} n={a['n']:3d} win={a['win']:3.0f}% IN {a['i']:+7,.0f} OUT {a['o']:+7,.0f} "
              f"DD {a['dd']:6,.0f} {a['ps']:.2f}/s")

    # ------------------------------------------------------------- 4. pace exceptions
    print("\n== 4. Per-index pace exceptions on OB (all priced OB candidates, 4h trend, gate (morning, afternoon) "
          "split 12:15); book = OB(variant) + LHM")
    ALL = K["ALL"]
    ALLT = [t for t in ALL if trend(t)]

    def ob_with(pace):
        return [dict(t, strat="OB") for t in ALLT if gate_halves(t, *pace.get(t["u"], (L.min_atr_ratio,) * 2))]
    chk = ob_with(LIVE_PACE)
    assert len(chk) == len(OB), f"OB regen {len(chk)} != cache {len(OB)}"
    GRID = {"NIFTY": (1.0, 1.1, 1.2, 1.3, 1.4, 1.5), "BANKNIFTY": (1.1, 1.2, 1.3, 1.4, 1.5, 1.6),
            "SENSEX": (0.9, 1.0, 1.1, 1.2, 1.3)}
    for u in INDICES:
        for half in (0, 1):
            for v in GRID[u]:
                cur = list(LIVE_PACE[u])
                if abs(cur[half] - v) < 1e-9:
                    continue
                cur[half] = v
                pace = dict(LIVE_PACE); pace[u] = tuple(cur)
                rec(f"4 pace {u} {'am' if half == 0 else 'pm'}", f"OB+LHM pace {u} {'am' if half == 0 else 'pm'}"
                    f" {v:g} ({cur[0]:g}/{cur[1]:g})", dict(v=v), run(ob_with(pace) + LHM), show_by=False)
        for v in GRID[u]:
            if LIVE_PACE[u] == (v, v):
                continue
            pace = dict(LIVE_PACE); pace[u] = (v, v)
            rec(f"4 pace {u} both", f"OB+LHM pace {u} both {v:g}", dict(v=v), run(ob_with(pace) + LHM))

    # ------------------------------------------------------------- 5. combinations
    print("\n== 5. Combinations (components chosen from sections 1-4: best both-halves rows); target >= 1 "
          "trade/session, both halves positive, max DD <= ~5,000")
    singles = [(f, lab, p, s) for f, lab, p, s in ROWS if not f.startswith("info")]
    passing = [(f, lab, p, s) for f, lab, p, s in singles if verdict(f, p, s)[0]]
    print(f"   {len(passing)} single variants pass (both halves + plateau); best by trades/session:")
    for f, lab, p, s in sorted(passing, key=lambda r: -r[3]["ps"])[:25]:
        print(fmt("    " + lab, s, BASE))
    pickle.dump(dict(ROWS=ROWS, BASE=BASE), open(os.path.join(OUT, "rows.pkl"), "wb"))
    combos(LH, PB, OPEN, OB, LHM, ob_with, prio)
    plateau_table()
    print(f"\n(run time {time.time() - t_start:.0f}s)")


def combos(LH, PB, OPEN, OB, LHM, ob_with, prio):
    """Section 5: a grid over one option per component. Each dimension is an
    ordered list, so a one-step move along one dimension is a neighbour."""
    PACES = [("live", {}), ("S1.1", {"SENSEX": (1.1, 1.1)}), ("S1.0", {"SENSEX": (1.0, 1.0)}),
             ("S0.9", {"SENSEX": (0.9, 0.9)}),
             ("S1.0 B1.3", {"SENSEX": (1.0, 1.0), "BANKNIFTY": (1.3, 1.3)}),
             ("S1.0 B1.3 N1.2", {"SENSEX": (1.0, 1.0), "BANKNIFTY": (1.3, 1.3), "NIFTY": (1.2, 1.2)})]
    LHMS = [("N.5", [LH[("NIFTY", "15:00", 0.5, 1.5)]]), ("N.4", [LH[("NIFTY", "15:00", 0.4, 1.5)]]),
            ("N.3", [LH[("NIFTY", "15:00", 0.3, 1.5)]]),
            ("N>S.3", [LH[("NIFTY", "15:00", 0.3, 1.5)], LH[("SENSEX", "15:00", 0.3, 1.5)]])]
    FHCS = [("none", []), ("4h .4", OPEN[("FHC", "4h", "10:15", 0.4, 1.0, 0, "15:00")]),
            ("4h .3", OPEN[("FHC", "4h", "10:15", 0.3, 1.0, 0, "15:00")]),
            ("4h .2", OPEN[("FHC", "4h", "10:15", 0.2, 1.0, 0, "15:00")])]
    PBS = [("none", [])] + [(k, v) for k, v in sorted(PB.items())]
    fl = OPEN[("FLOW", "no4h", "hl", "tgt", 2.0, 2.0, 1.5)]
    gp = OPEN[("GAP", "go", "4h", 0.2, "09:20", 1.5, 2.0, "15:00")]
    f15 = OPEN[("F15", "no4h", "o", "09:20", 0.3, 1.5, 0, "11:00")]
    MORN = [("none", []), ("FLOW", fl), ("FLOW+GAPgo", fl + gp), ("FLOW+GAPgo+F15", fl + gp + f15)]
    dims = [PACES, LHMS, FHCS, PBS, MORN]
    print(f"   grid: pace {[a for a, _ in PACES]} x LHM {[a for a, _ in LHMS]} x FHC(10:15, stop 1 ATR, "
          f"exit 15:00) {[a for a, _ in FHCS]} x playbook {[a for a, _ in PBS]} x morning {[a for a, _ in MORN]}")
    cache = {}

    def ev(ix):
        if ix in cache:
            return cache[ix]
        pace = dict(LIVE_PACE); pace.update(PACES[ix[0]][1])
        ts = (ob_with(pace) + prio(LHMS[ix[1]][1]) + list(FHCS[ix[2]][1]) + list(PBS[ix[3]][1])
              + list(MORN[ix[4]][1]))
        cache[ix] = stat(run(ts))
        return cache[ix]
    allx = list(itertools.product(*[range(len(d)) for d in dims]))
    for ix in allx:
        ev(ix)
    name = lambda ix: " | ".join(f"{d[k][0]}" for d, k in zip(dims, ix))
    ok = lambda s: s["i"] > 0 and s["o"] > 0 and s["dd"] <= 5000

    def nbs(ix):
        out = []
        for d in range(len(dims)):
            for step in (-1, 1):
                k = ix[d] + step
                if 0 <= k < len(dims[d]):
                    j = list(ix); j[d] = k; out.append(tuple(j))
        return out
    print(f"   {len(allx)} combinations")
    for title, filt in (("reaching >= 1.00 trade/session, BETTER than baseline in both halves, DD <= 5,000",
                         lambda s: ok(s) and s["ps"] >= 1.0 and better(s)),
                        ("reaching >= 1.00 trade/session, both halves positive, DD <= 5,000",
                         lambda s: ok(s) and s["ps"] >= 1.0),
                        ("best trades/session with both halves positive and DD <= 5,000", ok),
                        ("better than baseline in BOTH halves (any DD)", better)):
        hits = sorted([ix for ix in allx if filt(ev(ix))], key=lambda ix: (-ev(ix)["ps"], -ev(ix)["y"]))
        print(f"\n   -- {title}: {len(hits)} combinations")
        for ix in hits[:8]:
            s = ev(ix)
            nb = nbs(ix)
            n_ok = sum(1 for j in nb if ok(ev(j)))
            n_b = sum(1 for j in nb if better(ev(j)))
            print(fmt("   " + name(ix), s, BASE) + f"  nb ok {n_ok}/{len(nb)} nb better-both {n_b}/{len(nb)}")
        for ix in hits[:4]:
            print(f"      neighbours of [{name(ix)}]:")
            for j in nbs(ix):
                print(fmt("        " + name(j), ev(j), BASE))
    # Component-parameter plateau for the best >=1/s better-both combinations: keep everything
    # else, swap the FLOW and GAP-go settings for their grid neighbours.
    best = sorted([ix for ix in allx if ok(ev(ix)) and ev(ix)["ps"] >= 1.0 and better(ev(ix))
                   and "GAP" in MORN[ix[4]][0]], key=lambda ix: -ev(ix)["y"])[:2]
    FL_ALT = [("FLOW", "no4h", "hl", "tgt", k, r, m) for k, r, m in
              ((1.5, 2.0, 1.5), (2.5, 2.0, 1.5), (2.0, 1.5, 1.5), (2.0, 2.0, 1.0))] + \
             [("FLOW", "4h", "hl", "tgt", 2.0, 2.0, 1.5), ("FLOW", "no4h", "hl", "trail", 2.0, 2.0, 1.5),
              ("FLOW", "no4h", "pc", "tgt", 2.0, 2.0, 1.5), ("FLOW", "no4h", "hl+pc", "tgt", 2.0, 2.0, 1.5)]
    GP_ALT = [("GAP", "go", "4h", g, at, m, 2.0, "15:00") for g, at, m in
              ((0.3, "09:20", 1.5), (0.5, "09:20", 1.5), (0.2, "09:30", 1.5), (0.2, "09:20", 1.0))] + \
             [("GAP", "go", "no4h", 0.2, "09:20", 1.5, 2.0, "15:00"), ("GAP", "go", "4h", 0.2, "09:20", 1.5, 0, "11:00")]
    for ix in best:
        pace = dict(LIVE_PACE); pace.update(PACES[ix[0]][1])
        core = ob_with(pace) + prio(LHMS[ix[1]][1]) + list(FHCS[ix[2]][1]) + list(PBS[ix[3]][1])
        f15x = f15 if "F15" in MORN[ix[4]][0] else []
        print(f"\n   -- component plateau of [{name(ix)}] (FLOW = no4h hl tgt k2 r2 s1.5, GAP go = 4h g0.2 09:20 "
              f"s1.5 2R):")
        print(fmt("      as chosen", ev(ix), BASE))
        for key in FL_ALT:
            print(fmt(f"      FLOW -> {' '.join(map(str, key[1:]))}", stat(run(core + OPEN[key] + gp + f15x)), BASE))
        for key in GP_ALT:
            print(fmt(f"      GAP  -> {' '.join(map(str, key[2:]))}", stat(run(core + fl + OPEN[key] + f15x)), BASE))
        print(fmt("      no GAP (FLOW only)", stat(run(core + fl + f15x)), BASE))
        print(fmt("      no FLOW (GAP only)", stat(run(core + gp + f15x)), BASE))
        s_ = stat(run(core + fl + gp + f15x))
        tr_ = run(core + fl + gp + f15x)
        months = {}
        for t in tr_:
            months.setdefault(t["day"][:7], []).append(t["net2"])
        print("      per month: " + "  ".join(f"{m[2:]} {len(v)}/{sum(v):+,.0f}" for m, v in sorted(months.items())))
    top = sorted(allx, key=lambda ix: -ev(ix)["ps"])[:5]
    print("\n   -- for reference, the five combinations with the most trades/session (no constraint):")
    for ix in top:
        print(fmt("   " + name(ix), ev(ix), BASE))


def plateau_table():
    print("\n" + "=" * 140)
    print("ALL VARIANTS vs BASELINE (OB + LHM).  BOTH = better IN and OUT;  PASS = BOTH and every one-parameter "
          "neighbour in its family BOTH too")
    print("=" * 140)
    print(fmt("BASELINE OB + LHM", BASE))
    last = None
    passed = []
    first = {}
    for k, r in enumerate(ROWS):
        first.setdefault(r[0], k)
    for f, lab, p, s in sorted(ROWS, key=lambda r: first[r[0]]):
        if f.startswith("info"):
            continue
        if f != last:
            print(f"-- {f}"); last = f
        ok, both, nb = verdict(f, p, s)
        tag = "PASS" if ok else ("both, no plateau" if both else "")
        if ok:
            passed.append((f, lab, round(s["ps"], 2)))
        print(fmt("  " + lab, s, BASE) + f"  [{len(nb)} nb] {tag}")
    print("\nPASSING VARIANTS:", passed or "none")


if __name__ == "__main__":
    main()
