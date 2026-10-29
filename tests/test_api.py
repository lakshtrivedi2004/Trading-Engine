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
    # 0.125 s target; a few ms of scheduler jitter under load is not a breach.
    check("cancel and place share the bucket: >= 1/8 s apart at 8/s (±10 ms)",
          len(g) == 4 and min(g) >= 0.115, f"gaps {[round(x, 3) for x in g]}")
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

section("day loop: a failed start is retried, a failure after the engine started is not (6 Oct 09:17)")
try:
    import run_bot as _rb
    _saved = (_rb._run_intraday_day, _rb._wait_until_session, _rb.START_RETRY_SEC, _rb.signal.signal)
    _rb.START_RETRY_SEC = 0.0
    _rb.signal.signal = lambda *a, **k: None

    class _Args:
        no_telegram, no_dashboard, duration, capital, port = True, True, None, None, 0

    class _Leftover:
        stopped = 0

        def stop(self):
            _Leftover.stopped += 1

    _rb._wait_until_session = lambda cfg, stopping, log: not stopping.is_set()
    seen = {"n": 0}

    def _day(cfg, cm, creds, args, mode, notifier, dash_holder, current, stopping):
        seen["n"] += 1
        if seen["n"] == 1:
            current["started"].append(_Leftover())
            raise TimeoutError("The read operation timed out")
        check("the retry starts clean: nothing left over from the failed start",
              current["started"] == [] and current["stage"] == "starting")
        current["stage"] = "running"
        stopping.set()

    _rb._run_intraday_day = _day
    _rb.run_intraday_forever(None, None, None, _Args(), "live")
    check("a start-up timeout is retried and the half-started parts are stopped",
          seen["n"] == 2 and _Leftover.stopped == 1, f"days {seen['n']}, stopped {_Leftover.stopped}")

    def _late(cfg, cm, creds, args, mode, notifier, dash_holder, current, stopping):
        current["stage"] = "running"
        raise RuntimeError("mid-day failure")

    _rb._run_intraday_day = _late
    try:
        _rb.run_intraday_forever(None, None, None, _Args(), "live")
        check("a failure after the engine started is NOT retried (raises)", False)
    except RuntimeError:
        check("a failure after the engine started is NOT retried (raises)", True)

    # 7 Oct: a refused live start ended the process; systemd restarted it into
    # the same refusal until the unit died, dashboard and Switch button with it.
    _saved_hold, _saved_dash = _rb._hold_until_close, (_rb.Dashboard, _rb.DashboardState)
    refused = {"days": 0, "held": 0}

    def _refuse(cfg, cm, creds, args, mode, notifier, dash_holder, current, stopping):
        refused["days"] += 1
        current["started"].append(_Leftover())
        raise SystemExit("LIVE START REFUSED: the account already has open positions")

    class _Dash:
        url = "http://test"

        def __init__(self, state, port=0):
            self.state = state

        def start(self):
            return self

        def stop(self):
            pass

    class _DState:
        def __init__(self, *a):
            self.start_refused = ""

    def _hold(cfg, stopping):
        refused["held"] += 1
        refused["shown"] = holder_state[0].start_refused
        stopping.set()

    holder_state = []
    _rb.Dashboard = lambda st, port=0: holder_state.append(st) or _Dash(st)
    _rb.DashboardState = _DState
    _rb._hold_until_close = _hold
    _rb.cached_spot, _saved_spot = (lambda name: 0.0), _rb.cached_spot
    # Refused during market hours; also keeps the idle live-funds login thread off.
    _rb.market_phase, _saved_phase = (lambda **k: "open"), _rb.market_phase
    _Leftover.stopped = 0

    class _CfgStub:
        session_start, session_end = "09:15", "15:30"

        def active_underlyings(self):
            return []

    class _ArgsDash(_Args):
        no_dashboard = False

    _rb._run_intraday_day = _refuse
    try:
        _rb.run_intraday_forever(_CfgStub(), None, None, _ArgsDash(), "live")
        check("a LIVE START REFUSED does not end the process (no systemd restart loop)", True)
    except SystemExit as ex:
        check("a LIVE START REFUSED does not end the process (no systemd restart loop)", False, str(ex))
    check("...and is NOT retried this session: one start, then it holds",
          refused["days"] == 1 and refused["held"] == 1, str(refused))
    check("...the half-started parts are stopped", _Leftover.stopped == 1)
    check("...the dashboard shows why", "open positions" in refused.get("shown", ""),
          refused.get("shown", ""))

    def _other_exit(cfg, cm, creds, args, mode, notifier, dash_holder, current, stopping):
        raise SystemExit("no tradable chains")

    _rb._run_intraday_day = _other_exit
    try:
        _rb.run_intraday_forever(None, None, None, _Args(), "live")
        check("any other SystemExit at start still ends the process", False)
    except SystemExit:
        check("any other SystemExit at start still ends the process", True)
    _rb._hold_until_close, (_rb.Dashboard, _rb.DashboardState) = _saved_hold, _saved_dash
    _rb.cached_spot, _rb.market_phase = _saved_spot, _saved_phase
    _rb._run_intraday_day, _rb._wait_until_session, _rb.START_RETRY_SEC, _rb.signal.signal = _saved
