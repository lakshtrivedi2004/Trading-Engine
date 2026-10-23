#!/usr/bin/env python3
"""The retest read on 1, 3, 5 and 10-minute candles (owner, 23 Oct 2026) against
the live 1-minute read, through the real engine: tools/replay_engine.py play_day
(every live gate, the live order path over a fake Angel, Black-Scholes premiums
as research/lab/realistic.py) on the 1-minute NIFTY / BANKNIFTY / SENSEX bars
in .cache/history.

Only retest_timeframes differs between variants. last_half_hour, its AI check
and the noise-breakout shadow are off, so the retest book is measured alone.
Split 2026-05-25 (research/lab/common.py); a variant is better only if it is
better in both halves.

    venv/bin/python research/retest_tf_test.py run 1            # -> research_out/retest_tf/tf_1.json
    venv/bin/python research/retest_tf_test.py run 1,3,5,10
    venv/bin/python research/retest_tf_test.py report > research/retest_tf_result.txt
"""
import json
import os
import sys
import tempfile
import time
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research_out", "retest_tf")
SPLIT = "2026-05-25"
WARMUP_SESSIONS = 8                     # the candle store is seeded with the prior ~8 sessions


def run(variant):
    tfs = tuple(int(x) for x in variant.split(","))
    sys.path[:0] = [os.path.join(ROOT, "tools"), ROOT, os.path.join(ROOT, "research", "lab")]
    argv, sys.argv = sys.argv, [sys.argv[0]]
    import replay_engine as R           # noqa: E402  (installs the replay clock)
    sys.argv = argv
    from arb.capital_book import CapitalBook
    from arb.config import IntradayLimits

    load = IntradayLimits.load

    def patched(path=None):
        L = load(path)
        L.last_half_hour, L.lhm_ai_verify, L.noise_breakout = "off", "off", "off"
        L.retest_timeframes = tfs
        return L
    IntradayLimits.load = staticmethod(patched)
    L = IntradayLimits.load()

    bars = {n: R.load_bars(n) for n in R.SPEC}
    days = sorted({r[0][:10] for r in bars["NIFTY"]})[WARMUP_SESSIONS:]
    os.makedirs(OUT, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"tf_{'_'.join(map(str, tfs))}_", dir=OUT)

    class Args:
        live_broker = True
    R.CLOCK.set(R.datetime.fromisoformat(days[0] + "T09:00:00+05:30"))
    capital = CapitalBook(float(L.capital_ceiling or L.starting_capital),
                          path=os.path.join(work, "capital.json"), ceiling=L.capital_ceiling)
    trades, problems, t0 = [], [], time.time()
    for k, d in enumerate(days):
        logs = []
        r = R.play_day(date.fromisoformat(d), bars, work, capital, Args, logs)
        if r is None:
            continue
        for x in r["records"]:
            trades.append({"day": d, "u": x.get("underlying"), "symbol": x.get("symbol"),
                           "net": float(x.get("net_inr") or 0.0), "why": x.get("exit_reason"),
                           "r": x.get("r_multiple"), "tf": x.get("retest_tf_min", 1),
                           "playbooks": x.get("playbooks"), "entry_reason": x.get("entry_reason", "")[:160]})
        if r["errors"]:
            problems.append(f"{d}: {len(r['errors'])} loop errors, first {r['errors'][0]}")
        if r["net_left"] or r["open_left"]:
            problems.append(f"{d}: left open {r['net_left'] or r['open_left']}")
        print(f"{d} [{k + 1}/{len(days)}] {len(r['records'])} trades | {len(trades)} so far, "
              f"net Rs {sum(t['net'] for t in trades):+,.0f} | {time.time() - t0:.0f}s", flush=True)
    path = os.path.join(OUT, f"tf_{'_'.join(map(str, tfs))}.json")
    with open(path, "w") as fh:
        json.dump({"variant": list(tfs), "sessions": len(days), "first": days[0], "last": days[-1],
                   "trades": trades, "problems": problems}, fh, indent=1)
    print(f"wrote {path}")


def stats(tr):
    import numpy as np
    tr = sorted(tr, key=lambda t: t["day"])
    p = np.array([t["net"] for t in tr], dtype=float)
    if not len(p):
        return {"n": 0, "win": 0.0, "net": 0.0, "avg": 0.0, "pf": 0.0, "dd": 0.0, "i": 0.0, "o": 0.0,
                "mpos": 0.0}
    eq = np.cumsum(p)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    loss = -p[p < 0].sum()
    months = {}
    for t in tr:
        months[t["day"][:7]] = months.get(t["day"][:7], 0.0) + t["net"]
    return {"n": len(p), "win": 100.0 * (p > 0).mean(), "net": float(p.sum()), "avg": float(p.mean()),
            "pf": float(p[p > 0].sum() / loss) if loss > 0 else float("inf"), "dd": dd,
            "i": sum(t["net"] for t in tr if t["day"] < SPLIT), "o": sum(t["net"] for t in tr if t["day"] >= SPLIT),
            "mpos": 100.0 * sum(v > 0 for v in months.values()) / max(1, len(months))}


def fmt(label, s):
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"{label:34s} n={s['n']:4d}  win {s['win']:5.1f}%  net Rs {s['net']:+9,.0f}  avg {s['avg']:+6,.0f}  "
            f"PF {pf:>5s}  maxDD {s['dd']:8,.0f}  halves {s['i']:+8,.0f} / {s['o']:+8,.0f}  "
            f"months+ {s['mpos']:3.0f}%")


def report():
    res = {}
    for f in sorted(os.listdir(OUT)):
        if f.startswith("tf_") and f.endswith(".json"):
            d = json.load(open(os.path.join(OUT, f)))
            res[tuple(d["variant"])] = d
    if not res:
        raise SystemExit("no results - run the variants first")
    any_ = next(iter(res.values()))
    print(f"Retest read on candle sizes, real engine replay (tools/replay_engine.py), "
          f"{any_['sessions']} sessions {any_['first']} .. {any_['last']}, NIFTY / BANKNIFTY / SENSEX,")
    print(f"live gates, last_half_hour off, split {SPLIT}. Net after costs, 1 lot, Rs.\n")
    order = sorted(res, key=lambda v: (len(v), v))
    for v in order:
        d = res[v]
        print(fmt(f"retest_timeframes {list(v)}", stats(d["trades"])))
    base = res.get((1,))
    for v in order:
        if v == (1,) or base is None:
            continue
        a, b = stats(base["trades"]), stats(res[v]["trades"])
        both = b["i"] > a["i"] and b["o"] > a["o"]
        print(f"\n{list(v)} vs [1]: trades {b['n'] - a['n']:+d}, net {b['net'] - a['net']:+,.0f} "
              f"(halves {b['i'] - a['i']:+,.0f} / {b['o'] - a['o']:+,.0f}) -> "
              f"{'BETTER in both halves' if both else 'not better in both halves'}")
    for v in order:
        print(f"\n== {list(v)} by index")
        for u in ("NIFTY", "BANKNIFTY", "SENSEX"):
            print(fmt(f"   {u}", stats([t for t in res[v]["trades"] if t["u"] == u])))
        if len(v) > 1:
            print(f"   by the candle size that fired")
            for tf in v:
                print(fmt(f"     {tf}-minute", stats([t for t in res[v]["trades"] if int(t.get("tf") or 1) == tf])))
        if res[v]["problems"]:
            print("   problems: " + "; ".join(res[v]["problems"][:5]))


if __name__ == "__main__":
    if sys.argv[1:2] == ["run"]:
        run(sys.argv[2])
    else:
        report()
