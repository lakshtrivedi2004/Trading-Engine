#!/usr/bin/env python3
"""Last-half-hour (LHM) losses: is there a filter that skips them? (owner, 21 Oct 2026)

    nice -n 19 venv/bin/python research/lhm_filter_test.py

21 Oct 2026: NIFTY -205.9 from the previous close at 15:00 (-0.67 ADR), so LHM
bought a PE; the index had stopped falling at 14:50 (higher lows 22,657 ->
22,673) and a short-covering rally hit the 1.5-ATR stop at 15:03 (-Rs 584
live, -Rs 525 paper). At 15:00 the last 10 minutes were +11 points AGAINST
the trade, the last 15 minutes +1.

Filters on the same year of LHM trades (NIFTY, entry 15:00, exit 15:13, 4-hour
trend with the trade, priced as the engine buys them at Rs 11,000 / 8%):
  momN      the last N minutes (close i vs close i-N) moved WITH the day
  extreme   the 15:00 close within k ATR of the day's low (PE) / high (CE)
  stop      lhm_stop_atr 1.5 (live) vs 1.0 / 2.0 / 2.5
A filter counts only if better in BOTH halves (split 25 May 2026).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S                                       # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features                              # noqa: E402
from fixes_test import run, line                         # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

L = IntradayLimits.load()
rz.L = L
CAPITAL = 11000.0
IX = {"NIFTY": rz.Index("NIFTY")}
F = Features(IX)
P = S.prep(IX["NIFTY"])


def gen(minr):
    rows, c, atr, out = IX["NIFTY"].rows, P["c"], P["atr"], []
    for d in P["keys"]:
        dd = P["D"][d]
        if not dd["sig"]:
            continue
        for i in dd["idx"]:
            if S.hm(rows[i][0]) + 1 != S.T("15:00"):
                continue
            mv = c[i] - dd["pc"]
            if abs(mv) < L.lhm_adr_frac * dd["adr"]:
                continue
            side = 1 if mv > 0 else -1
            a = atr[i]
            stop = c[i] - side * minr * a
            j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), a, last=S.T("15:13"))
            t = S.cand("NIFTY", IX["NIFTY"], i, j, px, side, abs(c[i] - stop), why, "LHM")
            day = [r for r in rows[:i + 1] if r[0][:10] == rows[i][0][:10]]
            t.update(mom={n: (c[i] - c[i - n]) * side for n in (5, 10, 15, 30)},
                     ext=((c[i] - min(r[3] for r in day)) if side < 0
                          else (max(r[2] for r in day) - c[i])) / a if a > 0 else 99.0)
            out.append(t)
    keep = []
    for t in out:
        r = rz.reprice(t, IX["NIFTY"], CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                       slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
        if r and F.h4_ret(t, 5) * t["side"] > 0:
            u = dict(t)
            u.update(r)
            keep.append(u)
    return keep


def main():
    base = None
    for minr in (1.5, 1.0, 2.0, 2.5):
        T = gen(minr)
        tag = f"stop {minr:g} ATR" + (" (live)" if minr == 1.5 else "")
        tests = [("all", lambda t: True)]
        if minr == 1.5:
            tests += [(f"mom{n} with the day", (lambda n: lambda t: t["mom"][n] > 0)(n))
                      for n in (5, 10, 15, 30)]
            tests += [(f"within {k:g} ATR of day extreme", (lambda k: lambda t: t["ext"] <= k)(k))
                      for k in (0.5, 1.0, 2.0)]
            tests += [("mom10 with day OR within 1 ATR", lambda t: t["mom"][10] > 0 or t["ext"] <= 1.0)]
        for label, f in tests:
            tr = run([t for t in T if f(t)], side_cooldown=30)
            print(line(f"{tag} | {label}", tr, base), flush=True)
            if base is None:
                base = tr


if __name__ == "__main__":
    main()
