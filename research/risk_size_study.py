#!/usr/bin/env python3
"""Risk per trade 1-15%, and "always the option nearest our capital" (owner, 19 Oct 2026).

    nice -n 19 venv/bin/python research/risk_size_study.py [capital]

"If the market wants our stop it hits it whatever its size, so buy the option
whose price is closest to our capital, or 2+ lots. Forget the loss limit:
which risk limit makes the most profit? Check every value 1-15."

Today's live entries and exits (ob_fvg_retest, completed bars, live gates,
split 25 May 2026) are found once; then for every risk cap the trade is
re-priced as the engine would buy it at that cap (research/lab/realistic.py:
the dearest strike whose stop-out loss, costs and slip included, fits the cap)
and sized with:
  live lots   arb/intraday_engine.py:_lots_for - grade A 2 lots, else 1, cut
              to what cash and the cap allow
  max lots    as many lots as cash and the cap allow
"no cap" is the owner's rule as stated: the dearest strike whose lot fits the
capital, whatever the stop costs (premium stop still capped at
max_risk_frac_of_premium of the premium).

Rows are on fixed capital (no compounding) so rupees compare directly, and
with the daily loss halt OFF (owner: "forget the loss limit"); the "with 15%
halt" column is the same trades with the live halt on. Kelly (1956) on the
8% trades' R-multiples gives the growth-optimal risk fraction; MacLean, Thorp
& Ziemba (2010) for why practice uses a fraction of it.

Approximation: candidates are generated once with a permissive cap, so the
backtest worker's own coarse delta filter does not drop setups that a low cap
would have; the re-pricing applies the real cap per level.
"""
import copy
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features, SPLIT                       # noqa: E402
from playbook_lab import INDICES, candidates, live_gates  # noqa: E402
from fixes_test import priced                            # noqa: E402
from exit_sweep import stats                             # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

CAPITAL = float(sys.argv[1]) if len(sys.argv) > 1 else 11000.0
LEVELS = [float(x) for x in range(1, 16)] + [None]        # None = no cap


def kelly(R):
    best = (0.0, -1e9)
    for k in range(1, 100):
        f = k / 200.0
        if any(1 + f * r <= 0 for r in R):
            break
        g = sum(math.log(1 + f * r) for r in R) / len(R)
        if g > best[1]:
            best = (f, g)
    return best[0]


def sized(P, cap_inr, mode, L):
    out = []
    for t in P:
        by_cash = int(CAPITAL // max(t["lot_cost"], 1e-9))
        by_risk = int(cap_inr // max(t["risk_inr"], 1e-9)) if cap_inr else by_cash
        want = (int(getattr(L, f"lots_grade_{(t.get('grade') or 'c').lower()}", 1) or 1)
                if mode == "live" else 10**6)
        lots = max(0, min(want, by_cash, by_risk))
        if lots:
            u = dict(t, net=t["net"] * lots, lots=lots)
            out.append(u)
    return out


def simulate(ts, halt):
    return bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=30,
                  halt_pct=halt)


def main():
    L = IntradayLimits.load()
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    days = sorted({r[0][:10] for ix in IX.values() for r in ix.rows})
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks),
                        max_risk_pct_per_trade=100.0), 6)
    halt15 = L.max_daily_loss_pct * CAPITAL / 10000.0
    print(f"capital Rs {CAPITAL:,.0f} fixed, {len(C)} raw setups, {len(days)} trading days "
          f"{days[0]} - {days[-1]}\n", flush=True)
    head = ("| Risk cap | Rs at risk | Lots | Trades | Win % | Avg win | Avg loss | RR | PF | "
            "Year Rs | Before 25 May | After 25 May | Per day | Per week | Per month | "
            "Max DD Rs | Max DD % | Worst day | Avg premium | Avg lots | With 15% halt |")
    rows = {"live": [], "max": []}
    r8 = None
    for pct in LEVELS:
        Lv = copy.copy(L)
        Lv.max_risk_pct_per_trade = pct if pct else 1000.0
        rz.L = Lv
        P = live_gates(priced(C, IX, CAPITAL, True), F, Lv)
        cap_inr = CAPITAL * pct / 100.0 if pct else 0.0
        for mode in ("live", "max"):
            S = sized(P, cap_inr, mode, Lv)
            tr = simulate(S, 1e9)
            h = simulate(S, halt15)
            s = stats(tr, days)
            if pct == 8.0 and mode == "live":
                r8 = [t["net2"] / (t["risk_inr"] * t["lots"]) for t in tr]
            prem = sum(t["lot_cost"] for t in tr) / max(1, len(tr))
            lots = sum(t["lots"] for t in tr) / max(1, len(tr))
            hy = sum(t["net2"] for t in h)
            label = f"{pct:g}%" if pct else "no cap (nearest capital)"
            rows[mode].append(
                f"| {label} | {cap_inr:,.0f} | {mode} | {s['n']} | {s['win']} | {s['avg_win']:,} | "
                f"{s['avg_loss']:,} | {s['rr']} | {s['pf']} | {s['year']:+,} | {s['ins']:+,} | "
                f"{s['out']:+,} | {s['per_day']:+,} | {s['per_week']:+,} | {s['per_month']:+,} | "
                f"{s['dd']:,} | {100 * s['dd'] / CAPITAL:.0f}% | {s['worst_day']:+,} | "
                f"{prem:,.0f} | {lots:.2f} | {hy:+,.0f} |")
            print(rows[mode][-1], flush=True)
    for mode in ("live", "max"):
        print(f"\n## lots = {mode}\n\n{head}\n|" + "---|" * (head.count("|") - 1))
        print("\n".join(rows[mode]))
    if r8:
        f = kelly(r8)
        print(f"\nKelly on the 8% trades' R-multiples: {100 * f:.1f}% of capital per trade "
              f"(half Kelly {50 * f:.1f}%); {len(r8)} trades, mean {sum(r8) / len(r8):+.3f}R")


if __name__ == "__main__":
    main()
