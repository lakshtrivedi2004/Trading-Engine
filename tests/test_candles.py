#!/usr/bin/env python3
"""The candle store never hands the engine an unfinished minute as a closed bar,
and the shared historical-API budget degrades instead of failing.

Review D-H1 / D-B1-3 (2 Oct 2026): Angel's history API also returns the minute
still in progress. Merged as the last row, it became the "closed" bar the
closed-bar engine decided on, and the complete live-built bar for that minute
was then dropped at the roll. No network: a fake API and a controlled clock.
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import arb.candles as cdl
from arb.config import Underlying
from arb.instruments import IST

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" | {detail}" if detail else ""))


def section(t):
    print(f"\n[{t}]")


T0 = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
NOW = [T0.timestamp()]
_real_time = time.time


def at(minute, second):
    NOW[0] = (T0 + timedelta(minutes=minute, seconds=second)).timestamp()


def iso(minute):
    return (T0 + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%S+05:30")


class FakeApi:
    api_key = "TESTKEY1"

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def getCandleData(self, params):
        self.calls += 1
        return {"status": True, "data": [list(r) for r in self.rows]}


class Store:
    def __init__(self):
        self.ltp = np.zeros(4)
        self.bid = np.zeros(4)
        self.ask = np.zeros(4)
        self.volume = np.zeros(4)


tmp = tempfile.mkdtemp(prefix="candles_test_")
os.environ["TMPDIR"] = tmp
tempfile.tempdir = None
time.time = lambda: NOW[0]
try:
    # ═════════════════════════════════════════════════════════════════════════
    section("1. A history refresh during a minute never plants that minute")
    done = [[iso(m), 24000.0 + m, 24005.0 + m, 23995.0 + m, 24001.0 + m, 0.0] for m in range(5)]
    partial = [iso(5), 24051.0, 24052.0, 24049.0, 24051.0, 0.0]      # 10:05 at 10:05:20
    api = FakeApi(done + [partial])
    und = Underlying(name="NIFTY", spot_token="1")
    cs = cdl.CandleStore(api, [und], interval="1m", cache_dir=os.path.join(tmp, "c1"))
    st = Store()
    cs.attach_feed(st, {"NIFTY": 0})
    cdl.HIST_RETRY_SEC = (0.0, 0.0, 0.0)
    path = [(1, 24051.0), (10, 24090.0), (20, 24060.0), (40, 24088.0), (59, 24090.0)]
    at(5, 0.5)
    st.ltp[0] = 24050.0
    cs._sample_live()
    for sec, px in path:
        at(5, sec)
        st.ltp[0] = px
        cs._sample_live()
        if sec == 20:
            cs.refresh_once(force=True)
            check("the refresh keeps only finished minutes: the last row is 10:04",
                  cs.data["NIFTY"][-1][0] == iso(4), cs.data["NIFTY"][-1][0])
    at(6, 1)
    st.ltp[0] = 24089.0
    cs._sample_live()
    bar = cs.data["NIFTY"][-1]
    check("at the roll the complete 10:05 bar is in history, with its real high",
          bar[0] == iso(5) and bar[2] == 24090.0, str(bar[:5]))

    # A partial row for the minute already in history (an older process's cache,
    # or a refresh before this fix) is replaced by the live-built bar at the roll.
    cs2 = cdl.CandleStore(api, [und], interval="1m", cache_dir=os.path.join(tmp, "c2"))
    st2 = Store()
    cs2.attach_feed(st2, {"NIFTY": 0})
    cs2.data["NIFTY"] = [list(r) for r in done] + [list(partial)]
    for sec, px in path:
        at(5, sec)
        st2.ltp[0] = px
        cs2._sample_live()
    at(6, 1)
    st2.ltp[0] = 24089.0
    cs2._sample_live()
    b2 = cs2.data["NIFTY"][-1]
    check("a same-minute partial row is replaced by the complete live bar",
          b2[0] == iso(5) and b2[2] == 24090.0 and len(cs2.data["NIFTY"]) == 6, str(b2[:5]))

    at(5, 30)
    cs3 = cdl.CandleStore(api, [und], interval="1m", cache_dir=os.path.join(tmp, "c3"))
    cs3.seed("NIFTY", done + [partial])
    check("seed (stock picks) drops the minute still in progress",
          cs3.data["NIFTY"][-1][0] == iso(4), cs3.data["NIFTY"][-1][0])

    # ═════════════════════════════════════════════════════════════════════════
    section("2. The shared history budget")
    p = cdl._budget_path(api)
    check("the budget file name does not carry the API key",
          "TESTKEY1" not in p and "testkey1" not in p.lower(), os.path.basename(p))
    at(10, 0)
    with open(p, "w") as fh:
        json.dump([NOW[0] + 600.0], fh)                 # a stamp 10 min in the future
    t = _real_time()
    cdl._take_slot(api)
    check("a future stamp (clock stepped back) does not block the next call",
          _real_time() - t < 1.0, f"{_real_time() - t:.2f}s")

    class OtherApi:
        api_key = "OTHERKEY"
    lockdir = cdl._budget_path(OtherApi()) + ".lock"
    os.makedirs(lockdir, exist_ok=True)                  # unusable lock file
    ok = True
    try:
        cdl._take_slot(OtherApi())
    except OSError as e:
        ok = False
        print("   ", e)
    check("an unusable lock file falls back to in-process pacing, never raises", ok)
    # ═════════════════════════════════════════════════════════════════════════
    section("3. Exchange holidays are not trading days")
    from arb.instruments import market_phase
    check("Gandhi Jayanti 2026 (a Friday) is a holiday, not an open market",
          market_phase(datetime(2026, 10, 2, 10, 0, tzinfo=IST)) == "holiday")
    check("the next Monday at 10:00 is open",
          market_phase(datetime(2026, 10, 5, 10, 0, tzinfo=IST)) == "open")
    check("Diwali Balipratipada 2026 is a holiday",
          market_phase(datetime(2026, 11, 10, 11, 0, tzinfo=IST)) == "holiday")
    check("a weekend is still a weekend",
          market_phase(datetime(2026, 10, 3, 10, 0, tzinfo=IST)) == "weekend")
    # ═════════════════════════════════════════════════════════════════════════
    section("4. A dead feed builds no bars; zero-range bars never pass the pace gate (5 Oct 12:16)")

    class TickStore(Store):
        def __init__(self):
            super().__init__()
            self.recv_ns = np.zeros(4, dtype=np.int64)

    cs4 = cdl.CandleStore(FakeApi(done), [und], interval="1m", cache_dir=os.path.join(tmp, "c4"))
    st4 = TickStore()
    cs4.attach_feed(st4, {"NIFTY": 0})
    cs4.data["NIFTY"] = [list(r) for r in done]          # 10:00-10:04
    at(5, 1)
    st4.ltp[0] = 24050.0
    st4.recv_ns[0] = time.perf_counter_ns()               # fresh tick
    cs4._sample_live()
    check("a fresh tick opens the live bar", cs4.live.get("NIFTY", [None])[0] == iso(5))
    st4.recv_ns[0] = time.perf_counter_ns() - int(90e9)   # feed silent for 90 s
    for m in (6, 7, 8):
        at(m, 1)
        cs4._sample_live()
    check("no tick for 90 s: no new bar is invented for 10:06-10:08",
          cs4.live["NIFTY"][0] == iso(5) and cs4.data["NIFTY"][-1][0] == iso(4),
          f"live {cs4.live['NIFTY'][0][11:16]}, last row {cs4.data['NIFTY'][-1][0][11:16]}")
    st4.ltp[0] = 24070.0
    st4.recv_ns[0] = time.perf_counter_ns()               # ticks resume at 10:09
    at(9, 2)
    cs4._sample_live()
    check("ticks resume: the real 10:05 bar is kept, 10:09 opens, no flat 10:06-10:08",
          [r[0][11:16] for r in cs4.data["NIFTY"][-2:]] == ["10:04", "10:05"]
          and cs4.live["NIFTY"][0] == iso(9), str([r[0][11:16] for r in cs4.data["NIFTY"][-3:]]))
    st4.ltp[0] = 24071.0
    st4.recv_ns[0] = time.perf_counter_ns() - int(20e9)   # sparse but alive: 20 s since the last tick
    at(10, 1)
    cs4._sample_live()
    check("a 20 s gap between index ticks (normal for sparse slots) still rolls the bar",
          cs4.live["NIFTY"][0] == iso(10) and cs4.data["NIFTY"][-1][0] == iso(9))
    cs5 = cdl.CandleStore(FakeApi(done), [und], interval="1m", cache_dir=os.path.join(tmp, "c5"))
    st5 = TickStore()
    cs5.attach_feed(st5, {}, extra_slots={"RELIANCE": 1})
    st5.ltp[1] = 1400.0
    st5.recv_ns[1] = time.perf_counter_ns() - int(60e9)   # a quiet stock
    at(10, 1)
    cs5._sample_live()
    check("stocks are not held to the index tick-age rule",
          cs5.live.get("RELIANCE", [None])[0] == iso(10))

    from arb.config import IntradayLimits
    from arb.regime import regime_gate
    Lg = IntradayLimits()
    flat = [[iso(m), 24000.0, 24000.0, 24000.0, 24000.0, 0.0] for m in range(60)]
    why = regime_gate(flat, Lg, name="NIFTY")
    check("60 flat bars (dead feed) are refused, not passed as 'unknown regime'",
          why == "no_range_bars", repr(why))
finally:
    time.time = _real_time

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
