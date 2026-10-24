"""The open BTST's price, as fast as Angel gives it (owner, 23 Oct 2026).

A target can be touched for a second and fall back; a price looked at every
few seconds misses that, and in paper - where the desk decides the fill - a
missed touch is a missed target. So the price comes from Angel's websocket
(arb/feed.py MarketFeed: one token, every tick), and from the LTP endpoint
every ltp_poll_sec (1 s) only while the socket is down or quiet.

Neither sees every trade. The exchange's own day high does, and it is in
every tick and every LTP answer: a day high that rises after the target order
went in is a trade at that price after it went in. So `peak()` - the highest
price traded since `mark()` - is exact, whatever happened between two looks.
(One blind spot: a day high already above the target when it was marked;
then only the looks themselves count.)

A socket that goes quiet in market hours for ws_dead_sec (90 s) is stopped
and started again (on top of MarketFeed's own reconnects); a new Angel session
(the daily refresh, a re-login) starts a new socket. Every switch between the
websocket and the LTP call is logged.
"""
import threading
import time

from arb.book import PAISE
from arb.feed import BSE_FO, NSE_FO, MarketFeed
from arb.instruments import market_phase, now_ist
from btst.logbook import fmt


class _Feed(MarketFeed):
    """MarketFeed that also hands every tick to the watch."""

    def __init__(self, watch, *a, **k):
        super().__init__(*a, **k)
        self.watch = watch

    def _on_data(self, wsapp, message):
        super()._on_data(wsapp, message)
        try:
            self.watch.see(str(message.get("token")), float(message.get("last_traded_price") or 0) * PAISE,
                           float(message.get("high_price_of_the_day") or 0) * PAISE, "websocket")
        except Exception:
            pass


