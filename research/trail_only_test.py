#!/usr/bin/env python3
"""The owner's exit rule (16 Oct 2026), tested on a year before it goes live.

    venv/bin/python research/trail_only_test.py

Prompted by SENSEX26O2274700CE on 16 Oct: in at 268.85, out at "target" 329.75
(+2.33R) at 09:57, after which the premium ran past 540. The owner's rule:

    No fixed target. At 1:1 the stop goes to breakeven; after that it trails
    to each new swing low (lower high on a short) - the low of the last
    pullback - and the trade ends only when that trailed stop is hit.

That is target_mode = "trail" with trail_mode = "structure" (arb/trailing.py).
Variants change only how a "swing" is read: on 1-minute bars (noisy), on a
3-minute fold, with a stronger pivot, and without the reversal tightening.
"structure" targets the next support/resistance level (swing, PDH/PDL, opening
range, round number) instead - the zones drawn on the chart.

Same pipeline as research/trail_test.py (ob_fvg_retest, completed bars,
option-priced, costs inside the cap, side cooldown 30, live gates, split
25 May 2026). A variant counts only if it is BETTER BOTH halves.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import realistic as rz                                   # noqa: E402
from common import Features                              # noqa: E402
from playbook_lab import INDICES, candidates, live_gates  # noqa: E402
from fixes_test import priced, run, line                 # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

TRAIL = {"target_mode": "trail", "time_stop_min": 0.0}
VARIANTS = [
    ("today (momentum target + extend)", {}),
    ("fixed 1:2, exit at target", {"target_mode": "fixed", "on_target": "exit"}),
    ("OWNER: no target, swing trail 1m", TRAIL),
    ("no target, swing trail 3m", dict(TRAIL, trail_timeframe=3)),
    ("no target, swing trail 5m", dict(TRAIL, trail_timeframe=5)),
    ("no target, trail 1m strength 3", dict(TRAIL, trail_swing_strength=3)),
    ("no target, trail 3m, no tighten", dict(TRAIL, trail_timeframe=3, tighten_on_reversal=False)),
    ("no target, hybrid (swing|chandelier)", dict(TRAIL, trail_mode="hybrid", trail_timeframe=3)),
    ("extend on any up bar, 10 ext", {"max_target_extensions": 10, "extend_body_atr": 0.0,
                                      "extend_close_pos": 0.5}),
    ("S/R target (structure)", {"target_mode": "structure"}),
]


def main():
    L = IntradayLimits.load()
    rz.L = L
    capital = float(L.capital_ceiling or L.starting_capital)
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    base = None
    for label, over in VARIANTS:
        sets = dict(over, use_regime_gate=False, playbooks=list(L.playbooks))
        C = candidates(sets, 4)
        tr = run(live_gates(priced(C, IX, capital, True), F, L), side_cooldown=30)
        why = {}
        for t in tr:
            why[t["why"]] = why.get(t["why"], 0) + 1
        big = sum(1 for t in tr if t["net2"] > 0 and t.get("held", 0) >= 30)
        print(line(label, tr, base), f"| runners(30m+ win) {big}",
              "| exits", dict(sorted(why.items(), key=lambda kv: -kv[1])), flush=True)
        base = base or tr


if __name__ == "__main__":
    main()
