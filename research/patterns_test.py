#!/usr/bin/env python3
"""Classical chart patterns (research/patterns.py) on a year of 1-minute index
data, alone and added to the live book (OB + LHM), priced like the engine.

Owner, 7 Oct 2026: W patterns are visible on 1m - test patterns alongside the
candlesticks. Every pattern family x side x trigger x timeframe (1m / 3m / 5m,
resampled from 1m on the 09:15 grid) x size parameter x exit x live gates
ON/OFF is run; nothing is dropped from the table.

Pipeline per variant
  1. detect on CLOSED bars (patterns.py; look-ahead tested in
     patterns_selftest.py); entry = the close of the trigger bar, i.e. the
     1-minute row that closes it; entries 09:30-14:45 (warm-up 15 min, as the
     backtest), one per (index, bar, side)
  2. index exit, one of
        mm   the pattern's own measured-move target, the pattern stop, square
             off 15:00 (sahil_rules_test.mk: the stop widened to the engine's
             min_stop_atr x ATR20 if tighter)
        2R   the same with a 2R target
        eng  what wiring the pattern as a playbook would give: the pattern
             stop is the Setup's invalidation; the engine REFUSES a stop
             tighter than min_stop_atr x ATR20; target, extension, index trail
             and time stop from arb/trailing.py exactly as tools/backtest.py
             manages a playbook entry
  3. realistic.reprice: the contract the engine would buy, costs inside the
     8% cap, exit slip, next expiry on expiry day, premium stop and +1R
     breakeven every bar
  4. gates ON = the live per-index pace gate (atr_by_index halves on the
     engine's atr_ratio at the entry bar) AND the 4h trend with the trade;
     OFF = neither
  5. book: one position at a time across everything, F4 30 min, the live
     daily halt (sahil_rules_test.run_book == fixes_test.run(..., side_cooldown=30),
     asserted)

Baseline OB + LHM comes from research_out/sahil_1006/cands.pkl (159 trades,
+48,248, IN +27,094 / OUT +21,154, DD 3,649 at Rs 10,672.74, 8% risk).

    # heavy (detect + price, 2 workers; wait for frequency/mtf jobs first)
    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/patterns_test.py --build
    # every variant (minutes)
    nice -n 10 systemd-run --user --scope -q -p MemoryMax=3G venv/bin/python research/patterns_test.py > research/patterns_result.txt
"""
import os, sys, pickle, time, itertools
from multiprocessing import Pool
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
OUT = os.path.join(ROOT, "research_out", "patterns_1007")
BOOK_PKL = os.path.join(ROOT, "research_out", "sahil_1006", "cands.pkl")

import patterns as PT                                  # noqa: E402
import realistic as rz                                 # noqa: E402
import backtest as bt                                  # noqa: E402
from common import Features, gate_halves, summ, SPLIT  # noqa: E402
from playbook_lab import INDICES                       # noqa: E402
import sahil_rules_test as SR                          # noqa: E402
from arb import trailing                               # noqa: E402
from arb.positions import Position                     # noqa: E402
from arb.regime import atr as arb_atr                  # noqa: E402

L = SR.L
CAPITAL = SR.CAPITAL
cur = dict(L.atr_by_index)
T0, T1 = 9 * 60 + 15 + int(L.warmup_minutes), 14 * 60 + 45

# family -> (size parameter, values, extra configs {name: overrides}, mode labels)
FAMS = {
    "double":   ("tol", [0.2, 0.3, 0.5], {"": {}}),
    "triple":   ("tol", [0.3, 0.5, 0.75], {"": {}}),
    "ihs":      ("sh_tol", [0.5, 0.75, 1.0], {"": {}}),
    "box":      ("W", [20, 30, 45], {"": {}}),
    "channel":  ("tol", [0.2, 0.35, 0.5], {"": {}}),
    "triangle": ("tol", [0.2, 0.35, 0.5], {"": {}}),
    "flag":     ("pole_min", [2.0, 3.0, 4.0], {"flag/pole": dict(shape="flag", trig="pole"),
                                                "flag/edge": dict(shape="flag", trig="flag"),
                                                "pennant/pole": dict(shape="pennant", trig="pole"),
                                                "pennant/edge": dict(shape="pennant", trig="flag")}),
}
TFS = [1, 3, 5]
TMODES = ["mm", "2R", "eng"]

