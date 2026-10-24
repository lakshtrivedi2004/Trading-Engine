"""The BTST desk's Angel One session (owner, 24 Oct 2026).

One login, held by AngelSessions and handed out by Api:
  - a new login on the first call of each day, and again every trading day at
    session_refresh_at (09:14), so the session is fresh when the market opens -
    the websocket (btst/ticks.py) follows: a new session object restarts it;
  - any call Angel answers with an invalid / expired token (AG8001, AG8002,
    AG8003, AB1010 ...) logs in again and is sent once more;
  - a failed login is not retried for retry_sec (60 s): every call in between
    fails fast with the reason, instead of hammering Angel's login.
Every login, refresh and failure is logged ("angel" in the desk's log).
"""
import re
import threading
import time

from arb.instruments import market_phase, now_ist
from btst.logbook import fmt

AUTH_CODES = {"AG8001", "AG8002", "AG8003", "AB8050", "AB8051", "AB1010", "AB1011"}
_AUTH_TEXT = re.compile(r"invalid token|token expired|token is expired|session expired|invalid session|"
                        r"not logged in|unauthori[sz]ed|invalid jwt", re.I)


def auth_failed(answer=None, exc=None):
    """Angel refused the call for its token (not for the order or the data)."""
    if exc is not None:
        text = str(exc)
        return bool(_AUTH_TEXT.search(text)) or any(c in text for c in AUTH_CODES)
    if isinstance(answer, dict) and answer.get("status") is False:
        code = str(answer.get("errorcode") or answer.get("errorCode") or "")
        return code in AUTH_CODES or bool(_AUTH_TEXT.search(str(answer.get("message") or "")))
    return False


def _hm(s):
    p = [int(x) for x in str(s).split(":")]
    return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)


class AngelSessions:
    def __init__(self, login_fn, log=print, now=now_ist, clock=time.time, retry_sec=60.0, min_gap_sec=60.0):
        """login_fn() -> a logged-in arb.feed.AngelSession (.api, .jwt, .feed_token)."""
        self.login_fn, self.log, self.now, self.clock = login_fn, log, now, clock
        self.retry_sec, self.min_gap_sec = float(retry_sec), float(min_gap_sec)
        self.lock = threading.RLock()
        self.angel = self.api = None
        self.day = self.refreshed_on = None
        self.logged_in_at = self.last_error = None
        self.login_ts = self.failed_ts = 0.0
        self.logins = self.failures = self.auth_refreshes = 0

    def _login(self, why):
        if self.failed_ts and self.clock() - self.failed_ts < self.retry_sec:
            wait = self.retry_sec - (self.clock() - self.failed_ts)
            raise RuntimeError(f"Angel login failed {self.clock() - self.failed_ts:.0f}s ago ({self.last_error}); "
                               f"next try in {wait:.0f}s")
        self.log(fmt("INFO", "angel", f"Angel login ({why})"))
        try:
            angel = self.login_fn()
        except Exception as e:
            self.failed_ts, self.last_error = self.clock(), repr(e)[:200]
            self.failures += 1
            self.log(fmt("ERROR", "angel", f"Angel login failed ({why}): {e!r} - no new try for {self.retry_sec:g}s"))
            raise
        now = self.now()
        self.angel, self.api, self.day = angel, angel.api, now.date()
        self.login_ts, self.failed_ts, self.last_error = self.clock(), 0.0, None
        self.logged_in_at = now.isoformat(timespec="seconds")
        self.logins += 1
        self.log(fmt("INFO", "angel", f"Angel login ok ({why}) - the API and the websocket use the new session"))
        return angel

    def ensure(self):
        """The current SmartConnect, logging in first if there is none or the day turned."""
        with self.lock:
            if self.api is None or self.day != self.now().date():
                self._login("first login" if self.api is None else "a new day")
            return self.api

    def session(self):
        """The current AngelSession (for the websocket)."""
        with self.lock:
            self.ensure()
            return self.angel

    def refresh(self, why):
        """Log in again now. True if there is a fresh session to retry with (a
        login in the last min_gap_sec counts - the call that raced it reuses it)."""
        with self.lock:
            if self.api is not None and self.clock() - self.login_ts < self.min_gap_sec:
                return True
            try:
                self._login(why)
                return True
            except Exception:
                return False

    def daily(self, at="09:14"):
        """Once every trading day at `at`: a fresh login before the open."""
        now = self.now()
        if market_phase(now, start="09:15", end="15:45") in ("weekend", "holiday"):
            return False
        t = now.hour * 3600 + now.minute * 60 + now.second
        if t < _hm(at) or self.refreshed_on == now.date():
            return False
        self.refreshed_on = now.date()
        return self.refresh(f"the daily refresh at {at}, before the open")

    def state(self):
        return {"logged_in_at": self.logged_in_at, "logins": self.logins, "failures": self.failures,
                "auth_refreshes": self.auth_refreshes, "last_error": self.last_error,
                "refreshed_on": str(self.refreshed_on) if self.refreshed_on else None}


class Api:
    """The current session's SmartConnect. A call refused for its token logs in
    again and is sent once more (the refused call placed nothing)."""

    def __init__(self, sessions):
        self._s = sessions

    def __getattr__(self, name):
        attr = getattr(self._s.ensure(), name)
        if not callable(attr):
            return attr
        s = self._s

        def call(*a, **k):
            try:
                r = attr(*a, **k)
            except Exception as e:
                if not auth_failed(exc=e):
                    raise
                s.log(fmt("WARN", "angel", f"{name}: token refused ({e!r}) - logging in again"))
                s.auth_refreshes += 1
                if not s.refresh(f"{name} refused the token"):
                    raise
                return getattr(s.ensure(), name)(*a, **k)
            if auth_failed(answer=r):
                s.log(fmt("WARN", "angel", f"{name}: token refused ({r.get('errorcode')} {r.get('message')}) - "
                                           f"logging in again"))
                s.auth_refreshes += 1
                if s.refresh(f"{name} refused the token"):
                    return getattr(s.ensure(), name)(*a, **k)
            return r
        return call
