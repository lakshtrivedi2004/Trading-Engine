#!/usr/bin/env python3
"""Owner's question (1 Oct 2026): should the bot take only trades whose stop is
SHORT, so each loss is small? Tested on a year of real 1-minute index data
through the engine's own decision and exit code (tools/backtest.py).

    venv/bin/python research/short_stop_test.py              # current config
    venv/bin/python research/short_stop_test.py --procs 4

Two different things are both called "a short stop", and they are tested
separately because they behave differently:

  A. SELECT trades whose structural stop is already close (in ATRs of the
     1-minute index bar). The stop is still where the chart says the idea is
     wrong; the bot only skips setups whose invalidation is far away.
  B. RISK FEWER RUPEES per trade (a smaller max_risk_pct_per_trade). The stop
     stays where it is; the bot buys a lower-delta option so the same index
     stop costs less. This is research only - the live 6% is not changed.

Not tested here because the owner's own 7-session test already did: SHRINKING
the stop to fit a rupee amount (stop_policy="cap") - it lost Rs 12,720.

Each filter is applied BEFORE the one-position-at-a-time sequencing, so a
filter that skips a trade frees the slot for the next one, exactly as the
live engine would. In-sample before --split, held-out after. Option decay is
not charged (an upper bound, as tools/backtest.py says).
"""
import argparse
import json
import os
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import backtest as bt  # noqa: E402


def candidates(sets, procs):
    jobs = []
    for name in bt.LOT:
        p = os.path.join(ROOT, ".cache", "history", f"{name}.json")
        if not os.path.exists(p):
            continue
        rows = [r for r in json.load(open(p)) if "09:15" <= r[0][11:16] <= "15:29"]
        for lo in range(400, len(rows), 6000):
            jobs.append((name, rows, lo, min(lo + 6000, len(rows))))
    with Pool(procs, initializer=bt.init, initargs=(sets,)) as pool:
        out = [t for r in pool.map(bt.work, jobs) for t in r]
    return sorted(out, key=lambda t: (t["day"], t["m"], t["u"]))


def row(label, tr, split):
    a = bt.stats([t for t in tr if t["day"] < split])
    b = bt.stats([t for t in tr if t["day"] >= split])
    loss = [t["net2"] for t in tr if t["net2"] < 0]
    avg_loss = sum(loss) / len(loss) if loss else 0.0
    return (f"{label:30s} IN n={a['n']:4d} win={a['win']:3.0f}% net=Rs{a['net']:8,.0f} "
            f"R={a['R']:+.2f} | OUT n={b['n']:4d} win={b['win']:3.0f}% net=Rs{b['net']:7,.0f} "
            f"R={b['R']:+.2f} | avg loss Rs{avg_loss:6,.0f}  worst Rs{min([0] + loss):6,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2026-05-25")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()

    print("A. Take only trades whose structural stop is at most N ATR away\n")
    C = candidates({}, a.procs)
    sa = lambda t: t["risk"] / t["atr"] if t.get("atr") else 99.0
    print(row("all (today's rule)", bt.run(C), a.split))
    for cap in (1.5, 2.0, 2.5, 3.0, 4.0):
        print(row(f"stop <= {cap:g} ATR", bt.run(C, ok=lambda t, c=cap: sa(t) <= c), a.split))
    print()
    for lo, hi in ((1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 99)):
        print(row(f"  bucket {lo:g}-{hi:g} ATR",
                  bt.run(C, ok=lambda t, l=lo, h=hi: l <= sa(t) < h), a.split))

    print("\nB. Risk fewer rupees per trade (stop unchanged; lower-delta option)\n")
    for pct in (6.0, 4.0, 3.0, 2.0):
        Cp = candidates({"max_risk_pct_per_trade": pct}, a.procs)
        print(row(f"risk {pct:g}% (Rs {100 * pct:,.0f} on 10k)", bt.run(Cp), a.split))


if __name__ == "__main__":
    main()
