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
    blocked = []

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
        if self.mode in ("silent", "silent_block"):     # opens, never ticks (a holiday)
            while not self.closed.is_set():
                time.sleep(0.05)
            return
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
        elif self.mode in ("block", "silent_block"):
            # The real library when the link died mid-frame: close waits for
            # the reader's frame lock and never returns (review F-H1).
            FakeWS.blocked.append(self)
            threading.Event().wait()


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

# Review F-H1 (2 Oct 2026): with the real library a link that dies mid-frame
# makes close_connection() block for good. The watchdog must not wait on it.
FakeWS.made, FakeWS.plan, logs[:] = [], ["block", "tick"], []
mf = F.MarketFeed(session, 4, log=logs.append, stale_sec=1.0)
mf.register("1")
mf.start()
ok = until(lambda: len(FakeWS.made) >= 2 and mf.connected.is_set() and mf.ticks > 3, 15.0)
before = mf.ticks
time.sleep(0.5)
check("a close that never returns does not stop the reconnect (watchdog not stuck)",
      ok and mf.ticks > before and len(FakeWS.blocked) >= 1 and mf._watchdog.is_alive(),
      f"sockets {len(FakeWS.made)}, blocked closes {len(FakeWS.blocked)}, ticks {mf.ticks}")
t = time.time()
mf.stop()
check("stop() returns at once even if the socket's close would block",
      time.time() - t < 1.0, f"{time.time() - t:.2f}s")

# Review F-L1: a socket that opens and never ticks (a market holiday) is a
# failure, so the delay grows and re-logins are rationed.
relogins = []
sess2 = SimpleNamespace(jwt="j", feed_token="f", login=lambda: relogins.append(1),
                        creds=SimpleNamespace(api_key="k", client_id="c"))
FakeWS.made, FakeWS.plan, logs[:] = [], ["silent"] * 20, []
mf = F.MarketFeed(sess2, 4, log=logs.append, stale_sec=0.5)
mf.max_retry_delay = 1.0
mf.register("1")
mf.start()
until(lambda: mf._consecutive_failures >= 4, 20.0)
mf.stop()
check("an open socket that never ticks counts as a failure: failures escalate",
      mf._consecutive_failures >= 4, f"failures {mf._consecutive_failures}")
check("...and re-login is rationed (at most once per relogin_every_sec)",
      len(relogins) <= 1, f"{len(relogins)} re-logins in {len(FakeWS.made)} sockets")

# 5 Oct 2026: a FAILED re-login is retried soon, not rationed for ten minutes.
tries = []


def bad_login():
    tries.append(time.time())
    raise ConnectionError("Temporary failure in name resolution")


sess3 = SimpleNamespace(jwt="j", feed_token="f", login=bad_login,
                        creds=SimpleNamespace(api_key="k", client_id="c"))
FakeWS.made, FakeWS.plan, logs[:] = [], ["silent"] * 40, []
mf = F.MarketFeed(sess3, 4, log=logs.append, stale_sec=0.5)
mf.max_retry_delay, mf.relogin_retry_sec = 1.0, 0.5
mf.register("1")
mf.start()
until(lambda: len(tries) >= 2, 25.0)
mf.stop()
check("a failed re-login is tried again after relogin_retry_sec, not after ten minutes",
      len(tries) >= 2, f"{len(tries)} login attempts")

# Review F-L2: a raising log must not kill the watchdog or the reconnect loop.
boom = {"n": 0}


def bad_log(msg):
    boom["n"] += 1
    if boom["n"] <= 3:
        raise OSError("disk full")


FakeWS.made, FakeWS.plan = [], ["hang", "tick"]
mf = F.MarketFeed(session, 4, log=bad_log, stale_sec=1.0)
mf.register("1")
mf.start()
ok = until(lambda: len(FakeWS.made) >= 2 and mf.connected.is_set(), 15.0)
check("a log that raises does not kill the watchdog or the reconnect loop",
      ok and mf._watchdog.is_alive() and mf._thread.is_alive(),
      f"sockets {len(FakeWS.made)}, watchdog {mf._watchdog.is_alive()}")
mf.stop()

leak = ("Error occurred while making a GET request to https://apiconnect.angelone.in/x?"
        "data=%7B%22refreshToken%22:%20%22eyJhbGciOiJIUzUxMiJ9.abcdefgh.ijklmnop%22%7D")
