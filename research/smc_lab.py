#!/usr/bin/env python3
"""The owner's Forex SMC models (13 Oct 2026), each tested alone and added to
the live book, on a year of real 1-minute NIFTY / BANKNIFTY / SENSEX bars.

Same pipeline and pass rule as research/frequency_test.py section 2:
  * candidates: research_out/freq_1007/build_pb.py <playbook> (the engine's own
    decision code, pace gate off, priced as the option the engine would buy,
    costs inside the 8% cap, next expiry on expiry day);
  * live gates: per-index half-day pace (atr_by_index) and the 4-hour trend;
  * the book: OB (ob_fvg_retest) + LHM (NIFTY 15:00), run through
    fixes_test.run with the 30-minute same-side cooldown, one position at a time
    and the live daily halt.
A playbook PASSES only if it is positive alone in both halves (split
2026-05-25) AND the book with it beats the book without it in both halves.

    venv/bin/python research/smc_lab.py > research/smc_lab_result.txt
"""
import os, sys, pickle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research")]
import frequency_test as FT                                   # noqa: E402
from frequency_test import M, rz, L, INDICES, Features, gate_halves, LIVE_PACE, OUT  # noqa: E402

NEW = ["breaker_retest", "ifvg_retest", "silver_bullet", "judas_sweep", "ote_mss", "eqhl_sweep"]


def main():
    K = pickle.load(open(M.PKL, "rb"))
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    PP = {n: M.prep(IX[n]) for n in INDICES}
    FT.SESS = M.SESS = len(PP["NIFTY"]["keys"])
    OB, LHM = K["OB"], K["LHM"]
    FT.BASE = FT.stat(FT.run(OB + LHM))
    print(f"sessions {FT.SESS}, split {FT.SPLIT}, capital Rs {FT.CAPITAL:,.2f}, risk {L.max_risk_pct_per_trade:g}%")
    print(FT.fmt("BASELINE OB + LHM (OB before the 12 Oct retest window)", FT.BASE) + f" {FT.BASE['by']}\n")
    trend = lambda t: F.h4_ret(t, 5) * t["side"] > 0
    passed = []
    G_all = {}
    for name in NEW:
        p = os.path.join(OUT, f"pb_{name}.pkl")
        if not os.path.exists(p):
            print(f"{name}: candidates not built"); continue
        Z = pickle.load(open(p, "rb"))
        G = [dict(t, strat=name) for t in Z["ALL"] if trend(t) and
             gate_halves(t, *LIVE_PACE.get(t["u"], (L.min_atr_ratio, L.min_atr_ratio)))]
        G_all[name] = G
        alone = FT.stat(FT.run(G))
        print(f"== {name}: raw {len(Z['C'])} priced {len(Z['ALL'])} after live gates {len(G)}")
        # Are the gates starving it? The same candidates with no pace gate, and with no gate at all.
        print(FT.fmt(f"   {name} trend only (no pace)", FT.stat(FT.run(
            [dict(t, strat=name) for t in Z["ALL"] if trend(t)]))))
        print(FT.fmt(f"   {name} no gates", FT.stat(FT.run([dict(t, strat=name) for t in Z["ALL"]]))))
        print(FT.fmt(f"   {name} ALONE", alone))
        for u in INDICES:
            print(FT.fmt(f"     {u}", FT.stat(FT.run([t for t in G if t["u"] == u]))))
        book = FT.stat(FT.run(OB + LHM + G))
        print(FT.fmt(f"   OB + LHM + {name}", book, FT.BASE) + f" {book['by']}")
        ok = alone["i"] > 0 and alone["o"] > 0 and FT.better(book)
        print(f"   VERDICT {name}: {'PASS' if ok else 'fail'} (alone + both halves: "
              f"{alone['i'] > 0 and alone['o'] > 0}; book better both halves: {FT.better(book)})")
        if ok:
            passed.append(name)
        print()
    if len(passed) > 1:
        tr = OB + LHM + [t for n in passed for t in G_all[n]]
        print(FT.fmt("OB + LHM + " + " + ".join(passed), FT.stat(FT.run(tr)), FT.BASE))
    print("PASSING:", passed)


if __name__ == "__main__":
    main()
