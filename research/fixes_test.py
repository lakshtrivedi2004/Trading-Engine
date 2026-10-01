#!/usr/bin/env python3
"""The owner-approved fixes from the 1 Oct 2026 live-loss review, tested on a
year of real 1-minute index data before any of them is switched on.

    venv/bin/python research/fixes_test.py

The playbook is the live one (config/intraday.json, ob_fvg_retest). Each
candidate is decided on COMPLETED bars (that is F1, and it is what every
backtest here has always assumed), priced as the option the engine would buy
(research/lab/realistic.py), and put through today's live gates: per-index
half-day pace, the 4-hour trend, the next expiry on expiry day, one position at
a time across NIFTY, BANKNIFTY and SENSEX.

  F2  no entry when the index has already run >= N ATR in the trade's
      direction over the last 30 completed bars (arb/regime.py:run_in_direction)
  F3  no put right after a reclaimed sell-side sweep, no call after a buy-side
      one (arb/regime.py:swept_against)
  F4  after a losing trade, no trade on the same side on ANY index for N minutes
  F5  round-trip cost + exit slippage (1 spread + 1% of premium) inside the
      6% cap: the contract is chosen so a stop-out loses at most the cap

A fix counts only if it is better than the baseline in BOTH halves (split 25
May 2026). Option decay is modelled; slippage beyond the modelled spread is not.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"),
                ROOT, os.path.join(ROOT, "tools")]
import backtest as bt                         # noqa: E402
import realistic as rz                        # noqa: E402
from common import Features, summ             # noqa: E402
from playbook_lab import INDICES, candidates, live_gates  # noqa: E402
from arb.config import IntradayLimits         # noqa: E402
from arb.regime import run_in_direction, swept_against  # noqa: E402


def priced(C, IX, capital, costs_inside):
    out = []
    for t in C:
        r = rz.reprice(t, IX[t["u"]], capital, next_expiry_on_expiry_day=True,
                       costs_inside=costs_inside)
        if r is not None:
            u = dict(t)
            u.update(r)
            out.append(u)
    return out


def features(ts, IX):
    for t in ts:
        rows = IX[t["u"]].rows
        seg = rows[max(0, t["i"] - 399):t["i"] + 1]
        t["run"] = run_in_direction(seg, t["side"])
        t["swept"] = bool(swept_against(seg, t["side"]))
    return ts


def run(ts, side_cooldown=0):
    return bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=side_cooldown)


def line(label, tr, base=None):
    s = summ(tr)
    loss = [t["net2"] for t in tr if t["net2"] < 0]
    worst = min(loss) if loss else 0.0
    flag = ""
    if base is not None:
        b = summ(base)
        flag = "  BETTER BOTH" if s["i"] > b["i"] and s["o"] > b["o"] else ""
    return (f"{label:34s} n={s['n']:3d} win={s['win']:3.0f}% year Rs{s['y']:+8,.0f} "
            f"(IN {s['i']:+7,.0f} / OUT {s['o']:+7,.0f}) avg {s['avg']:+5.0f} "
            f"worst {worst:6,.0f} maxDD {s['dd']:6,.0f}{flag}")


def main():
    L = IntradayLimits.load()
    capital = float(L.capital_ceiling or L.starting_capital)
    rz.L = L
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    sets = {"use_regime_gate": False, "playbooks": list(L.playbooks)}
    C = candidates(sets, 4)
    P = features(live_gates(priced(C, IX, capital, False), F, L), IX)
    P5 = features(live_gates(priced(C, IX, capital, True), F, L), IX)
    print(f"capital Rs {capital:,.0f}, risk {L.max_risk_pct_per_trade:g}%, playbooks {L.playbooks}, "
          f"{len(C)} raw candidates\n")

    base = run(P)
    print(line("baseline (F1 = completed bars)", base))
    for n in (3, 4, 5, 6, 8):
        print(line(f"F2 run < {n} ATR", run([t for t in P if t["run"] < n]), base))
    print(line("F3 no trade after opposite sweep", run([t for t in P if not t["swept"]]), base))
    for m in (15, 30, 60, 120):
        print(line(f"F4 same-side cooldown {m} min", run(P, side_cooldown=m), base))
    print(line("F5 costs inside the cap", run(P5), base))
    print()
    for n in (4, 6):
        for m in (30, 60):
            keep = lambda t, n=n: t["run"] < n and not t["swept"]
            print(line(f"F2<{n}+F3+F4 {m}m", run([t for t in P if keep(t)], m), base))
            print(line(f"F2<{n}+F3+F4 {m}m+F5", run([t for t in P5 if keep(t)], m), base))


if __name__ == "__main__":
    main()