class PriceWatch:
    """One contract at a time: the open job's."""

    def __init__(self, session_fn=None, rest_quote=None, log=print, ltp_poll_sec=1.0, ws_fresh_sec=5.0,
                 stale_sec=60.0, use_ws=True, clock=time.time, feed_cls=_Feed, today=lambda: now_ist().date(),
                 hours=("09:15", "15:45"), ws_dead_sec=90.0):
        self.session_fn, self.rest_quote, self.log = session_fn, rest_quote, log
        self.ltp_poll_sec, self.ws_fresh_sec = float(ltp_poll_sec), float(ws_fresh_sec)
        self.stale_sec, self.use_ws, self.clock, self.feed_cls = float(stale_sec), use_ws, clock, feed_cls
        self.today, self.hours, self.ws_dead_sec = today, hours, float(ws_dead_sec)
        self.feed_started_at = self.last_ws_at = 0.0
        self.restarts, self.used, self.switch_said_at = 0, None, 0.0
        self.lock = threading.Lock()
        self.token, self.symbol = None, None
        self.feed, self.feed_session, self.feed_failed_at = None, None, 0.0
        self.last = {}                  # ltp, high, at, source
        self.marked, self.base_high, self.top = None, None, None   # marked: the day of the mark
        self.last_rest, self.ticks, self.rest_calls = 0.0, 0, 0

    # ── what was seen ───────────────────────────────────────────────────────
    def see(self, token, ltp, high, source):
        """A look at the price: a websocket tick or an LTP answer."""
        if token != self.token:
            return
        # to the paisa: the socket's paise x 0.01 (358.65000000000003) and the LTP
        # call's rupees (358.65) are the same price, not a new day high
        ltp, high = round(float(ltp), 2), round(float(high), 2)
        with self.lock:
            if source == "websocket":
                self.ticks += 1
                self.last_ws_at = self.clock()
            if ltp > 0:
                self.last.update(ltp=ltp, at=self.clock(), source=source)
            if high > 0:
                self.last["high"] = high
            if self.marked != self.today():
                return                              # no mark, or yesterday's: the day high has reset
            if ltp > 0:
                self.top = max(self.top or 0.0, ltp)
            if high > 0:
                if self.base_high is None:
                    self.base_high = high           # the first look after the mark: its high may be older
                elif high > self.base_high:
                    self.top = max(self.top or 0.0, high)   # a new day high: traded after the mark
                    self.base_high = high

    def mark(self):
        """From now on, keep the highest price traded (a target order went in)."""
        with self.lock:
            fresh = self.last.get("at") and self.clock() - self.last["at"] <= self.stale_sec
            self.marked = self.today()
            self.base_high = self.last.get("high") if fresh else None
            self.top = None

    def peak(self):
        """The highest price traded since mark(), or None."""
        with self.lock:
            return self.top if self.marked == self.today() else None

    # ── getting it ──────────────────────────────────────────────────────────
    def track(self, c):
        """Contract c is the one watched (a new one forgets the old one's mark)."""
        tok = str(c["token"])
        if tok != self.token:
            self.stop()
            with self.lock:
                self.token, self.symbol, self.last = tok, c["symbol"], {}
                self.marked, self.base_high, self.top = None, None, None
            self.log(fmt("INFO", "price", f"watching {c['symbol']} ({tok}, {c['exchange']})"))

    def follow(self, c):
        """Watch contract c (and only c), on the websocket when there is one."""
        self.track(c)
        self._ensure_feed(c)

    def _ensure_feed(self, c):
        if not self.use_ws or self.session_fn is None:
            return
        try:
            sess = self.session_fn()
        except Exception as e:
            self.log(fmt("WARN", "price", f"no Angel session for the websocket: {e!r}"))
            return
        if self.feed is not None and self.feed_session is sess:
            return
        if self.feed is None and self.clock() - self.feed_failed_at < 60:
            return
        if self.feed is not None:                    # a new day's login: the old tokens are dead
            self.log(fmt("INFO", "price", "new Angel session - reconnecting the websocket"))
            self._stop_feed()
        try:
            f = self.feed_cls(self, sess, 4, correlation_id="btstfeed01", log=self._feed_log, stale_sec=30.0,
                              market_open=lambda: market_phase(start=self.hours[0], end=self.hours[1]) == "open")
            f.register(self.token, BSE_FO if c["exchange"] == "BFO" else NSE_FO)
            f.start()
            self.feed, self.feed_session, self.feed_started_at = f, sess, self.clock()
            self.log(fmt("INFO", "price", f"websocket started for {c['symbol']} - every tick; the LTP call "
                                          f"every {self.ltp_poll_sec:g} s only while it is quiet"))
        except Exception as e:
            self.feed, self.feed_failed_at = None, self.clock()
            self.log(fmt("WARN", "price", f"websocket not started ({e!r}) - LTP call every "
                                          f"{self.ltp_poll_sec:g} s instead; retrying in 60 s"))

    def _feed_log(self, text):
        self.log(fmt("WARN" if any(w in str(text).lower() for w in ("error", "failed", "dead", "no ticks"))
                     else "INFO", "price", str(text)))

    def _check_feed(self, c):
        """A socket quiet for ws_dead_sec in market hours is started again."""
        if self.feed is None or market_phase(start=self.hours[0], end=self.hours[1]) != "open":
            return
        quiet = self.clock() - max(self.last_ws_at, self.feed_started_at)
        if quiet > self.ws_dead_sec:
            self.restarts += 1
            self.log(fmt("WARN", "price", f"websocket quiet for {quiet:.0f}s in market hours - starting it again "
                                          f"(restart #{self.restarts}); the LTP call stands in meanwhile"))
            self._stop_feed()
            self.feed_failed_at = 0.0
            self._ensure_feed(c)

    def price(self, c):
        """The latest price: the websocket's if it ticked in the last ws_fresh_sec,
        else an LTP call (at most every ltp_poll_sec). None if nothing recent."""
        self.follow(c)
        self._check_feed(c)
        now = self.clock()
        with self.lock:
            fresh = (self.last.get("source") == "websocket" and self.last.get("at")
                     and now - self.last["at"] <= self.ws_fresh_sec)
        used = "websocket" if fresh else "ltp call"
        if used != self.used:
            if (self.used is not None or used == "ltp call") and now - self.switch_said_at >= 30:
                self.switch_said_at = now               # at most one switch line in 30 s (an illiquid strike)
                self.log(fmt("INFO" if used == "websocket" else "WARN", "price",
                             f"{self.symbol}: price from the websocket" if used == "websocket" else
                             f"{self.symbol}: no fresh websocket tick - price from the LTP call every "
                             f"{self.ltp_poll_sec:g}s"))
            self.used = used
        if not fresh and self.rest_quote and now - self.last_rest >= self.ltp_poll_sec:
            self.last_rest = now
            self.rest_calls += 1
            q = self.rest_quote(c)
            if q and q.get("ltp"):
                self.see(self.token, float(q["ltp"]), float(q.get("high") or 0), "ltp call")
        with self.lock:
            if self.last.get("at") and now - self.last["at"] <= self.stale_sec:
                return self.last.get("ltp")
        return None

    def state(self):
        with self.lock:
            age = round(self.clock() - self.last["at"], 1) if self.last.get("at") else None
            return {"symbol": self.symbol, "ltp": self.last.get("ltp"), "source": self.last.get("source"),
                    "age_sec": age, "day_high": self.last.get("high"),
                    "peak_since_target": self.top if self.marked == self.today() else None,
                    "websocket": bool(self.feed and self.feed.connected.is_set()), "ticks": self.ticks,
                    "ltp_calls": self.rest_calls, "ws_restarts": self.restarts,
                    "ws_quiet_sec": round(self.clock() - self.last_ws_at, 1) if self.last_ws_at else None}

    def _stop_feed(self):
        f, self.feed, self.feed_session = self.feed, None, None
        if f is not None:
            try:
                f.stop()
            except Exception:
                pass

    def stop(self):
        """Nothing to watch (no open trade, or the market is shut)."""
        if self.feed is not None:
            self.log(fmt("INFO", "price", f"websocket stopped for {self.symbol}"))
        self._stop_feed()
