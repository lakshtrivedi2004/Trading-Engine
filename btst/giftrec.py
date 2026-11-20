"""GIFT Nifty recorded session by session (owner, 20 Nov 2026).

NSE IX trades GIFT Nifty in two sessions (IST): 06:30 - 15:40 and 16:35 -
02:45 (the second ends after midnight and belongs to the day it started).
While a session is on, the near-month future's last price is read every
gift_record_sec (60 s) on a thread of its own - a slow NSE IX never holds up
the trade - and kept in MongoDB (btst_gift), one document a session: the
points since the PREVIOUS session's last price (its "base"), the first, high,
low and last price, and one price a minute for the page's chart.

The base: the last price recorded in the session before (the 15:40 close for
the evening session, the 02:45 close for the morning one - a weekend or a
holiday in between is skipped, the last session recorded counts). No session
recorded before, or a new contract (the month rolled over): NSE IX's previous
close of this contract instead - said so (base_from).

The evening session reads the contract of the next day (as the desk's GIFT
target does), so on GIFT's expiry day the evening is already the next month,
and the morning after reads the same contract.
"""
import threading
import time as _time
from datetime import datetime, time, timedelta

from arb.instruments import IST
from btst.logbook import fmt

SESSIONS = (("06:30", "15:40"), ("16:35", "02:45"))


def _t(hm):
    h, m = (int(x) for x in str(hm).split(":"))
    return time(h, m)


def session_at(now, sessions=SESSIONS):
    """(no, start, end) of the GIFT session `now` (IST, aware) is in, else None."""
    for no, (a, b) in enumerate(sessions, 1):
        for d in (now.date(), now.date() - timedelta(days=1)):
            start = datetime.combine(d, _t(a), IST)
            end = datetime.combine(d + timedelta(days=1 if _t(b) <= _t(a) else 0), _t(b), IST)
            if start <= now < end:
                return no, start, end
    return None


def next_session(now, sessions=SESSIONS):
    """(no, start) of the next session to open after `now`."""
    best = None
    for no, (a, _) in enumerate(sessions, 1):
        for k in range(0, 3):
            start = datetime.combine(now.date() + timedelta(days=k), _t(a), IST)
            if start > now and (best is None or start < best[1]):
                best = (no, start)
    return best


def session_id(no, start):
    return f"{start.date().isoformat()}-S{no}"


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