G = {}          # worker globals (set in the parent before the fork)


def ar_series(rows, period=20, base=200):
    """arb/regime.py:atr_ratio at every bar: ATR20 of the bars up to and
    including i over the median true range of the 200 bars up to i, capped
    0.5-2.0 (0 while history is short)."""
    h = np.array([r[2] for r in rows]); l = np.array([r[3] for r in rows]); c = np.array([r[4] for r in rows])
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    n = len(tr); out = np.zeros(n)
    cs = np.concatenate([[0.0], np.cumsum(tr)])
    from numpy.lib.stride_tricks import sliding_window_view
    for a in range(base, n, 20000):
        b = min(n, a + 20000)
        win = sliding_window_view(tr[a - base + 1:b], base)       # rows: tr[i-199..i] for i in a..b-1
        med = np.median(win, axis=1)
        for k, i in enumerate(range(a, b)):
            at = (cs[i + 1] - cs[i + 1 - period]) / period
            out[i] = max(0.5, min(2.0, at / med[k])) if med[k] > 0 and at > 0 else 0.0
    return out


def eng_cand(u, i, side, inv, play):
    """tools/backtest.py:work's sizing filters and exit loop for one entry at row
    i with the playbook invalidation `inv` (copied, not imported: work() runs
    the whole decision stack)."""
    ix = G["IX"][u]; rows = ix.rows
    if i < 400:
        return None
    seg = rows[i - 399:i + 1]; spot = seg[-1][4]; iso = rows[i][0]
    lot = bt.LOT[u]; cap = 10000 * L.max_risk_pct_per_trade / 100
    risk = abs(spot - inv)
    if (inv - spot) * side >= 0:
        return None
    a = arb_atr(seg[:-1], period=L.atr_period)
    if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
        return None
    delta = min(0.55, cap / (risk * lot))
    if delta < L.min_delta:
        return None
    cost_pts = bt.FEE / lot; prem_risk = risk * delta
    if cost_pts > L.max_cost_frac_of_risk * prem_risk:
        return None
    tgt = trailing.initial_target(seg, side, spot, risk, L, bt.STEP[u])
    reach = abs(tgt - spot) if tgt else L.reward_risk * risk
    if reach < L.min_target_r * risk or reach * delta - cost_pts < L.min_net_points:
        return None
    pos = Position(trade_id=0, symbol="", token="", strike=0, opt="CE" if side > 0 else "PE",
                   side=side, qty=lot, entry=100.0, stop=100 - prem_risk, target=1e9,
                   risk_points=prem_risk, index_invalidation=inv, opened_at=0, cost_points=cost_pts)
    pos.index_entry, pos.index_risk, pos.index_target = spot, risk, tgt
    pos.delta, pos.entry_bar = delta, iso
    ex, why, j = None, "", i
    for j in range(i + 1, min(i + 400, len(rows))):
        o, h, l, c = rows[j][1:5]
        if rows[j][0][:10] != iso[:10] or bt.mins(rows[j][0]) >= 15 * 60:
            ex, why = rows[j - 1][4], "squareoff"; break
        sd = pos.side
        if (sd > 0 and l <= pos.index_invalidation) or (sd < 0 and h >= pos.index_invalidation):
            lvl = pos.index_invalidation
            ex = min(o, lvl) if sd > 0 else max(o, lvl)
            why = "trail_stop" if pos.index_trailed else "stop"; break
        if pos.index_target and ((sd > 0 and h >= pos.index_target) or (sd < 0 and l <= pos.index_target)):
            win = rows[j - 399:j] + [rows[j]]
            if not trailing.try_extend(pos, win, pos.index_target, L, bt.STEP[u]):
                ex, why = pos.index_target, "target"; break
        r_now = (c - spot) * sd / risk
        if L.time_stop_min and j - i >= L.time_stop_min and r_now < L.time_stop_min_r:
            ex, why = c, "time_stop"; break
        trailing.update(pos, rows[j - 199:j + 1] + [rows[j]], L, index_px=c)
    if ex is None:
        return None
    move = (ex - spot) * side
    return dict(u=u, day=iso[:10], m=PT.hm(iso) + 1, i=i, j=j, side=side, risk=risk, delta=delta,
                net=move * delta * lot - bt.FEE, why=why, held=j - i, plays=[play], ar=0.0, R=0.0)


