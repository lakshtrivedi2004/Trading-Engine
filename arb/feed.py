"""Angel One SmartAPI market feed.

The websocket callback is the hottest code in the process. It does exactly three
things: normalise the best-5 packet, store it into preallocated arrays by integer
slot, and signal the scanner. No allocation, no logging, no I/O, no locks.
ok
"""
import logging
import re
import threading
import time

import pyotp
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from arb.apilog import instrument

try:
    # Every websocket operation gets a deadline. Without one, a link that dies
    # mid-frame leaves the reader blocked until TCP gives up (~15 min) and
    # close_connection() blocked behind it (review F-H1, reproduced against the
    # real library). Angel's 10 s heartbeat keeps a healthy idle socket well
    # inside 20 s.
    import websocket as _websocket
    _websocket.setdefaulttimeout(20)
except Exception:                       # pragma: no cover - library missing
    pass
from arb.book import QuoteStore, normalise_best5, PAISE

NSE_CM, NSE_FO, BSE_CM, BSE_FO = 1, 2, 3, 4


class _RedactSecrets(logging.Filter):
    """SmartApi logs the whole request on any HTTP error - PIN, live TOTP, API
    key and bearer token included - so a pasted log line is a credential leak.
    Scrub those fields before the record reaches any handler."""
    PATTERNS = [
        (re.compile(r"('password':\s*')[^']*(')"), r"\1***\2"),
        (re.compile(r"('totp':\s*')[^']*(')"), r"\1***\2"),
        (re.compile(r"('X-PrivateKey':\s*')[^']*(')"), r"\1***\2"),
        (re.compile(r"('Authorization':\s*'Bearer )[^']*(')"), r"\1***\2"),
        (re.compile(r"('jwtToken':\s*')[^']*(')"), r"\1***\2"),
        (re.compile(r"('refreshToken':\s*')[^']*(')"), r"\1***\2"),
        (re.compile(r"('feedToken':\s*')[^']*(')"), r"\1***\2"),
        # The same fields URL-encoded in a query string or quoted in JSON: a
        # login that succeeds and a follow-up call that fails on the network
        # puts the refresh token in the exception text (review F-L7).
        (re.compile(r"((?:refreshToken|jwtToken|feedToken)(?:%22|\"|')?\s*(?::|%3A)\s*"
                    r"(?:%20|\s)*(?:%22|\"|')?(?:Bearer(?:%20|\s))?)[A-Za-z0-9._\-]{8,}"),
         r"\1***"),
    ]

    def filter(self, record):
        try:
            msg = record.getMessage()
            for pat, rep in self.PATTERNS:
                msg = pat.sub(rep, msg)
            record.msg, record.args = msg, ()
        except Exception:
            pass
        return True


def redact(text):
    """The same scrubbing for the engine's own log lines (exception texts)."""
    text = str(text)
    for pat, rep in _RedactSecrets.PATTERNS:
        text = pat.sub(rep, text)
    return text


def _install_redaction():
    try:
        from logzero import logger as lz
        if not any(isinstance(f, _RedactSecrets) for f in lz.filters):
            lz.addFilter(_RedactSecrets())
        for h in lz.handlers:
            if not any(isinstance(f, _RedactSecrets) for f in h.filters):
                h.addFilter(_RedactSecrets())
    except Exception:
        pass


# Network faults that are worth waiting out at login: DNS down, no route,
# connection reset, timeouts. Anything else (a wrong PIN, a bad TOTP seed,
# Angel rejecting the session) is raised immediately - retrying it would only
# lock the account.
_NETWORK_ERRORS = ("NameResolutionError", "MaxRetryError", "ConnectionError",
                   "ConnectionResetError", "ConnectTimeout", "ReadTimeout",
                   "NewConnectionError", "Name or service not known",
                   "Connection aborted", "Temporary failure in name resolution")