except Exception as ex:
    check("day loop retry (raised)", False, repr(ex)[:200])

section("live start: a failed order-book read is retried; a real working order still refuses (7 Oct 09:15)")
try:
    import run_bot as _rb2
    _rb2.READ_RETRY_SEC = 0.0

    class _OB:
        def __init__(self, answers):
            self.answers, self.calls = list(answers), 0

        def open_orders(self):
            self.calls += 1
            return self.answers.pop(0) if self.answers else None

    b1 = _OB([None, []])
    _rb2.refuse_working_orders(b1, log=lambda *_: None)
    check("one unreadable order book, then an empty one: the start goes ahead", b1.calls == 2)
    b2 = _OB([None, None, None])
    try:
        _rb2.refuse_working_orders(b2, log=lambda *_: None)
        check("three unreadable answers still refuse", False)
    except SystemExit:
        check("three unreadable answers still refuse", b2.calls == 3)
    b3 = _OB([[{"tag": "", "oid": "9", "symbol": "X", "status": "open", "side": "BUY", "variety": "NORMAL", "uid": "1"}]])
    try:
        _rb2.refuse_working_orders(b3, log=lambda *_: None)
        check("a working order nobody placed refuses at once", False)
    except SystemExit:
        check("a working order nobody placed refuses at once", b3.calls == 1)
except Exception as ex:
    check("start read retry (raised)", False, repr(ex)[:200])

