"""GIFT Nifty recorded session by session (owner, 20 Nov 2026).

NSE IX trades GIFT Nifty in two sessions (IST): 06:30 - 15:40 and 16:35 -
02:45 (the second ends after midnight and belongs to the day it started).
While a session is on, the near-month future's last price is read every
gift_record_sec (1 s - owner, 20 Nov 2026: NSE IX's quote changes up to once a
second at times; ~0.2 s and ~2% of a CPU a read) on a thread of its own - a
slow NSE IX never holds up the trade. Every new price (a new quote time) is
kept:
  - in a local file while the session runs (.cache/gift/<session>.csv,
    "epoch_ms,price"), gzipped into the session's MongoDB document when it
    ends (gz, ticks) and the file removed - a session left open by a stop is
    saved on the next start;
  - in MongoDB (btst_gift), one document a session, written once a minute
    (gift_save_sec, 60): the points since the PREVIOUS session's last price
    (its "base"), first, high, low and last, and one price a minute for the
    page's chart. A document rewritten every second would load the shared Atlas
    cluster for nothing; the page's live figures come from memory (state).

The base: Moneycontrol's previous close (owner, 20 Nov 2026: in step with the
page people read - gift.moneycontrol), read when the session opens and again
every gift_base_sync_sec (600 s); kept only when Moneycontrol's price is within
gift_base_sync_max_diff (25) points of NSE IX's (the same contract). Not read:
the last price recorded in the session before (the 15:40 close for the
evening session, the 02:45 close for the morning one - a weekend or a holiday
in between is skipped). No session recorded before, or a new contract (the
month rolled over): NSE IX's previous close of this contract - said so
(base_from).

The evening session reads the contract of the next day (as the desk's GIFT
target does), so on GIFT's expiry day the evening is already the next month,
and the morning after reads the same contract. A failed read is tried again
after 2, 4, 8 ... at most 60 s (gift_record_sec doubling).
"""
import gzip
import os
import threading
import time as _time
from datetime import datetime, time, timedelta

from arb.instruments import IST
from btst.logbook import fmt

SESSIONS = (("06:30", "15:40"), ("16:35", "02:45"))
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "gift")


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


def unzip(gz):
    """[(epoch_ms, price)] from a session's gzipped prices."""
    out = []
    for line in gzip.decompress(gz).decode().splitlines():
        a, _, b = line.partition(",")
        if a and b:
            out.append((int(a), float(b)))
    return out