class AngelSession:
    def __init__(self, creds):
        self.creds = creds
        self.api = None
        self.jwt = None
        self.refresh = None
        self.feed_token = None
        _install_redaction()

    def login(self, retry_for_sec=0.0, log=None):
        """Log in; with ``retry_for_sec`` > 0, keep trying through network
        outages for up to that long (backing off 15s -> 60s) before giving up."""
        deadline = time.time() + retry_for_sec
        wait = 15.0
        while True:
            try:
                return self._login_once()
            except Exception as e:
                text = f"{type(e).__name__}: {e}"
                network = any(k in text for k in _NETWORK_ERRORS)
                if not network or time.time() + wait > deadline:
                    raise
                if log:
                    log(f"login: network unavailable ({type(e).__name__}); "
                        f"retrying in {wait:.0f}s")
                time.sleep(wait)
                wait = min(60.0, wait * 2)

    def _login_once(self):
        # Re-login reuses the same SmartConnect so every holder of ``session.api``
        # (margin service, candle store, live broker) sees the renewed tokens.
        if self.api is None:
            # Every REST call is timed into logs/api_calls.csv (arb/apilog.py).
            self.api = instrument(SmartConnect(api_key=self.creds.api_key))
        totp = pyotp.TOTP(self.creds.totp_secret).now()
        data = self.api.generateSession(self.creds.client_id, self.creds.pin, totp)
        if not (data and data.get("status")):
            msg = (data or {}).get("message", data)
            # SmartApi returns None (and logs the request) on transport failures;
            # surface that as a network error so the retry loop can catch it.
            if data is None:
                raise ConnectionError("Angel login: no response (ConnectionError)")
            raise RuntimeError(f"Angel login failed: {msg}")
        self.jwt = data["data"]["jwtToken"]
        self.refresh = data["data"]["refreshToken"]
        self.feed_token = self.api.getfeedToken()
        return self

    def spot_ltp(self, token="99926000", symbol="NIFTY", exchange="NSE"):
        r = self.api.ltpData(exchange, symbol, token)
        if r and r.get("data"):
            return float(r["data"].get("ltp") or 0.0)
        return 0.0


