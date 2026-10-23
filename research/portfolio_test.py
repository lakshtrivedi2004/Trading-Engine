#!/usr/bin/env python3
"""More trades without more risk per trade: the three index strategies that are
positive in both halves on their own, run TOGETHER as one book (owner, 6 Oct
2026: "two days, no trade - catch more trades while staying profitable").

  OB   ob_fvg_retest, the live playbook (playbook_lab candidates, live pace gate)
  LHM  last-half-hour momentum, NIFTY only, entry 15:00, exit 15:13
       (research_out/momentum/l1_feasible.py; shadow live since 5 Oct)
  NB   noise-area breakout, 14-session sigma, 09:45-14:45, vm 1.0, minr 1.5,
       noise trail, live pace gate (research/noise_breakout_test.py; shadow)

All priced the same way (realistic.reprice: the contract the engine would buy,
costs inside the cap, next expiry on expiry day), 4h trend with the trade, at
today's capital and today's risk cap (config: 8%), one position at a time across
everything, F4 30 min and the live daily halt (fixes_test.run). Every
combination is printed with trades per session.

    nice venv/bin/python research/portfolio_test.py > research/portfolio_result.txt
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S
import realistic as rz
from common import Features, gate_halves
from playbook_lab import INDICES, candidates
from fixes_test import priced, run, line
from noise_breakout_test import prep_n
from flow_noise_robust import atr_ratio_series
from arb.config import IntradayLimits

L = IntradayLimits.load(); rz.L = L
CAPITAL = 10672.74
IX = {n: rz.Index(n) for n in INDICES}; F = Features(IX)
cur = dict(L.atr_by_index)


def pace_ok(t):
    return gate_halves(t, *cur.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))


def reprice(C):
    out = []
    for t in C:
        r = rz.reprice(t, IX[t["u"]], CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                       slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
        if r:
            u = dict(t); u.update(r); out.append(u)
    return out


def lhm_gen(name, ix, P, at="15:00", adr_frac=0.5, minr=1.5, last="15:13"):
    """l1_feasible.gen, copied (that module runs on import)."""
    rows, out = ix.rows, []; c = P["c"]; atr = P["atr"]
    for d in P["keys"]:
        dd = P["D"][d]
        if not dd["sig"]:
            continue
        for i in dd["idx"]:
            if S.hm(rows[i][0]) + 1 != S.T(at):
                continue
            mv = c[i] - dd["pc"]
            if abs(mv) < adr_frac * dd["adr"]:
                continue
            side = 1 if mv > 0 else -1; a = atr[i]; stop = c[i] - side * minr * a
            j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), a, last=S.T(last))
            out.append(S.cand(name, ix, i, j, px, side, abs(c[i] - stop), why, "LHM"))
    return out


print(f"capital Rs {CAPITAL:,.0f}, risk cap {L.max_risk_pct_per_trade:g}%, daily halt {L.max_daily_loss_pct:g}%\n",
      flush=True)
# OB: the live playbook
C = candidates({"use_regime_gate": False, "playbooks": list(L.playbooks)}, 2)
ALL = priced(C, IX, CAPITAL, True)
OB = [dict(t, strat="OB") for t in ALL if F.h4_ret(t, 5) * t["side"] > 0 and pace_ok(t)]
# "Slow grind" (owner approved the test, 6 Oct 2026): the same retest setups the
# pace gate or the 4h trend refused, taken when the DAY's move from the open is
# with the setup by at least w x ADR (withday, tools/backtest.py).
GRIND = {w: [dict(t, strat=f"G{w:g}") for t in ALL
             if not (F.h4_ret(t, 5) * t["side"] > 0 and pace_ok(t)) and t.get("withday", -9) >= w]
         for w in (0.0, 0.15, 0.3)}
# LHM: NIFTY only
PP = {n: S.prep(IX[n]) for n in INDICES}
LHM = [dict(t, strat="LHM") for t in reprice(lhm_gen("NIFTY", IX["NIFTY"], PP["NIFTY"]))
       if F.h4_ret(t, 5) * t["side"] > 0]
# NB: 14-session sigma, live pace gate
P14 = {n: prep_n(IX[n], 14) for n in INDICES}
AR = {n: atr_ratio_series(P14[n]) for n in INDICES}
var = dict(name="N1", kind="noise", lo="09:45", hi="14:45", grid=30, vm=1.0, minr=1.5, exit=dict(trail="noise"))
NB = []
for n in INDICES:
    for t in reprice(S.gen(n, IX[n], P14[n], var)):
        t["ar"] = float(AR[t["u"]][t["i"]])
        if F.h4_ret(t, 5) * t["side"] > 0 and pace_ok(t):
            NB.append(dict(t, strat="NB"))
sessions = len({r[0][:10] for r in IX["NIFTY"].rows})
print(f"sessions {sessions}; candidates after gates: OB {len(OB)}, LHM {len(LHM)}, NB {len(NB)}, "
      + ", ".join(f"grind w>={w:g} {len(v)}" for w, v in GRIND.items()) + "\n", flush=True)


def show(label, ts):
    tr = run(ts, side_cooldown=30)
    by = {}
    for t in tr:
        by[t.get("strat", "?")] = by.get(t.get("strat", "?"), 0) + 1
    print(line(label, tr) + f" | {len(tr) / sessions:.2f}/session {by}", flush=True)
    return tr


for label, ts in (("OB alone (live today)", OB), ("LHM alone", LHM), ("NB alone", NB),
                  ("OB + LHM", OB + LHM), ("OB + NB", OB + NB), ("LHM + NB", LHM + NB),
                  ("OB + LHM + NB (all three)", OB + LHM + NB)):
    tr = show(label, ts)
print("\n-- slow grind: refused retest setups taken when the day moves with them --")
for w, G in GRIND.items():
    show(f"grind alone, day move >= {w:g} ADR", G)
    show(f"OB + grind {w:g}", OB + G)
    show(f"all three + grind {w:g}", OB + LHM + NB + G)
best = run(OB + LHM + NB, side_cooldown=30)
print("\nall three, per month:")
months = {}
for t in best:
    months.setdefault(t["day"][:7], []).append(t["net2"])
for m in sorted(months):
    print(f"  {m}  n={len(months[m]):3d}  Rs {sum(months[m]):+8,.0f}")
