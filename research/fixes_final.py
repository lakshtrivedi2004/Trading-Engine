#!/usr/bin/env python3
"""Final check of the fixes chosen from research/fixes_test.py, with the F5
formula as the engine implements it (round trip + 1 spread + 1% of premium,
no double-counted entry spread): baseline, F4 30 min, F5, F4 + F5."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features
from playbook_lab import INDICES, candidates, live_gates
from fixes_test import priced, run, line
from arb.config import IntradayLimits

L = IntradayLimits.load(); rz.L = L
capital = float(L.capital_ceiling or L.starting_capital)
IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 4)
P = live_gates(priced(C, IX, capital, False), F, L)
P5 = live_gates(priced(C, IX, capital, True), F, L)
base = run(P)
print(line("baseline (F1 = completed bars)", base))
print(line("F4 same-side 30 min", run(P, side_cooldown=30), base))
print(line("F5 costs inside the cap", run(P5), base))
print(line("F4 30 min + F5", run(P5, side_cooldown=30), base))