class MarketFeed:
    """Owns the websocket and the quote store."""

    def __init__(self, session: AngelSession, capacity, on_tick=None,
                 correlation_id="arbfeed01", log=print, stale_sec=15.0,
                 market_open=None):
        self.session = session
        self.store = QuoteStore(capacity)
        self.on_tick = on_tick
        self.correlation_id = correlation_id
        self.log = log
        self.sws = None
        self.subscriptions = {NSE_CM: [], NSE_FO: [], BSE_CM: [], BSE_FO: []}
        self.tick_event = threading.Event()
        self.last_tick_ns = 0
        self.ticks = 0
        self.dropped = 0
        self.connected = threading.Event()
        self._thread = None
        # Reconnection is owned here, not by the library. Its retries fire only
        # from the error callback, give up after a fixed count, and never notice
        # a connection that simply goes quiet - which is what a network blip
        # leaves behind: on 11 Sep 2026 the socket went silent at 10:55 with no
        # error or close event and the engine scanned frozen quotes for the rest
        # of the morning. The watchdog treats "no tick for stale_sec while the
        # market is open" as dead and forces a fresh connection.
        self.stale_sec = stale_sec
        self.market_open = market_open or (lambda: True)
        self.reconnects = 0
        self.stalls = 0
        self.last_reconnect_at = 0.0
        self._stopping = threading.Event()
        self._watchdog = None
        self._consecutive_failures = 0
        # Each connection is one generation. A socket the watchdog gives up on
        # can stay blocked inside the library for minutes (29 Sep 2026, 12:49:
        # close_connection() did not return it, and with no tick ever arriving
        # the old watchdog never looked again - two minutes with a position open
        # and no stop being checked). So a dead socket is abandoned, not waited
        # for: its thread is left to die, its callbacks are ignored, and _run
        # opens the next generation as soon as _dead is set.
        self._gen = 0
        self._gen_started = 0.0
        self._opened_ns = 0
        self._dead = threading.Event()  # the CURRENT generation's; each gen gets its own
        self.connect_timeout = 30.0
        self.max_retry_delay = 60.0
        self.relogin_every_sec = 600.0  # never re-login more often than this
        self._last_relogin = 0.0
        self._relogin_ok = True
        self.relogin_retry_sec = 60.0   # after a FAILED login
        self._slot_cache = self.store.slot_of_token
        # Which chain (expiry) each slot belongs to, and a monotonic per-chain
        # tick counter. A tick touches exactly one expiry, so the scanner can skip
        # the chains that have not changed instead of rebuilding all of them.
        self.slot_group = [-1] * capacity
        self.group_seq = []

    def register(self, token, exchange_type=NSE_FO, group=-1):
        slot = self.store.assign(str(token))
        self.subscriptions[exchange_type].append(str(token))
        if group >= 0:
            while len(self.group_seq) <= group:
                self.group_seq.append(0)
            self.slot_group[slot] = group
        return slot

    # ── hot path ────────────────────────────────────────────────────────────
    def _on_data(self, wsapp, message):
        try:
            slot = self._slot_cache.get(message["token"])
            if slot is None:
                return
            bid, bq, ask, aq = normalise_best5(
                message.get("best_5_buy_data"), message.get("best_5_sell_data"))
            now = time.perf_counter_ns()
            self.store.update_from_tick(
                slot, bid, ask, bq, aq,
                message.get("last_traded_price", 0) * PAISE,
                message.get("open_interest", 0),
                message.get("volume_trade_for_the_day", 0),
                message.get("exchange_timestamp", 0),
                now_ns=now,
                ohl=(message.get("open_price_of_the_day", 0) * PAISE,
                     message.get("high_price_of_the_day", 0) * PAISE,
                     message.get("low_price_of_the_day", 0) * PAISE),
            )
            g = self.slot_group[slot]
            if g >= 0:
                # Monotonic, never reset. A lost increment under the GIL only
                # delays that chain by one tick, and the next tick corrects it.
                self.group_seq[g] += 1
            self.ticks += 1
            self.last_tick_ns = now
            self.tick_event.set()
        except Exception:
            self.dropped += 1

    def _on_open(self, wsapp):
        token_list = [{"exchangeType": et, "tokens": toks}
                      for et, toks in self.subscriptions.items() if toks]
        if token_list:
            self.sws.subscribe(self.correlation_id, SmartWebSocketV2.SNAP_QUOTE, token_list)
        self._opened_ns = time.perf_counter_ns()
        self.connected.set()

    def _on_error(self, wsapp, error):
        self.log(f"[feed] websocket error: {error}")

    def _on_close(self, wsapp, *a):
        self.connected.clear()
        self._dead.set()
        self.log("[feed] websocket closed")

    def _new_socket(self, gen):
        # max_retry_attempt=0: the library must not reconnect from inside its own
        # error callback (a nested run_forever); _run below does it instead.
        sws = SmartWebSocketV2(
            self.session.jwt, self.session.creds.api_key,
            self.session.creds.client_id, self.session.feed_token,
            max_retry_attempt=0)

        def live(fn):
            # An abandoned socket that wakes up later must not touch the store
            # or flip the connection state of the one that replaced it.
            return lambda *a: fn(*a) if gen == self._gen else None
        sws.on_data = live(self._on_data)
        sws.on_open = live(self._on_open)
        sws.on_error = live(self._on_error)
        sws.on_close = live(self._on_close)
        return sws

    def _connect(self, sws, gen, dead):
        try:
            sws.connect()                      # returns only when the socket closes
        except Exception as e:
            if gen == self._gen:
                self.log(f"[feed] connect failed: {redact(e)}")
        dead.set()                             # this generation's event only

    @staticmethod
    def _close_async(sws):
        """close_connection() can block until TCP gives up when the link died
        mid-frame (review F-H1), so it never runs on a thread anyone waits on."""
        if sws is None:
            return

        def run():
            try:
                sws.close_connection()
            except Exception:
                pass
        threading.Thread(target=run, name="ws-close", daemon=True).start()

    def _run(self):
        """Connect, wait until the socket dies or is declared dead, reconnect. Forever."""
        while not self._stopping.is_set():
            try:
                self._run_once()
            except Exception as e:             # a dead reconnect loop is a blind engine
                try:
                    self.log(f"[feed] reconnect loop error: {redact(e)!r}")
                except Exception:
                    pass
                self._stopping.wait(5.0)

    def _run_once(self):
        self._gen += 1
        gen = self._gen
        # A generation's own event: a late close of an abandoned socket can no
        # longer end the NEXT socket's life (review F-M1).
        self._dead = dead = threading.Event()
        self.connected.clear()
        self._opened_ns = 0
        self._gen_started = time.time()
        self.sws = sws = self._new_socket(gen)
        threading.Thread(target=self._connect, args=(sws, gen, dead),
                         name=f"ws-{gen}", daemon=True).start()
        while not dead.wait(0.5):
            if self._stopping.is_set():
                break
        self.connected.clear()
        if self._stopping.is_set():
            return
        # A socket that did not really connect, or that opened and never sent a
        # tick, is a failure: the delay grows instead of reconnecting every few
        # seconds all session (a market holiday, review F-L1).
        ticked = bool(self._opened_ns) and self.last_tick_ns > self._opened_ns
        if time.time() - self._gen_started < 5.0 or not self._opened_ns or not ticked:
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0
        self.reconnects += 1
        self.last_reconnect_at = time.time()
        delay = min(2.0 * self._consecutive_failures + 1.0, float(self.max_retry_delay))
        # The ration applies after a login that WORKED. A failed one (DNS down,
        # 5 Oct 2026 12:24) is retried after relogin_retry_sec: rationing it for
        # ten minutes left the feed reconnecting on dead tokens until a restart.
        gap = float(self.relogin_every_sec) if self._relogin_ok else float(self.relogin_retry_sec)
        if self._consecutive_failures and self._consecutive_failures % 3 == 0 \
                and time.time() - self._last_relogin >= gap:
            # Three straight failures usually mean the tokens are dead, not the
            # network. A fresh login renews jwt and feed token together - but
            # at most once per relogin_every_sec, or a quiet holiday logs in
            # every minute.
            self._last_relogin = time.time()
            try:
                self.session.login()
                self._relogin_ok = True
                self.log("[feed] re-login ok; reconnecting with fresh tokens")
            except Exception as e:
                self._relogin_ok = False
                self.log(f"[feed] re-login failed: {type(e).__name__}; retry in "
                         f"{self.relogin_retry_sec:.0f}s")
        self.log(f"[feed] reconnecting in {delay:.0f}s (reconnect #{self.reconnects})")
        self._stopping.wait(delay)

    def _declare_dead(self, gen, why):
        if gen != self._gen:
            return
        dead, sws = self._dead, self.sws
        self.stalls += 1
        self.connected.clear()
        dead.set()                             # _run moves on now, whatever close does
        self._close_async(sws)
        self.log(f"[feed] {why}; forcing reconnect (stall #{self.stalls})")

    def _watch(self):
        alarmed = 0                            # one alarm per generation
        while not self._stopping.wait(1.0):
            try:
                alarmed = self._watch_once(alarmed)
            except Exception:                  # the watchdog must outlive any error
                pass

    def _watch_once(self, alarmed):
        gen = self._gen
        if gen == alarmed or self._dead.is_set() or not self.market_open():
            return alarmed
        if not self.connected.is_set():
            # A connect that never opens is as dead as one that went quiet.
            waited = time.time() - self._gen_started
            if self._gen_started and waited > self.connect_timeout:
                self._declare_dead(gen, f"socket not open after {waited:.0f}s")
                return gen
            return alarmed
        # Quiet is measured from the last tick, or from the open if this
        # socket has not ticked yet: a socket that opens and never ticks is
        # caught too, which the old "no tick yet" skip missed.
        heard = max(self.last_tick_ns, self._opened_ns)
        quiet = (time.perf_counter_ns() - heard) / 1e9
        if quiet > self.stale_sec:
            self._declare_dead(gen, f"no ticks for {quiet:.0f}s while market open")
            return gen
        return alarmed

    def start(self, block=False):
        self._stopping.clear()
        if block:
            self._run()
            return self
        self._thread = threading.Thread(target=self._run, name="ws", daemon=True)
        self._thread.start()
        self._watchdog = threading.Thread(target=self._watch, name="ws-watchdog", daemon=True)
        self._watchdog.start()
        return self

    def stop(self):
        self._stopping.set()
        self._dead.set()
        self._close_async(self.sws)            # a stop must not hang on a dead link

    def wait_for_quotes(self, slots, timeout=30.0, need=1):
        """Block until at least ``need`` of the given slots have two-sided quotes."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ready = sum(1 for s in slots
                        if self.store.bid[s] > 0 and self.store.ask[s] > 0)
            if ready >= need:
                return ready
            time.sleep(0.1)
        return sum(1 for s in slots if self.store.bid[s] > 0 and self.store.ask[s] > 0)