check("a URL-encoded refresh token in an exception text is redacted",
      "eyJhbGciOiJIUzUxMiJ9" not in F.redact(leak), F.redact(leak)[-60:])

# Review F-M2: rows still queued at exit are written, not lost.
import subprocess                                   # noqa: E402
csv = os.path.join(tempfile.mkdtemp(prefix="apilog_exit_"), "calls.csv")
code = ("import sys; sys.path.insert(0, %r)\n"
        "from arb import apilog\n"
        "class A:\n"
        "    def ltpData(self, *a): return {'status': True}\n"
        "a = apilog.instrument(A(), path=%r)\n"
        "for _ in range(4): a.ltpData()\n") % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), csv)
subprocess.run([sys.executable, "-c", code], check=False, timeout=60)
rows = open(csv).read().splitlines()[1:] if os.path.exists(csv) else []
check("every call made just before exit reaches api_calls.csv", len(rows) == 4, f"{len(rows)} of 4 rows")

# ─────────────────────────────────────────────────────────────────────────────
section("3. Confirming an order never polls orderBook faster than Angel's 1/s")

from arb.config import CostModel                   # noqa: E402
from arb.execution import LiveBroker               # noqa: E402
from arb.intraday_engine import _Ticket            # noqa: E402


class SlowBook:
    """Order status unreadable (as under a rate limit), so every poll falls
    back to orderBook; the order shows complete there on the 3rd read."""
    def __init__(self):
        self.calls = {"individual_order_details": [], "orderBook": [], "position": []}

    def placeOrderFullResponse(self, params):
        return {"status": True, "data": {"orderid": "7", "uniqueorderid": "u7"}}

    def individual_order_details(self, uid):
        self.calls["individual_order_details"].append(time.time())
        return None

    def orderBook(self):
        self.calls["orderBook"].append(time.time())
        st = "complete" if len(self.calls["orderBook"]) >= 3 else "open"
        return {"data": [{"orderid": "7", "status": st, "filledshares": "65" if st == "complete" else "0",
                          "averageprice": "101.0"}]}

    def position(self):
        self.calls["position"].append(time.time())
        return {"status": True, "data": []}

    def cancelOrder(self, oid, variety):
        pass


def gaps(ts):
    return [b - a for a, b in zip(ts, ts[1:])]


api = SlowBook()
b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True, confirm_timeout=6.0)
leg = {"symbol": "NIFTY29SEP2622800CE", "token": "1", "side": "BUY", "price": 100.0,
       "qty": 65, "instrument": "OPT", "exchange": "NFO"}
legs, errs = b.place_structure(_Ticket([leg], 65, "T", "t", ""), 1)
g = gaps(api.calls["orderBook"])
check("the order is still confirmed through the orderBook fallback",
      legs[0]["filled"] and legs[0]["fill_price"] == 101.0, str(errs))
check("orderBook calls are >= 1.1 s apart", g and min(g) >= 1.09, f"gaps {[round(x, 2) for x in g]}")
g = gaps(api.calls["individual_order_details"])
check("order-status calls are >= 0.5 s apart", g and min(g) >= 0.49,
      f"min gap {min(g):.2f}s over {len(g) + 1} calls" if g else "")
b.net_position("X")
b.net_position("X")
g = gaps(api.calls["position"])
check("back-to-back position reads are paced to 1/s", g and min(g) >= 1.09, f"gaps {g}")

# ─────────────────────────────────────────────────────────────────────────────
section("4. Place, modify and cancel are held by the order bucket only (Angel: 9/s together)")


class Orders:
    """Every order call stamps the clock; orders complete at once."""
    def __init__(self):
        self.calls = []

    def placeOrderFullResponse(self, params):
        self.calls.append(("place", time.time()))
        return {"status": True, "data": {"orderid": "9", "uniqueorderid": "u9"}}

    def individual_order_details(self, uid):
        return {"data": {"status": "complete", "filledshares": "65", "averageprice": "100.0"}}

    def cancelOrder(self, oid, variety):
        self.calls.append(("cancel", time.time()))
        return {"status": True, "data": {"orderid": oid}}