KEEP = ("u", "day", "m", "i", "j", "side", "held", "net", "plays", "why", "prem", "strike", "dte")


def job(args):
    """Detect one (family, config, size, tf, index); build the three exits;
    price them. Returns slim candidate dicts tagged with the variant."""
    fam, cfg, size, tf, u = args
    t0 = time.time()
    ix = G["IX"][u]; P = G["PP"][u]; AR = G["AR"][u]
    B = G["BARS"][(u, tf)]
    pname, _, cfgs = FAMS[fam]
    over = dict(cfgs[cfg]); over[pname] = size
    sigs = PT.detect(B, fam, **over)
    raw = {tm: [] for tm in TMODES}
    seen = set()
    for s in sigs:
        i = int(B.end[s["t"]])
        m0 = PT.hm(ix.rows[i][0])
        if not (T0 <= m0 <= T1):
            continue
        sub = s["mode"] if fam not in ("channel", "triangle") else (
            s["kind"] if s["mode"] == "break" else s["mode"])
        tag = dict(pattern=s["pattern"], sub=sub)
        key = (s["pattern"], sub, i)
        if key in seen:
            continue
        seen.add(key)
        play = f"PAT_{s['pattern']}_{sub}"
        t = SR.mk(u, ix, P, i, s["side"], s["stop"], None, play, tgt=s["target"]) if s["target"] is not None else None
        if t:
            raw["mm"].append(dict(t, **tag))
        t = SR.mk(u, ix, P, i, s["side"], s["stop"], 2.0, play)
        if t:
            raw["2R"].append(dict(t, **tag))
        t = eng_cand(u, i, s["side"], s["stop"], play)
        if t:
            raw["eng"].append(dict(t, **tag))
    out = []
    for tm, C in raw.items():
        for t in SR.reprice(C, G["IX"]):
            d = {k: t[k] for k in KEEP if k in t}
            d.update(pattern=t["pattern"], sub=t["sub"], tm=tm, ar=float(AR[t["i"]]),
                     fam=fam, cfg=cfg, size=size, tf=tf)
            out.append(d)
    return (fam, cfg, size, tf, u, len(sigs), out, time.time() - t0)


def load_world():
    IX = {n: rz.Index(n) for n in INDICES}
    PP = {n: SR.prep(IX[n]) for n in INDICES}
    AR = {n: ar_series(IX[n].rows) for n in INDICES}
    return IX, PP, AR


def build(procs=2):
    os.makedirs(OUT, exist_ok=True)
    IX, PP, AR = load_world()
    BARS = {(u, tf): PT.from_rows(IX[u].rows, tf) for u in INDICES for tf in TFS}
    G.update(IX=IX, PP=PP, AR=AR, BARS=BARS)
    jobs = [(fam, cfg, size, tf, u) for fam, (pn, sizes, cfgs) in FAMS.items() for cfg in cfgs
            for size in sizes for tf in TFS for u in INDICES]
    todo = [j for j in jobs if not os.path.exists(_jp(j))]
    print(f"{len(jobs)} jobs, {len(todo)} to run", flush=True)
    t0 = time.time()
    with Pool(procs) as pool:
        for k, (fam, cfg, size, tf, u, nsig, out, dt) in enumerate(pool.imap_unordered(job, todo, chunksize=1)):
            with open(_jp((fam, cfg, size, tf, u)), "wb") as f:
                pickle.dump(out, f)
            print(f"  [{k + 1}/{len(todo)} {time.time() - t0:5.0f}s] {fam} {cfg or '-'} {size:g} {tf}m {u}: "
                  f"{nsig} signals -> {len(out)} priced ({dt:.0f}s)", flush=True)


