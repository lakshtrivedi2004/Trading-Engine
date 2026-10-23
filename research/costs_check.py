#!/usr/bin/env python3
"""Engine-faithful costs (2 Oct 2026 system recheck): the lab re-run with the
exact BUY + SELL fees the journal books and the engine's own friction test,
for today's fixes. The earlier tables used a flat Rs 75 a lot and no friction
test, which let through trades the live engine refused (tools/replay_engine.py
took 1 trade in 21 sessions where the lab expected about 8)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features
from playbook_lab import INDICES, candidates, live_gates
from fixes_test import run, line
from arb.config import IntradayLimits

L = IntradayLimits.load(); rz.L = L
capital = float(L.capital_ceiling or L.starting_capital)
IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 4)


def priced(costs_inside, exact):
    out = []
    for t in C:
        r = rz.reprice(t, IX[t["u"]], capital, next_expiry_on_expiry_day=True,
                       costs_inside=costs_inside, exact_costs=exact)
        if r is not None:
            u = dict(t); u.update(r); out.append(u)
    return live_gates(out, F, L)


base = run(priced(False, False))
print(line("old lab: flat Rs 75, no friction", base))
ex = run(priced(False, True))
print(line("exact fees + friction test", ex, base))
print(line("exact + F4 30 min", run(priced(False, True), side_cooldown=30), base))
print(line("exact + F5", run(priced(True, True)), base))
print(line("exact + F4 + F5 (the live config)", run(priced(True, True), side_cooldown=30), base))
