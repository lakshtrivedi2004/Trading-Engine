#!/usr/bin/env python3
"""Does the event calendar (config/events.json) help the live book? OB + LHM on
the year (same cache and runner as research/smc_lab.py), event days kept,
skipped, or traded only after the announcement hour (RBI 10:00, Budget 11:00).

    venv/bin/python research/event_filter_test.py > research/event_filter_result.txt
"""
import json, os, pickle, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research")]
import frequency_test as FT  # noqa: E402
from frequency_test import M, rz, INDICES  # noqa: E402

EV = json.load(open(os.path.join(ROOT, "config", "events.json")))
AFTER = {"rbi": 10 * 60 + 15, "budget": 12 * 60, "fed": 0}


def main():
    K = pickle.load(open(M.PKL, "rb"))
    PP = {n: M.prep(rz.Index(n)) for n in INDICES}
    FT.SESS = M.SESS = len(PP["NIFTY"]["keys"])
    book = K["OB"] + K["LHM"]
    FT.BASE = FT.stat(FT.run(book))
    print(FT.fmt("BASELINE OB + LHM", FT.BASE) + f" {FT.BASE['by']}")
    for kinds in (["rbi"], ["fed"], ["budget"], ["rbi", "budget"], ["rbi", "budget", "fed"]):
        days = {d: k for k in kinds for d in EV[k]}
        on = [t for t in book if t["day"] in days]
        print(f"\n== {'+'.join(kinds)}: {len(days)} dates, "
              f"{len({t['day'] for t in on})} with book trades")
        print(FT.fmt("   only those days", FT.stat(FT.run(on))))
        print(FT.fmt("   skip those days", FT.stat(FT.run([t for t in book if t["day"] not in days])), FT.BASE))
        late = [t for t in book if t["day"] not in days or t["m"] >= AFTER[days[t["day"]]]]
        print(FT.fmt("   event days: only after the announcement", FT.stat(FT.run(late)), FT.BASE))


if __name__ == "__main__":
    main()
