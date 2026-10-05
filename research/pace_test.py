#!/usr/bin/env python3
"""Is the ATR-ratio pace gate worth it? (owner's question, 5 Oct 2026)

Candidates for the live playbook are generated ONCE with the pace gate off
(each carries the gate's own atr_ratio, `ar`), option-priced as live (exact
costs, costs inside the cap), then every pace setting is applied afterwards
with the 4h trend and one-position-at-a-time sequencing, as live. Also: how
much of each day the gate is open at today's settings."""
import os, sys, json
from collections import defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features, gate_halves
from playbook_lab import INDICES, candidates
from fixes_test import priced, run, line
from arb.config import IntradayLimits
from arb.regime import atr_ratio

L = IntradayLimits.load(); rz.L = L
capital = float(L.capital_ceiling or L.starting_capital)
IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 3)
P = [t for t in priced(C, IX, capital, True) if F.h4_ret(t, 5) * t["side"] > 0]
cur = dict(L.atr_by_index)
VARIANTS = [("no pace gate", {u: (0, 0) for u in INDICES}),
            ("1.0x all", {u: (1.0, 1.0) for u in INDICES}),
            ("1.15x all", {u: (1.15, 1.15) for u in INDICES}),
            ("1.2x all", {u: (1.2, 1.2) for u in INDICES}),
            ("current (N 1.3/1.5, BN 1.5, SX 1.2)", cur),
            ("1.5x all", {u: (1.5, 1.5) for u in INDICES})]
base = None
for name, halves in VARIANTS:
    keep = [t for t in P if gate_halves(t, *halves.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
    tr = run(keep, side_cooldown=30)
    print(line(name, tr, base), flush=True)
    base = base or tr
# Low-pace vs high-pace trades: where do the winners come from?
for lo, hi in ((0, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 9)):
    sub = [t for t in P if lo <= t["ar"] < hi]
    print(line(f"  only trades with pace {lo}-{hi}x", run(sub, side_cooldown=30)), flush=True)
# How long is the gate open per day at the current settings (09:30-15:00)?
for u in INDICES:
    rows = IX[u].rows
    open_min, days = defaultdict(int), set()
    for k in range(400, len(rows)):
        hm = rows[k][0][11:16]
        if not ("09:30" <= hm < "15:00"):
            continue
        d = rows[k][0][:10]; days.add(d)
        a, b = cur.get(u, (L.min_atr_ratio, L.min_atr_ratio))
        need = a if hm < "12:15" else b
        if k % 5 == 0 and atr_ratio(rows[k - 399:k + 1], period=L.atr_period, baseline_bars=L.atr_baseline_bars) >= need:
            open_min[d] += 5
    vals = [open_min.get(d, 0) for d in sorted(days)]
    never = sum(1 for v in vals if v == 0)
    print(f"{u}: gate open median {sorted(vals)[len(vals)//2]} min/day of 330; never open on {never}/{len(vals)} days")
