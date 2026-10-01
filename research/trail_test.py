#!/usr/bin/env python3
"""The owner's trailing rule (1 Oct 2026), tested on a year before it goes live.

    venv/bin/python research/trail_test.py

"As soon as the trade goes our way and the market makes a higher low, trail the
stop to it; then to the second higher low, the third, for as long as the market
keeps going our way. The target trails with momentum, at least 1:2, further if
volatility allows. If the market turns against us with no sign of reaching the
target, wait for the stop as the analysis says."

Against today's rules, each piece alone and together:
  trail_from_first_swing   trail from the first higher low, not only after +1R
  target_floor_r = 2       the target is never closer than 2R
  min_target_r = 2         ...or: skip trades whose achievable target is < 2R
  max_target_extensions    2 -> 10 (keep extending while momentum lasts)
  time_stop_min = 0        no 40-minute time stop: the stop decides

Same pipeline as research/fixes_test.py: ob_fvg_retest, completed bars,
option-priced (research/lab/realistic.py, costs inside the cap = F5),
the same-side 30-minute cooldown (F4), live gates, split 25 May 2026.
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

VARIANTS = [
    ("today's trailing", {}),
    ("trail from first higher low", {"trail_from_first_swing": True}),
    ("target floor 2R", {"target_floor_r": 2.0}),
    ("skip if target < 2R", {"min_target_r": 2.0}),
    ("extensions 2 -> 10", {"max_target_extensions": 10}),
    ("no time stop (wait for SL)", {"time_stop_min": 0.0}),
    ("first-swing + floor 2R + ext 10", {"trail_from_first_swing": True, "target_floor_r": 2.0,
                                         "max_target_extensions": 10}),
    ("owner's full rule (+ no time stop)", {"trail_from_first_swing": True, "target_floor_r": 2.0,
                                            "max_target_extensions": 10, "time_stop_min": 0.0}),
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
        print(line(label, tr, base), "| exits", dict(sorted(why.items(), key=lambda kv: -kv[1])),
              flush=True)
        base = base or tr


if __name__ == "__main__":
    main()
