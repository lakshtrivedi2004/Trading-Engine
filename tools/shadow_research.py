#!/usr/bin/env python3
"""Forward shadow of the research candidates on each day's LIVE bars - no orders, ever.

    venv/bin/python tools/shadow_research.py            # after the close: today's shadow trades
    venv/bin/python tools/shadow_research.py --day 2026-10-21
    venv/bin/python tools/shadow_research.py --report   # everything logged so far

The owner (21 Oct 2026): what passed the year test goes live; the rest is
checked in shadow for future updates. These did not pass cleanly, so they
are only watched here:
  BB squeeze NIFTY    better in the book in 9 of 27 nearby settings, mostly after May
  Supertrend SENSEX   adds profit but triples the book's drawdown
  HKS NIFTY+SENSEX    half-hour periodicity, 9 of 27 settings
  LHM 0.3 ADR         LHM on smaller day moves (live: 0.5)
Each day: the engine's own 1-minute bars (.cache/candles, written live) are
appended to .cache/history_live, the research generators run exactly as in
the backtests (research/online_strategies_test.py, research/lhm_refine_test.py),
and the day's trades - priced with the backtest's option model at Rs 11,000 -
go to journal/shadow_research_<day>.jsonl. A candidate earns a place in the
engine only if its forward record agrees with its backtest.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_HIST = os.path.join(ROOT, ".cache", "history_live")
NAMES = ("NIFTY", "BANKNIFTY", "SENSEX")


def extend_history():
    """Append the engine's live-recorded bars to .cache/history_live."""
    os.makedirs(LIVE_HIST, exist_ok=True)
    for n in NAMES:
        path, live = os.path.join(LIVE_HIST, f"{n}.json"), os.path.join(ROOT, ".cache", "candles", f"{n}_1m.json")
        seed = os.path.join(ROOT, ".cache", "history", f"{n}.json")    # tools/fetch_history.py
        rows = json.load(open(path)) if os.path.exists(path) else \
            (json.load(open(seed)) if os.path.exists(seed) else [])
        if os.path.exists(live):
            seen = {r[0]: r for r in rows}
            for r in json.load(open(live)):
                seen[r[0]] = r
            rows = [seen[k] for k in sorted(seen)]
        json.dump(rows, open(path, "w"))
    return rows[-1][0][:10] if rows else ""


def run(day):
    os.environ["HISTORY_DIR"] = LIVE_HIST
    sys.argv = sys.argv[:1]          # research/exit_sweep.py reads argv[1] as capital on import
    sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"),
                    ROOT, os.path.join(ROOT, "tools")]      # research/lab/screen.py: tracked copy
    import online_strategies_test as O                   # noqa: E402  (loads the bars)
    import lhm_refine_test as R                          # noqa: E402
    import screen as S                                   # noqa: E402

    def priced(name, kind):
        return R.price([t for t in O.gen(name, kind) if t["day"] == day])
    legs = {
        "BB squeeze NIFTY": priced("NIFTY", "bb"),
        "Supertrend SENSEX": priced("SENSEX", "st"),
        "HKS NIFTY+SENSEX": [t for n in ("NIFTY", "SENSEX") for t in R.hks(n, tmin=1.5)
                             if t["day"] == day and t["m"] <= S.T("14:15")],
        "LHM 0.3 ADR": [t for t in R.lhm("NIFTY", frac=0.3) if t["day"] == day],
    }
    out = os.path.join(ROOT, "journal", f"shadow_research_{day}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        for strat, ts in legs.items():
            for t in ts:
                rows = O.IX[t["u"]].rows
                rec = dict(day=day, strategy=strat, index=t["u"], side="CE" if t["side"] > 0 else "PE",
                           entry=rows[t["i"]][0][11:16], exit=rows[t["j"]][0][11:16],
                           index_entry=round(rows[t["i"]][4], 2), why=t["why"],
                           strike=t.get("strike"), premium=round(t.get("prem", 0.0), 2),
                           net_inr=round(t["net"]))
                fh.write(json.dumps(rec) + "\n")
                print(f"{strat:20s} {t['u']:9s} {rec['side']} {rec['entry']}-{rec['exit']} "
                      f"{rec['why']:9s} Rs {rec['net_inr']:+,}")
            if not ts:
                print(f"{strat:20s} no signal")
    print(f"saved {out}")


def report():
    rows = [json.loads(l) for p in sorted(glob.glob(os.path.join(ROOT, "journal", "shadow_research_*.jsonl")))
            for l in open(p) if l.strip()]
    days = sorted({p[-16:-6] for p in glob.glob(os.path.join(ROOT, "journal", "shadow_research_*.jsonl"))})
    if not days:
        print("nothing logged yet")
        return
    print(f"{len(days)} days logged, {days[0]} - {days[-1]}\n")
    print("| Strategy | Trades | Win % | Net Rs | Avg per trade |\n|---|---|---|---|---|")
    for strat in sorted({r["strategy"] for r in rows}):
        xs = [r["net_inr"] for r in rows if r["strategy"] == strat]
        print(f"| {strat} | {len(xs)} | {100 * sum(x > 0 for x in xs) / len(xs):.0f} | "
              f"{sum(xs):+,} | {sum(xs) / len(xs):+,.0f} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
        return
    last = extend_history()
    run(a.day or last)


if __name__ == "__main__":
    main()