def _jp(j):
    fam, cfg, size, tf, u = j
    return os.path.join(OUT, f"c_{fam}_{cfg.replace('/', '-') or 'x'}_{size:g}_{tf}m_{u}.pkl")


# ----------------------------------------------------------------------------
# report
# ----------------------------------------------------------------------------
ROWS = []


def stat(tr, sess):
    s = summ(tr)
    return dict(n=s["n"], win=s["win"], i=s["i"], o=s["o"], y=s["y"], dd=s["dd"], ps=len(tr) / sess)


def short(s):
    return (f"n={s['n']:4d} w={s['win']:3.0f}% IN {s['i']:+7,.0f} OUT {s['o']:+7,.0f} "
            f"tot {s['y']:+8,.0f} DD {s['dd']:6,.0f} {s['ps']:.2f}/s")


def neighbours(fam, params):
    out = []
    for f, lab, p, a, b in ROWS:
        if f != fam:
            continue
        diff = [k for k in params if p[k] != params[k]]
        if len(diff) != 1:
            continue
        k = diff[0]
        vals = sorted({q[k] for ff, _, q, _, _ in ROWS if ff == fam})
        if abs(vals.index(params[k]) - vals.index(p[k])) == 1:
            out.append((a, b))
    return out


def main():
    t_start = time.time()
    K = pickle.load(open(BOOK_PKL, "rb"))
    assert abs(K["capital"] - CAPITAL) < 1e-6 and K["risk"] == L.max_risk_pct_per_trade, "stale book cache"
    IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
    sess = len({r[0][:10] for r in IX["NIFTY"].rows})
    BOOK = K["OB"] + K["LHM"]
    from fixes_test import run as fx_run
    base = SR.run_book(BOOK)
    assert [round(t["net2"], 6) for t in fx_run(BOOK, side_cooldown=30)] == [round(t["net2"], 6) for t in base]
    BASE = stat(base, sess)
    print(f"capital Rs {CAPITAL:,.2f}, risk cap {L.max_risk_pct_per_trade:g}% (costs inside), daily halt "
          f"{L.max_daily_loss_pct:g}%, F4 30 min, one position at a time, split {SPLIT}, {sess} sessions")
    print("BASELINE OB + LHM  " + short(BASE))
    print("""
Columns, per variant: ALONE = the pattern as the only strategy (same discipline);
BOOK = OB + LHM + the pattern; dIN/dOUT = book minus baseline.
  A+     alone positive in BOTH halves
  BOTH   book better than the baseline in BOTH halves
  PASS   BOTH, and every one-step neighbour (timeframe 1/3/5m, size parameter) BOTH too
  A+pl   A+ and every one-step neighbour A+ too
Exits: mm = measured move / pattern stop (stop widened to 1 ATR20 if tighter), 2R = 2R target,
eng = the engine's own playbook management (tight stop refused, arb/trailing.py target/trail/time stop).
Gates: on = live per-index pace gate + 4h trend with the trade; off = neither.
""", flush=True)

    files = sorted(f for f in os.listdir(OUT) if f.startswith("c_") and f.endswith(".pkl"))
    C = []
    for f in files:
        C += pickle.load(open(os.path.join(OUT, f), "rb"))
    print(f"{len(files)} candidate files, {len(C):,} priced candidates\n", flush=True)
    h4 = {}
    for t in C:
        k = (t["u"], t["i"], t["side"])
        if k not in h4:
            h4[k] = F.h4_ret(t, 5) * t["side"] > 0
    pace = lambda t: gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))

    groups = {}
    for t in C:
        groups.setdefault((t["pattern"], t["sub"], t["cfg"], t["tm"], t["tf"], t["size"]), []).append(t)
    fam_of = {}
    for t in C:
        fam_of[t["pattern"]] = t["fam"]
    order = [p for f in FAMS for p in PT.NAMES[f]]
    keys = sorted(groups, key=lambda k: (order.index(k[0]), k[1], k[2], TMODES.index(k[3]), k[4], k[5]))
    last = None
    for k in keys:
        pattern, sub, cfg, tm, tf, size = k
        for gates in ("on", "off"):
            ts = groups[k]
            if gates == "on":
                ts = [t for t in ts if h4[(t["u"], t["i"], t["side"])] and pace(t)]
            ts = [dict(t, strat="PAT") for t in ts]
            ts.sort(key=lambda t: (t["day"], t["m"], t["u"]))
            a = stat(SR.run_book(ts), sess)
            b = stat(SR.run_book(BOOK + ts), sess)
            fam = f"{pattern} {sub}{' ' + cfg if cfg else ''} exit={tm} gates={gates}"
            pn = FAMS[fam_of[pattern]][0]
            lab = f"{tf}m {pn}={size:g}"
            ROWS.append((fam, lab, dict(tf=tf, size=size), a, b))
    fam_order = list(dict.fromkeys(f for f, *_ in ROWS))
    for fam, lab, p, a, b in sorted(ROWS, key=lambda r: (fam_order.index(r[0]), r[2]["tf"], r[2]["size"])):
        if fam != last:
            print(f"-- {fam}"); last = fam
        nb = neighbours(fam, p)
        aplus = a["i"] > 0 and a["o"] > 0
        both = b["i"] > BASE["i"] and b["o"] > BASE["o"]
        tags = []
        if aplus:
            tags.append("A+pl" if nb and all(x["i"] > 0 and x["o"] > 0 for x, _ in nb) else "A+")
        if both:
            tags.append("PASS" if nb and all(y["i"] > BASE["i"] and y["o"] > BASE["o"] for _, y in nb) else "BOTH")
        print(f"  {lab:16s} ALONE {short(a)} | BOOK n={b['n']:4d} IN {b['i']:+7,.0f} OUT {b['o']:+7,.0f} "
              f"tot {b['y']:+8,.0f} DD {b['dd']:6,.0f} {b['ps']:.2f}/s dIN {b['i'] - BASE['i']:+7,.0f} "
              f"dOUT {b['o'] - BASE['o']:+7,.0f}  {' '.join(tags)}")

    # summaries
    print("\n" + "=" * 110)
    n_var = len(ROWS)
    aplus = [(f, l, a, b) for f, l, p, a, b in ROWS if a["i"] > 0 and a["o"] > 0]
    both = [(f, l, a, b) for f, l, p, a, b in ROWS if b["i"] > BASE["i"] and b["o"] > BASE["o"]]
    passed, aplpl = [], []
    for f, l, p, a, b in ROWS:
        nb = neighbours(f, p)
        if b["i"] > BASE["i"] and b["o"] > BASE["o"] and nb and all(y["i"] > BASE["i"] and y["o"] > BASE["o"] for _, y in nb):
            passed.append((f, l, a, b))
        if a["i"] > 0 and a["o"] > 0 and nb and all(x["i"] > 0 and x["o"] > 0 for x, _ in nb):
            aplpl.append((f, l, a, b))
    print(f"{n_var} variants. Alone positive in both halves (A+): {len(aplus)}; with a plateau (A+pl): {len(aplpl)}. "
          f"Book better in both halves (BOTH): {len(both)}; with a plateau (PASS): {len(passed)}.")
    for title, rows in (("A+pl (alone, positive both halves, neighbours too)", aplpl),
                        ("PASS (improves OB + LHM in both halves, neighbours too)", passed),
                        ("BOTH without plateau", [r for r in both if r not in passed])):
        print(f"\n{title}:")
        for f, l, a, b in sorted(rows, key=lambda r: -(r[3]["y"])):
            print(f"  {f:58s} {l:16s} alone {a['y']:+8,.0f} ({a['i']:+,.0f}/{a['o']:+,.0f}) n={a['n']} | "
                  f"book {b['y']:+8,.0f} dIN {b['i'] - BASE['i']:+,.0f} dOUT {b['o'] - BASE['o']:+,.0f} DD {b['dd']:,.0f}")
        if not rows:
            print("  none")
    # looser plateau, for transparency: neighbours over the size parameter only (same timeframe)
    print("\nLOOSER plateau (size neighbours only, same timeframe; NOT the acceptance rule):")
    loose = []
    for f, l, p, a, b in ROWS:
        nb = [(x, y) for ff, ll, q, x, y in ROWS if ff == f and q["tf"] == p["tf"] and q["size"] != p["size"]
              and abs(sorted({r[2]["size"] for r in ROWS if r[0] == f}).index(q["size"])
                      - sorted({r[2]["size"] for r in ROWS if r[0] == f}).index(p["size"])) == 1]
        if b["i"] > BASE["i"] and b["o"] > BASE["o"] and nb and all(y["i"] > BASE["i"] and y["o"] > BASE["o"] for _, y in nb):
            loose.append((f, l, a, b))
    for f, l, a, b in sorted(loose, key=lambda r: -(r[3]["y"])):
        print(f"  {f:58s} {l:16s} alone {a['y']:+8,.0f} ({a['i']:+,.0f}/{a['o']:+,.0f}) n={a['n']} | "
              f"book {b['y']:+8,.0f} dIN {b['i'] - BASE['i']:+,.0f} dOUT {b['o'] - BASE['o']:+,.0f} DD {b['dd']:,.0f}")
    if not loose:
        print("  none")
    print("\nPer pattern and gates: variants / A+ / BOTH, median trades a session alone, median alone total, "
          "best and worst alone total")
    for pt in order:
        for g in ("on", "off"):
            rr = [r for r in ROWS if r[0].split()[0] == pt and r[0].endswith("gates=" + g)]
            if not rr:
                continue
            ys = [a["y"] for _, _, _, a, _ in rr]
            print(f"  {pt:16s} gates {g:3s} variants {len(rr):3d}  A+ {sum(1 for r in rr if r[3]['i'] > 0 and r[3]['o'] > 0):3d}  "
                  f"BOTH {sum(1 for r in rr if r[4]['i'] > BASE['i'] and r[4]['o'] > BASE['o']):3d}  "
                  f"{float(np.median([a['ps'] for _, _, _, a, _ in rr])):.2f}/s  median {float(np.median(ys)):+8,.0f}  "
                  f"best {max(ys):+8,.0f}  worst {min(ys):+9,.0f}")
    # per pattern, the best book variant (for orientation, not selection)
    print("\nPer pattern (all its variants): how many A+ / BOTH / PASS, and the median alone total")
    pats = {}
    for f, l, p, a, b in ROWS:
        pats.setdefault(f.split()[0], []).append((f, l, p, a, b))
    for pt in order:
        rr = pats.get(pt, [])
        if not rr:
            continue
        med = float(np.median([a["y"] for _, _, _, a, _ in rr]))
        print(f"  {pt:16s} variants {len(rr):3d}  A+ {sum(1 for r in rr if r[3]['i'] > 0 and r[3]['o'] > 0):3d}  "
              f"BOTH {sum(1 for r in rr if r[4]['i'] > BASE['i'] and r[4]['o'] > BASE['o']):3d}  "
              f"PASS {sum(1 for r in passed if r[0].split()[0] == pt):3d}  median alone {med:+9,.0f}")
    print(f"\n(run time {time.time() - t_start:.0f}s)")


if __name__ == "__main__":
    if "--build" in sys.argv:
        build(procs=2)
    elif "--probe" in sys.argv:          # quick timing on one job
        IX, PP, AR = load_world()
        G.update(IX=IX, PP=PP, AR=AR, BARS={(u, tf): PT.from_rows(IX[u].rows, tf) for u in ["NIFTY"] for tf in TFS})
        for a in [("double", "", 0.3, 1, "NIFTY"), ("box", "", 30, 1, "NIFTY"), ("flag", "flag/pole", 3.0, 1, "NIFTY"),
                  ("channel", "", 0.35, 1, "NIFTY"), ("triangle", "", 0.35, 3, "NIFTY")]:
            r = job(a)
            from collections import Counter
            print(a, "signals", r[5], "priced", len(r[6]), f"{r[7]:.1f}s",
                  Counter((t["pattern"], t["sub"], t["tm"]) for t in r[6]), flush=True)
    else:
        main()