class GiftRecorder:
    def __init__(self, store, fetch, cfg=None, now=None, log=None, clock=_time.time):
        self.store, self.fetch = store, fetch
        self.c = cfg or (lambda k, d=None: d)
        self.now = now or (lambda: datetime.now(IST))
        self.log = log or (lambda *_: None)
        self.clock = clock
        self.cur = None                         # the session being recorded (its document, no points)
        self.last_try = 0.0
        self.err_said = None                # when a failed read was last logged (once every 10 min)
        self.live = {"status": "starting"}

    def _sessions(self):
        return tuple(tuple(x) for x in (self.c("gift_sessions", None) or SESSIONS))

    def _say(self, text, level="INFO"):
        self.log(fmt(level, "gift", text))

    def run(self, stop):
        """The thread: a pass every few seconds until `stop` is set."""
        while not stop.is_set():
            try:
                self.pass_once()
            except Exception as e:
                self._say(f"GIFT Nifty recorder error: {e!r}", "ERROR")
            stop.wait(5)

    def pass_once(self):
        now = self.now()
        ses = session_at(now, self._sessions())
        if self.cur and (not ses or session_id(ses[0], ses[1]) != self.cur["session_id"]):
            self._end(self.cur)
        if not ses:
            if self.live.get("status") not in ("closed",):
                nxt = next_session(now, self._sessions())
                self.live = dict(self.live, status="closed",
                                 next_open=_iso(nxt[1]) if nxt else None, next_session=nxt[0] if nxt else None)
            return
        if self.clock() - self.last_try < float(self.c("gift_record_sec", 60)):
            return
        self.last_try = self.clock()
        no, start, end = ses
        sid = session_id(no, start)
        day = start.date() + timedelta(days=1 if end.date() > start.date() else 0)   # the evening: tomorrow's contract
        try:
            q = self.fetch(day)
        except Exception as e:
            if self.err_said is None or self.clock() - self.err_said > 600:
                self.err_said = self.clock()
                self._say(f"GIFT Nifty not read for session {no} ({e!r}) - trying again every "
                          f"{float(self.c('gift_record_sec', 60)):g} s", "WARN")
            self.live = dict(self.live, status="error", error=repr(e)[:160], at=_iso(now))
            return
        qt = None
        try:
            qt = datetime.fromisoformat(q["time"]) if q.get("time") else None
        except ValueError:
            pass
        if qt and not (start - timedelta(minutes=5) <= qt <= end + timedelta(minutes=5)):
            self.live = dict(self.live, status="no_trading", quote_time=q.get("time"), at=_iso(now),
                             session=no, start=_iso(start), end=_iso(end))
            return                              # a quote from before the session: not trading (a holiday)
        self.err_said = None
        doc = self.cur if self.cur and self.cur["session_id"] == sid else self._open(sid, no, start, end, q)
        px = float(q["price"])
        when = qt or now
        minute = int(when.timestamp() * 1000) // 60000 * 60000
        upd = {"last": px, "last_at": _iso(when), "high": max(doc.get("high") or px, px),
               "low": min(doc.get("low") or px, px), "change": round(px - doc["base"], 2),
               "n": int(doc.get("n") or 0) + (1 if minute != doc.get("last_minute") else 0),
               "last_minute": minute, "expiry": q.get("expiry")}
        point = [minute, px] if minute != doc.get("last_minute") else None
        self.store.gift_point(sid, upd, point)
        doc.update(upd)
        self.cur = doc
        self.live = {"status": "recording", "session": no, "session_id": sid, "start": _iso(start), "end": _iso(end),
                     "base": doc["base"], "base_at": doc.get("base_at"), "base_from": doc.get("base_from"),
                     "first": doc.get("first"), "last": px, "at": _iso(when), "change": upd["change"],
                     "high": upd["high"], "low": upd["low"], "expiry": q.get("expiry"), "points": upd["n"]}

    def _open(self, sid, no, start, end, q):
        """The session's document: the one saved (a restart), else a new one with its base."""
        old = self.store.gift_session(sid)
        if old:
            old.pop("points", None)
            self._say(f"GIFT Nifty session {no} ({sid}) recording again after a restart - base {old['base']:,.2f}")
            return old
        px = float(q["price"])
        prev = self.store.gift_session_before(_iso(start))
        if prev and prev.get("last") and prev.get("expiry") == q.get("expiry"):
            base, base_at = float(prev["last"]), prev.get("last_at")
            base_from = f"the last price of session {prev.get('session')} ({prev.get('session_id')})"
        elif q.get("prev_close"):
            base, base_at = float(q["prev_close"]), None
            base_from = ("NSE IX's previous close (a new contract: " + str(q.get("expiry")) + ")" if prev
                         else "NSE IX's previous close (no session recorded before)")
        else:
            base, base_at, base_from = px, _iso(self.now()), "its first price (nothing before it)"
        doc = {"session_id": sid, "day": start.date().isoformat(), "session": no, "start": _iso(start),
               "end": _iso(end), "expiry": q.get("expiry"), "base": base, "base_at": base_at, "base_from": base_from,
               "first": px, "first_at": q.get("time") or _iso(self.now()), "last": px, "high": px, "low": px,
               "change": round(px - base, 2), "n": 0, "last_minute": None, "points": [], "closed": False}
        self.store.save_gift_session(doc)
        self._say(f"GIFT Nifty session {no} open ({start:%H:%M} - {end:%H:%M}): first {px:,.2f}, base {base:,.2f} "
                  f"({base_from}) - recorded every {float(self.c('gift_record_sec', 60)):g} s")
        doc = dict(doc)
        doc.pop("points")
        return doc

    def _end(self, doc):
        self.store.gift_point(doc["session_id"], {"closed": True}, None)
        self._say(f"GIFT Nifty session {doc['session']} ({doc['session_id']}) ended: last {doc['last']:,.2f}, "
                  f"{doc['change']:+,.2f} points since {doc['base']:,.2f}, {doc.get('n', 0)} minute(s) saved")
        self.cur = None


def start(recorder, stop):
    t = threading.Thread(target=recorder.run, args=(stop,), daemon=True, name="gift-recorder")
    t.start()
    return t