try:
    api = Orders()
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True, confirm_timeout=2.0)
    b.order_rate_per_sec = 8.0
    for _ in range(3):
        b.cancel_order("9", "NORMAL")
    b.place_structure(_Ticket([dict(leg)], 65, "T", "t", ""), 1)
    b.cancel_order("9", "NORMAL")
    g = gaps([t for _, t in api.calls])
    check("cancel and place share the bucket: >= 1/8 s apart at 8/s",
          len(g) == 4 and min(g) >= 0.124, f"gaps {[round(x, 3) for x in g]}")
    check("modifyOrder and cancelOrder have no poll gap of their own (bucket only)",
          "modifyOrder" not in LiveBroker.POLL_GAP and "cancelOrder" not in LiveBroker.POLL_GAP
          and "placeOrderFullResponse" not in LiveBroker.POLL_GAP and hasattr(b, "_order_gate"))
    api = Orders()
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    t0 = time.time()
    for _ in range(5):
        b.cancel_order("9", "NORMAL")
    check("with the rate at 0 (the code default) order calls are not held", time.time() - t0 < 0.1,
          f"{time.time() - t0:.3f}s")
except Exception as ex:                                # the parent commit has no bucket
    check("order bucket (raised)", False, repr(ex)[:200])

section("feed: reconnects never grow the subscription lists (5 Oct OOM)")
try:
    import threading as _th
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2 as _RealWS
    from arb import feed as _feed

    class _SentApp:
        def __init__(self):
            self.sent = []

        def send(self, data, *a, **k):
            self.sent.append(len(data))

    class _Stub:
        pass

    st = _Stub()
    st.subscriptions = {1: ["26000", "26009", "2885"], 2: ["35001", "35002"], 3: ["19000"]}
    st.correlation_id = "arbfeed01"
    st.connected = _th.Event()
    sizes, frames = [], []
    for gen in range(6):
        # The REAL SDK class (its subscribe keeps what it is given), a fake socket.
        st.sws = _RealWS("jwt", "key", "CLIENT", "feed", max_retry_attempt=0)
        st.sws.wsapp = _SentApp()
        _feed.MarketFeed._on_open(st, None)
        sizes.append({et: len(t) for et, t in st.subscriptions.items()})
        frames.append(st.sws.wsapp.sent[-1] if st.sws.wsapp.sent else 0)
    check("our lists stay the same size over 6 reconnects", all(x == sizes[0] for x in sizes),
          f"{sizes[0]} .. {sizes[-1]}")
    check("each subscribe frame is the same size", len(set(frames)) == 1 and frames[0] > 0, f"{frames}")
except Exception as ex:
    check("subscription growth (raised)", False, repr(ex)[:200])

section("scrip master: a failed download retries, then falls back to yesterday's file (6 Oct 09:17)")
try:
    import io as _io, json as _json, urllib.request as _ur
    from arb import instruments as _ins
    _saved = (_ins.CACHE_DIR, _ur.urlopen)
    _tmp = tempfile.mkdtemp()
    _ins.CACHE_DIR = _tmp
    calls = {"n": 0}

    def _mk(fail_first):
        def _open(req, timeout=None):
            calls["n"] += 1
            if calls["n"] <= fail_first:
                raise TimeoutError("The read operation timed out")
            return _io.BytesIO(_json.dumps([{"token": "1", "name": "NIFTY"}]).encode())
        return _open

    _ur.urlopen = _mk(2)
    data, src = _ins.load_scrip_master(force=True, retry_sec=0)
    check("two timeouts then a good download", src == "downloaded" and calls["n"] == 3 and data, src)
    calls["n"] = 0
    _ur.urlopen = _mk(99)
    data, src = _ins.load_scrip_master(force=True, retry_sec=0)
    check("all tries fail, cache present: yesterday's file, marked STALE",
          src.startswith("STALE") and data and calls["n"] == 3, src)
    os.remove(os.path.join(_tmp, "scrip_master.json"))
    try:
        _ins.load_scrip_master(force=True, retry_sec=0)
        check("all tries fail, no cache: raises", False)
    except TimeoutError:
        check("all tries fail, no cache: raises", True)
    _ins.CACHE_DIR, _ur.urlopen = _saved
except Exception as ex:
    check("scrip master fallback (raised)", False, repr(ex)[:200])

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