class GiftRecorder:
    def __init__(self, store, fetch, cfg=None, now=None, log=None, clock=_time.time, folder=None, base_fn=None):
        self.store, self.fetch, self.base_fn = store, fetch, base_fn
        self.synced_at = None                   # when Moneycontrol's previous close was last read
        self.sync_err_said = None
        self.c = cfg or (lambda k, d=None: d)
        self.now = now or (lambda: datetime.now(IST))
        self.log = log or (lambda *_: None)
        self.clock = clock
        self.dir = folder or self.c("gift_dir", None) or DIR
        self.cur = None                         # the session being recorded (its document, no points)
        self.last_try = 0.0
        self.wait = None                        # after a failed read: seconds to the next try
        self.fails = 0
        self.err_said = None                    # when a failed read was last logged (once every 10 min)
        self.saved_at = 0.0
        self.last_quote = None                  # (quote time, price) last kept - a new one is a new price
        self.swept = False
        self.live = {"status": "starting"}

    def _sessions(self):
        return tuple(tuple(x) for x in (self.c("gift_sessions", None) or SESSIONS))

    def _say(self, text, level="INFO"):
        self.log(fmt(level, "gift", text))

    def _file(self, sid):
        return os.path.join(self.dir, f"{sid}.csv")

    def run(self, stop):
        """The thread: a pass every 0.2 s until `stop` is set (a read every gift_record_sec)."""
        while not stop.is_set():
            try:
                self.pass_once()
            except Exception as e:
                self._say(f"GIFT Nifty recorder error: {e!r}", "ERROR")
            stop.wait(0.2)

    def pass_once(self):
        now = self.now()
        ses = session_at(now, self._sessions())
        if not self.swept:
            self.swept = True
            self._sweep(session_id(ses[0], ses[1]) if ses else None)
        if self.cur and (not ses or session_id(ses[0], ses[1]) != self.cur["session_id"]):
            self._end(self.cur)
        if not ses:
            if self.live.get("status") not in ("closed",):
                nxt = next_session(now, self._sessions())
                self.live = dict(self.live, status="closed",
                                 next_open=_iso(nxt[1]) if nxt else None, next_session=nxt[0] if nxt else None)
            return
        every = self.wait if self.wait is not None else float(self.c("gift_record_sec", 3))
        if self.clock() - self.last_try < every:
            return
        self.last_try = self.clock()
        no, start, end = ses
        sid = session_id(no, start)
        day = start.date() + timedelta(days=1 if end.date() > start.date() else 0)   # the evening: tomorrow's contract
        try:
            q = self.fetch(day)
        except Exception as e:
            self.fails += 1
            self.wait = min(60.0, float(self.c("gift_record_sec", 3)) * 2 ** self.fails)
            if self.err_said is None or self.clock() - self.err_said > 600:
                self.err_said = self.clock()
                self._say(f"GIFT Nifty not read for session {no} ({e!r}) - trying again in {self.wait:g} s, "
                          f"backing off to every 60 s", "WARN")
            self.live = dict(self.live, status="error", error=repr(e)[:160], at=_iso(now))
            return
        if self.fails:
            self._say(f"GIFT Nifty read again after {self.fails} failure(s)")
        self.fails, self.wait, self.err_said = 0, None, None
        qt = None
        try:
            qt = datetime.fromisoformat(q["time"]) if q.get("time") else None
        except ValueError:
            pass
        if qt and not (start - timedelta(minutes=5) <= qt <= end + timedelta(minutes=5)):
            self.live = dict(self.live, status="no_trading", quote_time=q.get("time"), at=_iso(now),
                             session=no, start=_iso(start), end=_iso(end))
            return                              # a quote from before the session: not trading (a holiday)
        doc = self.cur if self.cur and self.cur["session_id"] == sid else self._open(sid, no, start, end, q)
        px = float(q["price"])
        self._sync_base(doc, px)
        when = qt or now
        key = (q.get("time") or _iso(now), px)
        fresh = key != self.last_quote and (q.get("time") is not None or px != doc.get("last"))
        if fresh:
            self.last_quote = key
            os.makedirs(self.dir, exist_ok=True)
            with open(self._file(sid), "a") as fh:
                fh.write(f"{int(when.timestamp() * 1000)},{px:g}\n")
            doc["ticks"] = int(doc.get("ticks") or 0) + 1
        minute = int(when.timestamp() * 1000) // 60000 * 60000
        upd = {"last": px, "last_at": _iso(when), "high": max(doc.get("high") or px, px),
               "low": min(doc.get("low") or px, px), "change": round(px - doc["base"], 2),
               "n": int(doc.get("n") or 0) + (1 if minute != doc.get("last_minute") else 0),
               "last_minute": minute, "expiry": q.get("expiry"), "ticks": int(doc.get("ticks") or 0)}
        point = [minute, px] if minute != doc.get("last_minute") else None
        if point or self.clock() - self.saved_at >= float(self.c("gift_save_sec", 60)):
            self.store.gift_point(sid, upd, point)
            self.saved_at = self.clock()
        doc.update(upd)
        self.cur = doc
        self.live = {"status": "recording", "session": no, "session_id": sid, "start": _iso(start), "end": _iso(end),
                     "base": doc["base"], "base_at": doc.get("base_at"), "base_from": doc.get("base_from"),
                     "first": doc.get("first"), "last": px, "at": _iso(when), "read_at": _iso(now),
                     "change": upd["change"], "high": upd["high"], "low": upd["low"], "expiry": q.get("expiry"),
                     "points": upd["n"], "ticks": upd["ticks"]}

    def _sync_base(self, doc, px):
        """The base set to Moneycontrol's previous close: at the session's first
        read, then every gift_base_sync_sec - when its price agrees with NSE IX's."""
        if self.base_fn is None:
            return
        if self.synced_at is not None and doc.get("synced") and \
                self.clock() - self.synced_at < float(self.c("gift_base_sync_sec", 600)):
            return
        self.synced_at = self.clock()
        try:
            mc = self.base_fn()
        except Exception as e:
            if self.sync_err_said is None or self.clock() - self.sync_err_said > 3600:
                self.sync_err_said = self.clock()
                self._say(f"Moneycontrol not read ({e!r}) - the base stays {doc['base']:,.2f} ({doc.get('base_from')})",
                          "WARN")
            return
        self.sync_err_said = None
        prev, mpx = mc.get("prev_close"), mc.get("price")
        far = float(self.c("gift_base_sync_max_diff", 25))
        if not prev or (mpx and abs(mpx - px) > far):
            self._say(f"Moneycontrol's GIFT Nifty {mpx} is more than {far:g} points from NSE IX's {px:,.2f} - "
                      f"another contract? the base stays {doc['base']:,.2f}", "WARN")
            return
        doc["synced"] = True
        if abs(prev - float(doc["base"])) < 0.005:
            return
        old = doc["base"]
        doc.update(base=prev, base_at=self.now().isoformat(timespec="seconds"),
                   base_from=f"Moneycontrol's previous close (read {self.now():%H:%M})")
        self.store.gift_point(doc["session_id"], {"base": prev, "base_at": doc["base_at"], "base_from": doc["base_from"],
                                                  "change": round(px - prev, 2), "synced": True}, None)
        self._say(f"GIFT Nifty session {doc['session']} base {old:,.2f} -> {prev:,.2f}, Moneycontrol's previous "
                  f"close: {px:,.2f} = {px - prev:+,.2f} points")

    def _open(self, sid, no, start, end, q):
        """The session's document: the one saved (a restart), else a new one with its base."""
        old = self.store.gift_session(sid)
        if old:
            old.pop("points", None)
            old.pop("gz", None)
            try:
                with open(self._file(sid)) as fh:
                    old["ticks"] = sum(1 for _ in fh)
            except OSError:
                pass
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
               "change": round(px - base, 2), "n": 0, "ticks": 0, "last_minute": None, "points": [], "closed": False}
        self.store.save_gift_session(doc)
        self._say(f"GIFT Nifty session {no} open ({start:%H:%M} - {end:%H:%M}): first {px:,.2f}, base {base:,.2f} "
                  f"({base_from}) - read every {float(self.c('gift_record_sec', 3)):g} s, every new price kept")
        doc = dict(doc)
        doc.pop("points")
        return doc

    def _save_file(self, sid):
        """The session's prices gzipped into its document, the local file removed. The count, or None."""
        path = self._file(sid)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return None
        n = raw.count(b"\n")
        self.store.save_gift_ticks(sid, gzip.compress(raw), n)
        os.remove(path)
        return n

    def _end(self, doc):
        n = self._save_file(doc["session_id"])
        final = {k: doc.get(k) for k in ("last", "last_at", "high", "low", "change", "n", "last_minute", "expiry")}
        final.update(closed=True, ticks=n if n is not None else doc.get("ticks"))
        self.store.gift_point(doc["session_id"], final, None)
        self._say(f"GIFT Nifty session {doc['session']} ({doc['session_id']}) ended: last {doc['last']:,.2f}, "
                  f"{doc['change']:+,.2f} points since {doc['base']:,.2f}, {doc.get('n', 0)} minute(s), "
                  f"{n if n is not None else 0} price(s) saved")
        self.cur = None
        self.last_quote = None
        self.synced_at = None                   # the next session reads Moneycontrol at once

    def _sweep(self, current):
        """On start: a session file left by a stop (not the session on now) saved into its document."""
        try:
            names = [f[:-4] for f in os.listdir(self.dir) if f.endswith(".csv")]
        except OSError:
            return
        for sid in names:
            if sid == current:
                continue
            n = self._save_file(sid)
            self.store.gift_point(sid, {"closed": True, "ticks": n}, None)
            self._say(f"GIFT Nifty session {sid}: {n} price(s) left by a stop - saved")


def start(recorder, stop):
    t = threading.Thread(target=recorder.run, args=(stop,), daemon=True, name="gift-recorder")
    t.start()
    return t
