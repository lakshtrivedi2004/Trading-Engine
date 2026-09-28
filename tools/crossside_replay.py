#!/usr/bin/env python3
"""Replay the premium-per-point and cross-side rules on recorded books (research/PAPERS.md 0h).

    python tools/crossside_replay.py data/book_20260917_123059.f4 data/book_20260928_104420.f4

Uses the engine's own arb/optionflow.py on 1-minute mids built from TickRecorder
files (a 4 Hz file is thinned to one snapshot a minute; the engine's own
1-per-minute recording is used as is). Nearest non-expiring contract per index,
strikes whose spread is above the index's cap are blanked.

Reports:
  A  realised premium points per index point against chain delta, calls vs puts
  B  the effective delta's error against the chain delta's, when predicting the
     premium change over the next 30 minutes from the index change that happened
  C  one trade at a time per index, entering on the 10-minute index move (the
     same stand-in tools/premium_replay.py uses), buying ~0.5 delta, holding 30
     minutes, ask in, bid out, Rs 75 a lot - split by the cross-side verdict
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from arb.config import IntradayLimits
from arb.instruments import parse_expiry
from arb.intraday import chain_delta
from arb.optionflow import effective_delta, read, realised_beta
from arb.smc import BEAR, BULL

SPOT = {65: 0, 30: 130, 20: 218}            # lot -> spot slot, as run_bot registers them
NAME = {65: "NIFTY", 30: "BANKNIFTY", 20: "SENSEX"}
MAXSPR = {65: 3.0, 30: 6.0, 20: 8.0}
FEE, W_ENTRY, HOLD, HIST = 75.0, 10, 30, 31
L = IntradayLimits.load()


def series(path):
    meta = json.load(open(path.replace(".f4", ".meta.json"))); n = meta["n_slots"]
    raw = np.memmap(path, dtype=np.float32, mode="r")
    a = raw[: raw.size // (n * 8) * n * 8].reshape(-1, n, 8)
    if len(a) > 3000:                        # 4 Hz research recording
        a = a[239::240]
    bid, ask, ltp = (a[:, :, c].astype(float) for c in (1, 2, 5))
    day = os.path.basename(path).split("_")[1]
    near = {}
    for e in meta["expiries"]:
        d = parse_expiry(e["expiry"])
        if d.strftime("%Y%m%d") > day and (e["lot_size"] not in near or d < near[e["lot_size"]][0]):
            near[e["lot_size"]] = (d, e)
    for lot, (_, e) in near.items():
        S = ltp[:, SPOT[lot]]
        ok = lambda sl: (bid[:, sl] > 0) & (ask[:, sl] > 0) & (ask[:, sl] - bid[:, sl] <= MAXSPR[lot])
        mid = lambda sl: np.where(ok(sl), (bid[:, sl] + ask[:, sl]) / 2, np.nan)
        yield (f"{day} {NAME[lot]} {e['expiry']}", lot, np.where(S > 0, S, np.nan),
               [float(k) for k in e["strikes"]], mid(e["ce_slots"]), mid(e["pe_slots"]),
               ask[:, e["ce_slots"]], bid[:, e["ce_slots"]], ask[:, e["pe_slots"]], bid[:, e["pe_slots"]])


def atr1(S, t, n=20):
    d = np.abs(np.diff(S[max(0, t - n):t + 1]))
    d = d[np.isfinite(d)]
    return float(d.mean()) if len(d) else 0.0


A = {"CE": [], "PE": []}; B = {"CE": [], "PE": []}; C = []
for path in sys.argv[1:]:
    for tag, lot, S, K, CM, PM, CA, CB, PA, PB in series(path):
        # A: whole-window beta per strike, strikes with chain delta 0.3-0.7
        for opt, M in (("CE", CM), ("PE", PM)):
            for j in range(1, len(K) - 1):
                cd = np.nanmean([chain_delta(M[t], K, j, opt) for t in range(0, len(S), 5)
                                 if np.isfinite(M[t, j - 1:j + 2]).all()] or [np.nan])
                if not 0.3 <= cd <= 0.7:
                    continue
                b, r2, n = realised_beta(M[:, j], S)
                if n >= 20:
                    A[opt].append(abs(b) / cd)
        # B and C, minute by minute
        t = HIST
        busy_until = -1
        while t < len(S) - HOLD:
            if not (np.isfinite(S[t]) and np.isfinite(S[t - W_ENTRY]) and np.isfinite(S[t + HOLD])):
                t += 1; continue
            for opt, M in (("CE", CM), ("PE", PM)):
                j = int(np.argmin(np.abs(np.array(K) - S[t])))
                if not (0 < j < len(K) - 1) or not np.isfinite(M[t, j - 1:j + 2]).all():
                    continue
                if not np.isfinite(M[t + HOLD, j]):
                    continue
                cd = chain_delta(M[t], K, j, opt)
                eff, _ = effective_delta(cd, opt, M[t - HIST + 1:t + 1, j], S[t - HIST + 1:t + 1], L)
                sgn = 1 if opt == "CE" else -1
                move = S[t + HOLD] - S[t]
                if sgn * move > 0:           # a favourable move: the reward side
                    B[opt].append((M[t + HOLD, j] - M[t, j], cd * abs(move), eff * abs(move)))
            mv = S[t] - S[t - W_ENTRY]
            if t <= busy_until or mv == 0:
                t += 1; continue
            side = BULL if mv > 0 else BEAR
            Mb, Mo, BA, BBid = (CM, PM, CA, CB) if side == BULL else (PM, CM, PA, PB)
            ob, oo = ("CE", "PE") if side == BULL else ("PE", "CE")
            ds = [chain_delta(Mb[t], K, j, ob) if np.isfinite(Mb[t, j - 1:j + 2]).all() else np.nan
                  for j in range(1, len(K) - 1)]
            if not np.isfinite(ds).any():
                t += 1; continue
            i = 1 + int(np.nanargmin(np.abs(np.array(ds) - 0.5)))
            do = [chain_delta(Mo[t], K, j, oo) if np.isfinite(Mo[t, j - 1:j + 2]).all() else np.nan
                  for j in range(1, len(K) - 1)]
            if not np.isfinite(do).any():
                t += 1; continue
            jo = 1 + int(np.nanargmin(np.abs(np.array(do) - ds[i - 1])))
            deep, far = (i - 1, i + 1) if ob == "CE" else (i + 1, i - 1)
            win = slice(t - HIST + 1, t + 1)
            x = read(side, Mb[win, i], Mo[win, jo], S[win],
                     (Mb[win, deep], Mb[win, far]), (ds[i - 1], do[jo - 1]), L, atr=atr1(S, t))
            if x.verdict == "no_data" or not (BA[t, i] > 0 and BBid[t + HOLD, i] > 0):
                t += 1; continue
            C.append((tag, x.verdict, x.confirms, x.against, bool(x.detail.get("vol_crush")),
                      (BBid[t + HOLD, i] - BA[t, i]) * lot - FEE))
            busy_until = t + HOLD
            t += 1

print("A. realised premium points per index point / chain delta (strikes 0.3-0.7 delta)")
for opt in ("CE", "PE"):
    r = np.array(A[opt])
    if len(r):
        print(f"   {opt}: {len(r):3d} strikes  median {np.median(r):.2f}  IQR {np.percentile(r, 25):.2f}-{np.percentile(r, 75):.2f}")
print(f"\nB. premium change over {HOLD} min on a favourable index move: actual vs predicted")
for opt in ("CE", "PE"):
    r = np.array(B[opt])
    if len(r):
        act, ch, ef = r[:, 0], r[:, 1], r[:, 2]
        print(f"   {opt}: n={len(r):4d}  actual/chain {act.sum() / ch.sum():.2f}  actual/effective {act.sum() / ef.sum():.2f}"
              f"  MAE chain {np.abs(act - ch).mean():.2f}  MAE effective {np.abs(act - ef).mean():.2f}"
              f"  over-promised: chain {100 * (ch > act).mean():.0f}%  effective {100 * (ef > act).mean():.0f}%")
print(f"\nC. {W_ENTRY}-min momentum entries, one at a time per index, {HOLD}-min hold, net of spread and Rs {FEE:.0f}/lot")
P = np.array([c[5] for c in C])
def show(label, s):
    s = np.asarray(s, bool)
    if not s.any():
        print(f"   {label:34s} n=  0"); return
    print(f"   {label:34s} n={s.sum():3d}  win {100 * (P[s] > 0).mean():3.0f}%  mean Rs {P[s].mean():+6.0f}  total Rs {P[s].sum():+8.0f}")
show("all", [True] * len(C))
show("cross-side confirms", [c[2] for c in C])
show("cross-side against", [c[3] for c in C])
show("neither", [not c[2] and not c[3] for c in C])
show("vol crush", [c[4] for c in C])
for v in sorted({c[1] for c in C}):
    show(f"  verdict {v}", [c[1] == v for c in C])
print("\nSamples this small are hints, not evidence (research/PAPERS.md section 4).")
