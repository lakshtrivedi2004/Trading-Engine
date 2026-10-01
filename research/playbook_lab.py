#!/usr/bin/env python3
"""Every playbook, alone, through the engine's decision code and priced as the
option the engine would actually buy. One table, same rules for all.

    venv/bin/python research/playbook_lab.py                    # every playbook
    venv/bin/python research/playbook_lab.py ob_fvg_retest amd_retest
    venv/bin/python research/playbook_lab.py --set zone_stop_pad_atr=0.25 ob_fvg_retest

Pipeline, per playbook:
  1. tools/backtest.py:work with only that playbook enabled and the pace gate
     OFF, so every candidate carries its own atr_ratio and the gate is applied
     afterwards exactly as regime_gate would (research_out/2026-09-30/gen.py).
  2. research_out/2026-09-30/realistic.py:reprice: the contract
     arb/intraday.py would pick, Black-Scholes premium with measured decay and
     spreads, the premium stop and +1R breakeven on every bar.
  3. The live gates as configured today: per-index half-day pace
     (atr_by_index), the 4-hour trend with the trade (trend_4h_bars), the next
     expiry on expiry day, one position at a time across indices.

Capital and risk come from config/intraday.json (capital_ceiling,
max_risk_pct_per_trade). Split: in-sample before 2026-05-25, held-out after.
"""
import argparse
import json
import os
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "research_out", "2026-09-30")
sys.path[:0] = [LAB, ROOT, os.path.join(ROOT, "tools")]
import backtest as bt          # noqa: E402
import realistic as rz         # noqa: E402
from common import Features, gate_halves, run, summ  # noqa: E402
from arb.config import IntradayLimits  # noqa: E402
from arb.playbooks import PLAYBOOKS    # noqa: E402

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]


def candidates(sets, procs):
    jobs = []
    for name in INDICES:
        rows = rz.load_rows(name)
        for lo in range(400, len(rows), 3000):
            jobs.append((name, rows, lo, min(lo + 3000, len(rows))))
    with Pool(procs, initializer=bt.init, initargs=(sets,)) as pool:
        C = [t for r in pool.imap_unordered(bt.work, jobs) for t in r]
    return sorted(C, key=lambda t: (t["day"], t["m"], t["u"]))


def priced(C, IX, capital):
    """Reprice each candidate; on expiry day use the next expiry, as live does."""
    out = []
    for t in C:
        r = rz.reprice(t, IX[t["u"]], capital, next_expiry_on_expiry_day=True)
        if r is None:
            continue
        u = dict(t)
        u.update(r)
        out.append(u)
    return out


def live_gates(ts, F, L):
    halves = dict(L.atr_by_index or {})
    n4 = int(L.trend_4h_bars or 0)
    keep = []
    for t in ts:
        a, b = halves.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio))
        if not gate_halves(t, a, b):
            continue
        if n4 and F.h4_ret(t, n4) * t["side"] <= 0:
            continue
        keep.append(t)
    return keep


def line(label, tr):
    s = summ(tr)
    loss = [t["net2"] for t in tr if t["net2"] < 0]
    avg_loss = sum(loss) / len(loss) if loss else 0.0
    return (f"{label:24s} n={s['n']:3d} win={s['win']:3.0f}% year Rs{s['y']:+8,.0f} "
            f"(IN {s['i']:+7,.0f} / OUT {s['o']:+7,.0f}) avg {s['avg']:+5.0f} "
            f"avg loss {avg_loss:5,.0f} maxDD {s['dd']:6,.0f} months+ {s['mpos']:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    extra = {k: json.loads(v) for k, _, v in (p.partition("=") for p in a.set)}
    names = a.names or list(PLAYBOOKS)
    L = IntradayLimits.load()
    for k, v in extra.items():
        setattr(L, k, tuple(v) if isinstance(v, list) else v)
    capital = float(L.capital_ceiling or L.starting_capital)
    rz.L = L
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    print(f"capital Rs {capital:,.0f}, risk {L.max_risk_pct_per_trade:g}%, "
          f"indices {', '.join(INDICES)}, overrides {extra or 'none'}\n")
    for name in names:
        sets = dict(extra, use_regime_gate=False, playbooks=[name])
        C = candidates(sets, a.procs)
        P = priced(C, IX, capital)
        tr = run(live_gates(P, F, L))
        print(line(name, tr), f"| raw {len(C)} priced {len(P)}", flush=True)
        for u in INDICES:
            sub = [t for t in tr if t["u"] == u]
            if sub:
                print(line("    " + u, sub))


if __name__ == "__main__":
    main()
