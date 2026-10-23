#!/usr/bin/env python3
"""Owner, 6 Oct 2026: "keep the stop below 7%; avoid trades whose stop is high;
choose low-stop, high-target, high-probability trades". Does preferring small
stops help? Tested on the live playbook's priced candidates for the year
(research_out/pace_wf_P.pkl from research/pace_walkforward.py: 778 candidates,
4h trend applied, costs inside the 6% cap), with the live pace gate, F4 and one
position at a time (fixes_test.run, side_cooldown=30). Every variant printed.

    venv/bin/python research/stop_size_test.py > research/stop_size_result.txt
"""
import os, sys, pickle
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
from common import gate_halves
from fixes_test import run, line
from arb.config import IntradayLimits

L = IntradayLimits.load()
D = pickle.load(open(os.path.join(ROOT, "research_out", "pace_wf_P.pkl"), "rb"))
P, cap = D["P"], float(D["capital"])
cur = dict(L.atr_by_index)
G = [t for t in P if gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
print(f"capital {cap:,.0f}; candidates {len(P)}, after the live pace gate {len(G)}\n")
base = run(G, side_cooldown=30)
print(line("LIVE (no stop-size filter)", base))

def sa(t): return t["risk"] / t["atr"] if t["atr"] else 9
def rp(t): return 100 * t["prem_risk_inr"] / cap

print("\n-- trades bucketed by stop size in ATRs (each bucket run alone) --")
for lo, hi in ((0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99)):
    print(line(f"  stop {lo}-{hi} ATR", run([t for t in G if lo <= sa(t) < hi], side_cooldown=30)))
print("\n-- bucketed by planned rupee risk as % of capital --")
for lo, hi in ((0, 3), (3, 4.5), (4.5, 6.1), (6.1, 99)):
    print(line(f"  risk {lo}-{hi}% of capital", run([t for t in G if lo <= rp(t) < hi], side_cooldown=30)))
print("\n-- filters: skip trades whose stop is wider than X ATR --")
for x in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
    print(line(f"  stop <= {x} ATR", run([t for t in G if sa(t) <= x], side_cooldown=30), base))
print("\n-- filters: skip trades whose stop is NARROWER than X ATR --")
for x in (0.75, 1.0, 1.25, 1.5):
    print(line(f"  stop >= {x} ATR", run([t for t in G if sa(t) >= x], side_cooldown=30), base))
print("\n-- by grade --")
for g in sorted({t['grade'] for t in G}):
    print(line(f"  grade {g}", run([t for t in G if t['grade'] == g], side_cooldown=30)))
