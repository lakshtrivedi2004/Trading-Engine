#!/usr/bin/env python3
"""Who spent the Angel rate budget: a summary of logs/api_calls.csv.

    venv/bin/python tools/api_usage.py                 # today
    venv/bin/python tools/api_usage.py 2026-10-01      # one day
    venv/bin/python tools/api_usage.py --file other.csv

Per endpoint: calls, refusals, peak calls in any one second and any one
minute, median latency, and which threads made them. Then the first
rate-limit refusal and every call in the 60 s before it.
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.apilog import PATH  # noqa: E402


def load(path, day):
    with open(path) as fh:
        return [r for r in csv.DictReader(fh) if r["iso"].startswith(day)]


def peak(ts, width):
    """Most calls inside any window of `width` seconds."""
    best, j = 0, 0
    for i, t in enumerate(ts):
        while t - ts[j] >= width:
            j += 1
        best = max(best, i - j + 1)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--file", default=PATH)
    a = ap.parse_args()
    if not os.path.exists(a.file):
        sys.exit(f"no {a.file} yet - it is written once the engine has logged in")
    rows = load(a.file, a.day)
    if not rows:
        sys.exit(f"no calls logged on {a.day}")

    by = defaultdict(list)
    for r in rows:
        by[r["endpoint"]].append(r)
    print(f"{a.day}: {len(rows)} REST calls, pids {sorted({r['pid'] for r in rows})}\n")
    print(f"{'endpoint':26} {'calls':>6} {'refused':>7} {'limited':>7} "
          f"{'max/s':>5} {'max/min':>7} {'med ms':>6}  threads")
    for ep, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ts = sorted(float(r["ts"]) for r in rs)
        out = Counter(r["outcome"] for r in rs)
        ms = sorted(float(r["ms"]) for r in rs)
        threads = ", ".join(f"{t}:{n}" for t, n in Counter(r["thread"] for r in rs).most_common(3))
        print(f"{ep:26} {len(rs):>6} {out['refused'] + out['error']:>7} {out['rate_limited']:>7} "
              f"{peak(ts, 1.0):>5} {peak(ts, 60.0):>7} {ms[len(ms) // 2]:>6.0f}  {threads}")

    hit = next((r for r in sorted(rows, key=lambda r: float(r["ts"]))
                if r["outcome"] == "rate_limited"), None)
    if hit is None:
        print("\nno rate-limit refusals")
        return
    t = float(hit["ts"])
    print(f"\nfirst rate-limit refusal: {hit['iso']} {hit['endpoint']} "
          f"(pid {hit['pid']}, {hit['thread']})\ncalls in the 60 s before it:")
    for r in sorted(rows, key=lambda r: float(r["ts"])):
        if t - 60.0 <= float(r["ts"]) <= t:
            print(f"  {r['iso'][11:]}  pid {r['pid']:>7}  {r['thread']:14} "
                  f"{r['endpoint']:26} {r['outcome']}")


if __name__ == "__main__":
    main()
