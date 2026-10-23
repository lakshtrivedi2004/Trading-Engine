#!/usr/bin/env python3
"""Per-trade risk cap (% of capital) for the INDEX desk, 1-10% (owner, 6 Oct 2026:
"7% or below; give me a table for every stoploss value before anything changes").

Candidates of the live playbook are generated ONCE at the most permissive cap
(10%, a superset), then each cap re-prices them exactly as live (contract choice,
one lot must fit the cap with costs inside, exact costs), and the live gates
apply: 4h trend, per-index pace, F4 30 min, one position at a time, daily halt.
Run at today's live capital and at the ceiling used by the other research.

    nice venv/bin/python research/risk_cap_test.py > research/risk_cap_result.txt
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import realistic as rz
from common import Features, gate_halves
from playbook_lab import INDICES, candidates
from fixes_test import priced, run, line
from arb.config import IntradayLimits

L = IntradayLimits.load(); rz.L = L
IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks), "max_risk_pct_per_trade": 10.0}, 2)
cur = dict(L.atr_by_index)
print(f"raw candidates (pace gate off, 10% superset): {len(C)}", flush=True)
for capital in (10672.74, float(L.capital_ceiling or L.starting_capital)):
    print(f"\n=== capital Rs {capital:,.0f} ===")
    base = None
    for pct in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0):
        L.max_risk_pct_per_trade = pct
        P = [t for t in priced(C, IX, capital, True) if F.h4_ret(t, 5) * t["side"] > 0]
        G = [t for t in P if gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
        tr = run(G, side_cooldown=30)
        risk = sorted(t["prem_risk_inr"] for t in tr) if tr else [0]
        lbl = f"risk cap {pct:g}% (Rs {capital * pct / 100:,.0f})" + ("  <- live" if pct == 6.0 else "")
        print(line(lbl[:34], tr, base) + f" | priced {len(P)} gated {len(G)} | median risk Rs {risk[len(risk)//2]:,.0f}",
              flush=True)
        base = base or tr
    L.max_risk_pct_per_trade = 6.0
