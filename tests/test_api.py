#!/usr/bin/env python3
"""Broker API hygiene: every REST call is logged, and nothing hammers Angel.

A fake SmartAPI plays the broker. No network, no orders.
"""
import csv
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb import apilog

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


def wait_rows(path, n, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            with open(path) as fh:
                rows = list(csv.DictReader(fh))
            if len(rows) >= n:
                return rows
        time.sleep(0.05)
    return []


# ─────────────────────────────────────────────────────────────────────────────
section("1. Every REST call lands in api_calls.csv, with its outcome")


class Api:
    def ltpData(self, *a):
        return {"status": True, "data": {"ltp": 1.0}}

    def orderBook(self):
        return {"status": False, "message": "Access denied because of exceeding access rate"}

    def getCandleData(self, p):
        raise RuntimeError("Couldn't parse the JSON response received from the server: "
                           "b'Access denied because of exceeding access rate'")

    def position(self):
        return None

    def not_an_endpoint(self):
        return 1


path = os.path.join(tempfile.mkdtemp(), "api_calls.csv")
api = apilog.instrument(Api(), path=path)
check("instrumenting twice is a no-op", apilog.instrument(api, path=path) is api
      and hasattr(api.ltpData, "__wrapped__") and not hasattr(api.ltpData.__wrapped__, "__wrapped__"))
api.ltpData("NSE", "NIFTY", "1")
api.orderBook()
try:
    api.getCandleData({})
    raised = False
except RuntimeError:
    raised = True
api.position()
api.not_an_endpoint()
check("a failing call still raises to its caller", raised)
rows = wait_rows(path, 4)
got = {r["endpoint"]: r["outcome"] for r in rows}
check("four calls logged, the non-endpoint not", len(rows) == 4, str(got))
check("ok / rate_limited (dict) / rate_limited (raised) / error (None) told apart",
      got == {"ltpData": "ok", "orderBook": "rate_limited",
              "getCandleData": "rate_limited", "position": "error"}, str(got))
check("rows carry pid and thread", all(r["pid"] == str(os.getpid()) and r["thread"] for r in rows))
check("a comma in an error message cannot break the CSV",
      all(len(r) == len(apilog.HEADER.split(",")) for r in rows))

# ─────────────────────────────────────────────────────────────────────────────
section("2. A socket that hangs on close is abandoned, not waited for (29 Sep 12:49)")

import threading                                   # noqa: E402
from types import SimpleNamespace                  # noqa: E402
from arb import feed as F                          # noqa: E402


TICK = {"token": "1", "last_traded_price": 10000,
        "best_5_buy_data": [{"flag": 1, "price": 9990, "quantity": 65, "no of orders": 1}],
        "best_5_sell_data": [{"flag": 0, "price": 10010, "quantity": 65, "no of orders": 1}]}


class FakeWS:
    """One fake connection. mode: 'tick' sends ticks until closed; 'hang' ticks
    a little, then goes silent and ignores close_connection; 'never' never opens."""
    SNAP_QUOTE = 3
    made = []
    plan = []

    def __init__(self, *a, **kw):
        self.mode = FakeWS.plan.pop(0) if FakeWS.plan else "tick"
        self.closed = threading.Event()
        FakeWS.made.append(self)

    def subscribe(self, *a):
        pass

    def connect(self):
        if self.mode == "never":
            threading.Event().wait()                 # blocks for ever
        self.on_open(None)
        n = 0
        while True:
            if self.mode == "tick" and self.closed.is_set():
                self.on_close(None)
                return
            if self.mode == "tick" or n < 3:
                self.on_data(None, TICK)
            n += 1
            time.sleep(0.1)

    def close_connection(self):
        if self.mode == "tick":
            self.closed.set()                        # 'hang' ignores it, like the real stall


def until(cond, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.1)
    return cond()


F.SmartWebSocketV2 = FakeWS
session = SimpleNamespace(jwt="j", feed_token="f", login=lambda: None,
                          creds=SimpleNamespace(api_key="k", client_id="c"))
logs = []
FakeWS.plan = ["hang", "tick"]
mf = F.MarketFeed(session, 4, log=logs.append, stale_sec=1.0)
mf.register("1")
mf.start()
until(lambda: mf.stalls >= 1)
check("the hung socket is declared dead after the quiet limit",
      mf.stalls >= 1 and any("no ticks" in m for m in logs), f"stalls {mf.stalls}")
check("a second socket is opened even though the first never returned",
      until(lambda: len(FakeWS.made) >= 2), f"{len(FakeWS.made)} sockets")
until(lambda: len(FakeWS.made) >= 2 and mf.connected.is_set())
before = mf.ticks
time.sleep(0.6)
check("ticks flow again on the new socket", mf.ticks > before and mf.connected.is_set(),
      f"{before} -> {mf.ticks}")
mf.stop()

FakeWS.made, FakeWS.plan, logs[:] = [], ["never", "tick"], []
mf = F.MarketFeed(session, 4, log=logs.append, stale_sec=1.0)
mf.connect_timeout = 1.0
mf.register("1")
mf.start()
until(lambda: mf.ticks > 0)
check("a connect that never opens is given up on and retried",
      any("not open" in m for m in logs) and len(FakeWS.made) >= 2 and mf.ticks > 0,
      f"{len(FakeWS.made)} sockets, {mf.ticks} ticks")
mf.stop()

FakeWS.made, FakeWS.plan, logs[:] = [], ["tick"], []
mf = F.MarketFeed(session, 4, log=logs.append, stale_sec=1.0)
mf.register("1")
mf.start()
time.sleep(2.5)
check("a healthy socket is left alone", mf.stalls == 0 and len(FakeWS.made) == 1,
      f"stalls {mf.stalls}, sockets {len(FakeWS.made)}")
mf.stop()
time.sleep(0.8)
check("stop() ends the feed without reconnecting", len(FakeWS.made) == 1)

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
