#!/usr/bin/env python3
"""Does the backtest's option model hold on the LIVE market? (owner, 19 Oct 2026)

    venv/bin/python research/live_calibration.py            # every data/book_*.f4

Every profit number in research/ comes from research/lab/realistic.py: a
Black-Scholes premium with time at THETA_F speed and a fixed spread per index.
The live engine records the real option chain (arb/recorder.py, once a minute
when live) - bid, ask, LTP for ATM +/- 10 strikes on several expiries. This
checks the model against those recordings on the two things that make or lose
the money:

  spread   real ask - bid at ATM +/- 3 on the traded expiry vs realistic.SPREAD (what every
           backtest trade pays on entry and exit)
  moves    real change of the option mid over 5 / 15 / 30 minutes vs the
           model's change for the SAME index move: IV implied from the real
           mid at the start, then bs() at the end with time at THETA_F speed.
           "real/model slope" = sum(real x model) / sum(model^2): above 1 the
           real option moved more than the backtest assumed, below 1 less.

The index is the put-call-parity forward from the same chain (no separate
feed needed). Quotes older than 5 s, or a missing side, are skipped.
"""
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research", "lab"), ROOT]
from bsmodel import T_years, bs, implied_vol             # noqa: E402
from realistic import SPREAD, THETA_F                     # noqa: E402

NAMES = ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "NIFTY")
HORIZONS = (5, 15, 30)
STRIKES = 3                             # ATM +/- 3: where the engine's picks land
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def underlying(sym):
    return next(n for n in NAMES if sym.startswith(n))


def expiry_date(s):                     # "19OCT2026"
    return date(int(s[5:]), MON[s[2:5]], int(s[:2]))


def load(meta_path):
    m = json.load(open(meta_path))
    n, c = m["n_slots"], len(m["columns"])
    raw = np.fromfile(meta_path.replace(".meta.json", ".f4"), dtype=np.float32)
    k = raw.size // (n * c)
    a = raw[:k * n * c].reshape(k, n, c)
    t0 = datetime.fromisoformat(m["started_ist"]).replace(tzinfo=None)
    t1 = datetime.fromtimestamp(os.path.getmtime(meta_path.replace(".meta.json", ".f4")))
    step = max(1.0, (t1 - t0).total_seconds() / max(1, k - 1))
    # Down-sample anything faster than a snapshot a minute to minutes.
    every = max(1, int(round(60.0 / step)))
    a = a[::every]
    times = [t0 + timedelta(seconds=step * every * i) for i in range(len(a))]
    return m, a, times


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "data", "book_*.meta.json")))
    spreads, moves = {}, {}
    days = {}
    for f in files:
        try:
            m, a, times = load(f)
        except Exception as e:                          # a torn recording
            print(f"skip {os.path.basename(f)}: {e!r}")
            continue
        if len(a) < 10:
            continue
        bid, ask, age = a[:, :, 1].astype(float), a[:, :, 2].astype(float), a[:, :, 7]
        ok = (bid > 0) & (ask > bid) & (age < 5000)
        mid = np.where(ok, (bid + ask) / 2, np.nan)
        # Only the expiry the engine trades: the nearest one, or the next on
        # its expiry day (expiry_trade_next) - far expiries are wider and
        # never bought.
        traded = {}
        for e in m["expiries"]:
            name, exp = underlying(e["ce_symbol"][0]), expiry_date(e["expiry"])
            if exp > times[0].date() and (name not in traded or exp < traded[name]):
                traded[name] = exp
        for e in m["expiries"]:
            name = underlying(e["ce_symbol"][0])
            exp = expiry_date(e["expiry"])
            if exp != traded.get(name):
                continue
            K = np.array(e["strikes"])
            ce, pe = np.array(e["ce_slots"]), np.array(e["pe_slots"])
            days.setdefault(name, set()).add(times[0].date())
            # forward per minute from the strike where call and put are closest
            diff = np.abs(mid[:, ce] - mid[:, pe])
            F = np.full(len(a), np.nan)
            for t in range(len(a)):
                if np.all(np.isnan(diff[t])):
                    continue
                j = int(np.nanargmin(diff[t]))
                F[t] = K[j] + mid[t, ce[j]] - mid[t, pe[j]]
            step = float(np.median(np.diff(K))) if len(K) > 1 else 1.0
            for t in range(len(a)):
                if np.isnan(F[t]) or not (K[0] <= F[t] <= K[-1]):
                    continue
                atm = int(np.argmin(np.abs(K - F[t])))
                for off in range(-STRIKES, STRIKES + 1):
                    i = atm + off
                    if not 0 <= i < len(K):
                        continue
                    for opt, slots in (("CE", ce), ("PE", pe)):
                        s = slots[i]
                        if not ok[t, s]:
                            continue
                        if True:
                            spreads.setdefault(name, []).append(ask[t, s] - bid[t, s])
                        T0 = T_years(times[t], exp, [])
                        iv = implied_vol(mid[t, s], F[t], K[i], T0, opt) if T0 > 0 else None
                        if not iv:
                            continue
                        for h in HORIZONS:
                            u = t + h
                            if u >= len(a) or np.isnan(F[u]) or not ok[u, s]:
                                continue
                            Tu = T0 - THETA_F * (T0 - T_years(times[u], exp, []))
                            model = bs(F[u], K[i], max(Tu, 0.0), iv, opt) - mid[t, s]
                            real = mid[u, s] - mid[t, s]
                            moves.setdefault((name, h), []).append((real, model, F[u] - F[t]))
        print(f"read {os.path.basename(f)}: {len(a)} minutes", flush=True)

    print("\n| Index | Days | Real spread (median) | Real spread (p90) | Model spread | Verdict |")
    print("|---|---|---|---|---|---|")
    for name in sorted(spreads):
        s = np.array(spreads[name])
        md = SPREAD.get(name, float("nan"))
        verdict = "model OK" if np.median(s) <= md * 1.25 else "model too optimistic"
        print(f"| {name} | {len(days[name])} | {np.median(s):.2f} | {np.percentile(s, 90):.2f} | "
              f"{md:.2f} | {verdict} |")

    print("\n| Index | Horizon | Samples | Mean real dP | Mean model dP | Mean error | "
          "Corr | Real/model slope |")
    print("|---|---|---|---|---|---|---|---|")
    for (name, h) in sorted(moves):
        x = np.array(moves[(name, h)])
        real, model, dF = x[:, 0], x[:, 1], x[:, 2]
        corr = float(np.corrcoef(real, model)[0, 1]) if len(x) > 2 else float("nan")
        # slope of real on model through 0: 1.0 = the option moved exactly as
        # the backtest prices it; 0.9 = it paid 10% less than the backtest says
        den = float(np.sum(model * model))
        ratio = float(np.sum(real * model)) / den if den else float("nan")
        print(f"| {name} | {h} min | {len(x):,} | {real.mean():+.2f} | {model.mean():+.2f} | "
              f"{(real - model).mean():+.2f} | {corr:.3f} | {ratio:.3f} |")


if __name__ == "__main__":
    main()
