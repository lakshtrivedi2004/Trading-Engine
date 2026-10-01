#!/usr/bin/env python3
"""Why does the live engine take fewer trades than the backtest says it should?

    venv/bin/python research/replay_vs_lab.py 2026-09-22 2026-09-23 2026-09-24

For each day: run the REAL engine through tools/replay_engine.py, recording the
refusal reason it noted for each index on each bar it evaluated. Then generate
the backtest's candidates (tools/backtest.py:work, ob_fvg_retest, pace gate off)
for the same days, apply the live gates as research/playbook_lab.py does, and
print, for every candidate, what the engine said at that bar.
"""
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "tools"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT]
import replay_engine as rp                       # noqa: E402  (installs the replay clock)
import backtest as bt                            # noqa: E402
import realistic as rz                           # noqa: E402
from common import Features, gate_halves         # noqa: E402
from arb.config import IntradayLimits            # noqa: E402
from arb.capital_book import CapitalBook         # noqa: E402
import arb.intraday_engine as ie                 # noqa: E402


def engine_notes(day, bars, work, capital):
    """Run one day; return {(underlying, bar_iso): [reasons]} for each decision."""
    notes = defaultdict(list)
    cur = {"und": None}
    orig_binding = ie.IntradayEngine._binding_for
    orig_note = ie.IntradayEngine._note

    def binding_for(self, und):
        cur["und"] = und
        return orig_binding(self, und)

    def note(self, reason):
        orig_note(self, reason)
        now = rp.CLOCK.now_ist()
        bar = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00+05:30")
        notes[(cur["und"] or "-", bar)].append(reason)
    ie.IntradayEngine._binding_for = binding_for
    ie.IntradayEngine._note = note
    try:
        class A:
            live_broker = False
        r = rp.play_day(day, bars, work, capital, A, [])
    finally:
        ie.IntradayEngine._binding_for = orig_binding
        ie.IntradayEngine._note = orig_note
    return notes, r


def lab_candidates(day, bars, L):
    out = []
    bt.init({"use_regime_gate": False, "playbooks": list(L.playbooks)})
    for name in rp.SPEC:
        rows = bars[name]
        idx = [k for k, r in enumerate(rows) if r[0][:10] == day.isoformat()]
        if not idx:
            continue
        out += bt.work((name, rows, idx[0], idx[-1] + 1))
    return out


def main():
    days = [date.fromisoformat(d) for d in sys.argv[1:]] or [date(2026, 9, 24)]
    L = IntradayLimits.load()
    bars = {n: rp.load_bars(n) for n in rp.SPEC}
    work = os.path.join(ROOT, "research_out", "replay", "vs_lab")
    os.makedirs(work, exist_ok=True)
    rp.CLOCK.set(datetime.fromisoformat(days[0].isoformat() + "T09:00:00+05:30"))
    capital = CapitalBook(float(L.capital_ceiling), path=os.path.join(work, "capital.json"),
                          ceiling=L.capital_ceiling)
    # Lab's 4-hour trend and pace inputs.
    rz.L = L
    IX = {n: rz.Index(n) for n in rp.SPEC}
    F = Features(IX)
    halves = dict(L.atr_by_index or {})
    tally = Counter()
    for day in days:
        notes, r = engine_notes(day, bars, work, capital)
        C = lab_candidates(day, bars, L)
        print(f"\n== {day}: engine took {len(r['records'])} trades; "
              f"backtest has {len(C)} raw candidates")
        for t in sorted(C, key=lambda t: (t["m"], t["u"])):
            a, b = halves.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio))
            gate = "pace ok" if gate_halves(t, a, b) else "pace FAIL"
            h4 = F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
            said = notes.get((t["u"], t["iso"]), ["(no decision recorded)"])
            key = "+".join(sorted(set(said)))
            if gate == "pace ok" and h4:
                tally[key] += 1
            print(f"  {t['iso'][11:16]} {t['u']:9s} side {t['side']:+d} risk {t['risk']:6.1f} "
                  f"ar {t['ar']:.2f} {gate:9s} 4h {'with' if h4 else 'AGAINST':7s} -> engine: {said}")
    print("\nengine's reason on the candidates the LAB would have traded (pace ok + 4h with):")
    for k, v in tally.most_common():
        print(f"  {v:4d}  {k}")


if __name__ == "__main__":
    main()