section("an order from an unregistered IP (AG7002) is a refusal, and new entries pause (7 Oct 09:55)")
try:
    from arb.execution import LiveBroker as _LB, ip_refused as _ipr
    from arb.config import CostModel as _CM2
    from arb.intraday_engine import IntradayEngine as _IE2
    from types import SimpleNamespace as _NS2

    class _IpApi:
        def placeOrderFullResponse(self, params):
            return {"success": False, "message": "223.190.81.113 is not a registered IP, please check your registered IP.",
                    "errorCode": "AG7002", "data": ""}

    b = _LB(_IpApi(), _CM2(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    leg = {"symbol": "SENSEX26O0872300CE", "token": "1", "side": "BUY", "price": 100.0, "qty": 20,
           "instrument": "OPT", "exchange": "BFO", "kind": "E", "underlying": "SENSEX", "strike": 72300.0,
           "opt": "CE", "depth": 100, "age_ms": 0.0, "market": False, "quote_price": 100.0}
    b.protection_pct = 1.0
    b.order_rate_per_sec = 0.0
    msg = b._place_confirmed(leg)
    check("AG7002 is recognised", _ipr({"errorCode": "AG7002"}) and _ipr({"message": "x is not a registered IP"})
          and not _ipr({"message": "some other refusal"}))
    if msg is not None:
        check("...the leg is refused and final, not 'unknown'",
              leg.get("broker_status") == "refused" and leg.get("final") and leg.get("ip_refused"), str(leg))
    logs = []
    e = _NS2(paused_until=0.0, notifier=None, log=logs.append, _note=lambda *_: None, IP_PAUSE_SEC=300.0)
    hit = _IE2._ip_refused(e, [{"ip_refused": True}])
    check("the engine pauses new entries ~5 min and says what to do",
          hit and e.paused_until > time.time() + 290 and any("REGISTERED" in x.upper() for x in logs), str(logs[-1:]))
    check("an ordinary unfilled entry does not pause", not _IE2._ip_refused(e, [{"filled": False}]))
except Exception as ex:
    check("IP refusal (raised)", False, repr(ex)[:200])

section("switch to LIVE: a refused start is shown, can be retried, and holds till the close (7 Oct)")
try:
    import threading as _th
    import run_bot as _rb3
    from arb.dashboard import DashboardState as _DS, switch_target

    check("the Switch button flips the mode", switch_target("/api/control/switch_mode", "paper") == "live"
          and switch_target("/api/control/switch_mode", "live") == "paper")
    check("Retry LIVE asks for live while already live",
          switch_target("/api/control/switch_mode?to=live", "live") == "live")
    check("an unknown ?to= falls back to the flip",
          switch_target("/api/control/switch_mode?to=x", "live") == "paper")

    class _C:
        session_start, session_end = "09:15", "15:30"

    ds = _DS(None, None, None, None, _C(), "live", None)
    ds.intraday_mode = True
    snap = ds.snapshot()
    check("no refusal: the idle page is not halted", snap["halted"] is False and "start_refused" not in snap)
    ds.start_refused = "LIVE START REFUSED: could not read available funds from Angel (x)"
    snap = ds.snapshot()
    check("a refusal is the page's halt reason, with the Retry flag",
          snap["halted"] is True and snap["halt_reason"] == ds.start_refused
          and snap["start_refused"] == ds.start_refused, str(snap.get("halt_reason")))

    _saved_mp = _rb3.market_phase
    _rb3.market_phase = lambda **k: "closed"
    ev = _th.Event()
    t0 = time.time()
    _rb3._hold_until_close(_C(), ev)
    check("the hold ends at the close (next session checks again)", time.time() - t0 < 1.0)
    _rb3.market_phase = lambda **k: "open"
    ev.set()
    t0 = time.time()
    _rb3._hold_until_close(_C(), ev)
    check("the hold ends at once on a stop (switch / retry / restart)", time.time() - t0 < 1.0)
    _rb3.market_phase = _saved_mp
except Exception as ex:
    check("refused start shown (raised)", False, repr(ex)[:200])

section("Set paper capital: scalper page, paper only, never with a position open (28 Oct)")
try:
    import json
    import threading as _th
    import urllib.request as _ur
    from http.server import ThreadingHTTPServer as _Srv
    from arb.capital_book import CapitalBook as _CB
    from arb.dashboard import DashboardState as _DS, make_handler as _mh

    class _C2:
        session_start, session_end = "09:15", "15:30"

    class _Book:
        n = 0

        def count(self):
            return self.n

    class _Eng:
        def __init__(self, cap):
            self.capital, self.book, self.logs = cap, _Book(), []
            self.log = self.logs.append

    with tempfile.TemporaryDirectory() as _d:
        ds2 = _DS(None, None, None, None, _C2(), "paper", None)
        ds2.intraday_mode = True
        ds2.paper_ledger = os.path.join(_d, "capital.json")
        srv = _Srv(("127.0.0.1", 0), _mh(ds2))
        _th.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def _set(q):
            req = _ur.Request(f"{base}/api/control/paper_capital?{q}", method="POST")
            with _ur.urlopen(req, timeout=5) as r:
                return json.loads(r.read())

        res = _set("capital=50000")
        check("idle: the paper ledger on disk takes the figure",
              res.get("ok") and res.get("capital") == 50_000.0
              and _CB(0.0, path=ds2.paper_ledger).chosen == 50_000.0, str(res))
        check("...and the idle page shows what the next session trades",
              ds2.snapshot()["paper_capital"] == 50_000.0)
        for q in ("capital=abc", "capital=0", "capital=-5", "capital=nan", "capital=1e12", ""):
            check(f"a bad figure is refused ({q or 'none'})", _set(q).get("ok") is False)

        eng = _Eng(_CB(10_000.0, path=os.path.join(_d, "running.json"), ceiling=13_466.9))
        ds2.intraday = eng
        eng.book.n = 1
        res = _set("capital=25000")
        check("a position open: refused, capital untouched",
              res.get("ok") is False and eng.capital.capital == 10_000.0, str(res))
        eng.book.n = 0
        res = _set("capital=25,000")
        check("flat: a running session trades the new figure at once",
              res.get("ok") and eng.capital.capital == 25_000.0
              and any("paper capital set" in x for x in eng.logs), str(res))

        ds2.mode = "live"
        res = _set("capital=30000")
        check("live: refused, the account's funds decide",
              res.get("ok") is False and eng.capital.capital == 25_000.0, str(res))
        ds2.mode, ds2.intraday_mode = "paper", False
        check("the arbitrage desk: refused, scalper only", _set("capital=30000").get("ok") is False)
        srv.shutdown()
        srv.server_close()
except Exception as ex:
    check("set paper capital (raised)", False, repr(ex)[:200])

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
