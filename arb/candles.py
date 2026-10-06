"""Index OHLC candles from Angel's historical API, cached off the hot path.

Fetched on a background thread and served from memory. The trading engine never
waits on this - a chart that fails to load must not delay an order.
"""
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import deque
from datetime import timedelta

import numpy as np

try:
    import fcntl                   # POSIX: the budget is shared across processes
except ImportError:                # pragma: no cover - Windows: per process only
    fcntl = None

from arb.instruments import IST, now_ist, parse_expiry
from arb.volume import VolumeTracker

INTERVALS = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
             "1h": "ONE_HOUR", "1d": "ONE_DAY"}
SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

# Angel's historical endpoint: 3 requests/s, 150/min, 5000/hour per API key
# (Angel's published limits), and in practice it refuses bursts well before
# that ("Access denied because of exceeding access rate"). Every caller - the
# index candles and the stock desk in this process, AND any other engine on the
# same host using the same key (prod and dev) - draws from one shared budget,
# kept in a lock-protected file, with headroom under each published limit.
HIST_GAP_SEC = 1.0                # <= 1/s       (limit 3/s)
HIST_PER_MIN = 100                # <= 100/min   (limit 150/min)
HIST_PER_HOUR = 4000              # <= 4000/hour (limit 5000/hour)
HIST_RETRY_SEC = (5.0, 15.0, 30.0)
_hist_lock = threading.Lock()


def _rate_limited(text):
    # Seen live: "Access denied because of exceeding access rate"; Angel's docs
    # word it "403 Access denied because of exceeding rate limit". Match both.
    t = str(text).lower()
    return "access rate" in t or "rate limit" in t or "access denied" in t


def _budget_path(api):
    # A hash, not the key itself: /tmp is world-readable and an 8-character
    # Angel key would otherwise be the whole file name (review D-B3).
    key = str(getattr(api, "api_key", "") or "default")
    tag = hashlib.sha256(key.encode()).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"angel_hist_{tag}.json")


_local_stamps = []


def _take_slot_local():
    """The same budget within this process only, for when the shared file
    cannot be used (owned by another user, /tmp read-only or full) - pacing
    must degrade, never stop history altogether (review D-B2)."""
    while True:
        with _hist_lock:
            now = time.time()
            _local_stamps[:] = [t for t in _local_stamps if 0.0 <= now - t < 3600.0]
            wait = (_local_stamps[-1] + HIST_GAP_SEC - now) if _local_stamps else 0.0
            for window, cap in ((60.0, HIST_PER_MIN), (3600.0, HIST_PER_HOUR)):
                inside = [t for t in _local_stamps if now - t < window]
                if len(inside) >= cap:
                    wait = max(wait, inside[0] + window - now)
            if wait <= 0:
                _local_stamps.append(now)
                return
        time.sleep(min(wait, 5.0))


def _take_slot(api):
    """Block until one getCandleData call fits the shared budget, and book it.

    The file holds the last hour's request times. It is only read and written
    under an exclusive lock (fcntl, so it holds across processes); the waiting
    happens outside the lock so another engine is never stuck behind a sleeper.
    """
    path = _budget_path(api)
    while True:
        try:
            lf = open(path + ".lock", "a+")
        except OSError:
            return _take_slot_local()
        with _hist_lock, lf:
            if fcntl is not None:
                fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                with open(path) as fh:
                    stamps = [float(t) for t in json.load(fh)]
            except (OSError, ValueError, TypeError):
                stamps = []
            now = time.time()
            # Only the past hour: a stamp from the future (the clock stepped
            # back) would otherwise block every call for the size of the step
            # (review D-B1).
            stamps = [t for t in stamps if 0.0 <= now - t < 3600.0]
            wait = (stamps[-1] + HIST_GAP_SEC - now) if stamps else 0.0
            for window, cap in ((60.0, HIST_PER_MIN), (3600.0, HIST_PER_HOUR)):
                inside = [t for t in stamps if now - t < window]
                if len(inside) >= cap:
                    wait = max(wait, inside[0] + window - now)
            if wait <= 0:
                stamps.append(now)
                try:
                    tmp = f"{path}.{os.getpid()}.tmp"
                    with open(tmp, "w") as fh:
                        json.dump(stamps, fh)
                    os.replace(tmp, path)
                except OSError:
                    pass
                return
        time.sleep(min(wait, 5.0))


def candle_data(api, params):
    """api.getCandleData within the shared budget, retried on a rate refusal.

    A refusal is retried after 5 s, 15 s and 30 s rather than left to the next
    refresh: losing the morning's history means the engine reads structure off
    stale bars until then. Any other error is raised at once."""
    for k in range(len(HIST_RETRY_SEC) + 1):
        _take_slot(api)
        err, r = None, None
        try:
            r = api.getCandleData(params)
        except Exception as e:
            err = e
        refused = (_rate_limited(err) if err is not None else
                   isinstance(r, dict) and r.get("status") is False
                   and _rate_limited(r.get("message", "")))
        if not refused or k == len(HIST_RETRY_SEC):
            if err is not None:
                raise err
            return r
        time.sleep(HIST_RETRY_SEC[k])


class CandleStore:
    def __init__(self, api, underlyings, interval="5m", lookback_days=5,
                 refresh_sec=900.0, cache_dir=None, log=lambda *_: None):
        self.api = api
        self.underlyings = {u.name: u for u in underlyings}
        self.interval = interval
        self.lookback_days = lookback_days
        self.refresh_sec = refresh_sec
        self.log = log
        self.data = {}                 # name -> [[iso, o, h, l, c, v], ...]
        self.fetched_at = {}
        # Angel's historical endpoint is rate limited hard and five-minute bars do
        # not change quickly, so results are cached to disk. A restart then paints
        # the chart immediately from cache instead of spending a request.
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".cache", "candles")
        os.makedirs(self.cache_dir, exist_ok=True)
        self._load_cache()
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        # Live bar, built from the spot index tick. The historical endpoint only
        # returns completed bars, so without this the chart is always one full
        # interval behind the market and the last candle never moves.
        self._store = None
        self._spot_slots = {}
        # An index does not trade, so it has no volume of its own. The front
        # future on it does, and the feed already records the exchange's
        # cumulative day count for it. Differencing that per bucket gives the
        # participation figure the index itself cannot provide. See arb/volume.py.
        self._future_slots = {}
        self._vol = VolumeTracker()
        self.live = {}                 # name -> [iso, o, h, l, c, v] for the open bucket
        self.max_rows = 3000           # ~8 sessions of 1-minute bars
        self.stale_tick_sec = 30.0     # no index tick for this long: build no live bar (sparse index ticks reach ~20 s)
        self._fetch_errors = {}        # name -> last error text, logged once
        self._live_thread = None
        # The option premium chart: one mid-price row per minute for every
        # subscribed contract, built from the same ticks. Our P&L is this
        # chart, not the index one (arb/regime.py:premium_gate).
        self._premium = deque(maxlen=120)
        self._memory_only = set()

    def attach_feed(self, store, spot_slots, future_slots=None, extra_slots=None):
        """Give the store access to the live spot ticks, keyed by index name.

        `future_slots` is optional and keyed the same way. Without it the volume
        column stays zero and everything downstream reports "no opinion" rather
        than inventing one.
        """
        self._store = store
        self._spot_slots = dict(spot_slots)
        self._future_slots = self._pick_futures(future_slots or {})
        # Cash stocks (arb/stockdesk.py): live bars only, their own traded
        # volume, and never written to the cache every minute - two hundred
        # files a minute would cost more than the bars are worth.
        for name, slot in (extra_slots or {}).items():
            self._spot_slots[name] = int(slot)
            self._future_slots[name] = int(slot)
            self._memory_only.add(name)
        if self._future_slots:
            self.log(f"[candles] volume proxied from futures: "
                     + ", ".join(sorted(self._future_slots)))
        else:
            self.log("[candles] no futures volume available; "
                     "volume-based confirmations will abstain")
        return self

    @staticmethod
    def _pick_futures(future_slots):
        """One slot per underlying: the NEAREST expiry future.

        The caller hands us the whole term structure per index, as a list of
        dicts carrying a `slot`. Volume belongs to the front month - that is
        where the participation is - so the nearest expiry is the proxy and the
        back months are ignored. A bare int is accepted too, so a caller that
        already resolved the slot is not forced to wrap it.
        """
        out = {}
        for name, entry in (future_slots or {}).items():
            if isinstance(entry, int):
                out[name] = entry
                continue
            if not entry:
                continue
            # live_futures() documents "nearest first", so entry[0] is already
            # the front month. Re-deriving it from the expiry string anyway
            # means this does not quietly break if that ordering ever changes.
            front = entry[0]
            try:
                front = min(entry, key=lambda f: parse_expiry(f["expiry"]))
            except Exception:
                pass
            slot = front.get("slot") if isinstance(front, dict) else None
            if isinstance(slot, int):
                out[name] = slot
        return out

    def _bucket_iso(self, ts):
        secs = SECONDS.get(self.interval, 300)
        start = int(ts // secs) * secs
        from datetime import datetime
        return datetime.fromtimestamp(start, IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")

    def _sample_live(self):
        st = self._store
        if st is None:
            return
        now = time.time()
        iso = self._bucket_iso(now)
        if hasattr(st, "ask") and (not self._premium or self._premium[-1][0] != iso):
            b, a = st.bid, st.ask
            with self._lock:
                self._premium.append((iso, np.where((b > 0) & (a > 0), (b + a) / 2, st.ltp)))
        recv = getattr(st, "recv_ns", None)
        now_ns = time.perf_counter_ns()
        for name, slot in self._spot_slots.items():
            px = float(st.ltp[slot])
            if px <= 0:
                continue
            # A dead feed must not invent bars. Without this the last price
            # was written as a flat bar every minute: 5 Oct 12:16-12:26 the
            # pace read 0.50 against a true 1.03, and 20 flat bars make ATR 0,
            # which the regime gate used to pass. A minute with no tick is left
            # missing; the next history refresh fills it. Index slots only - a
            # quiet cash stock legitimately goes seconds without a trade.
            if recv is not None and self.stale_tick_sec > 0 and name not in self._memory_only \
                    and (now_ns - int(recv[slot])) / 1e9 > self.stale_tick_sec:
                continue
            bar = self.live.get(name)
            if bar is None or bar[0] != iso:
                # A new interval opened: the previous live bar is now complete and
                # can stand in for history until the next historical refresh.
                if bar is not None:
                    with self._lock:
                        rows = self.data.get(name) or []
                        if rows and rows[-1][0] == bar[0]:
                            # A history row for this same minute was fetched
                            # while the minute was still open, so it is partial;
                            # the bar built from every tick is the complete one
                            # (review D-H1).
                            rows[-1] = bar
                            self.data[name] = rows
                        elif not rows or rows[-1][0] < bar[0]:
                            rows.append(bar)
                            # Bounded: the engine reads at most a few hundred.
                            keep = int(self.max_rows)
                            if len(rows) > keep:
                                del rows[:len(rows) - keep]
                            self.data[name] = rows
                        snap = list(rows)
                    # Persist the bar we just built. Without this, a restart
                    # while the historical API is refusing us throws away the
                    # whole morning's structure and the day trend with it.
                    if name not in self._memory_only:
                        self._save_cache(name, snap)
                self.live[name] = [iso, px, px, px, px, self._volume_delta(name)]
            else:
                if px > bar[2]:
                    bar[2] = px
                if px < bar[3]:
                    bar[3] = px
                bar[4] = px
                bar[5] += self._volume_delta(name)

    def _volume_delta(self, name):
        """Contracts traded in the front future since the last sample."""
        slot = self._future_slots.get(name)
        if slot is None or self._store is None:
            return 0.0
        return self._vol.delta(name, float(self._store.volume[slot]))

    def _run_live(self):
        while not self._stop.is_set():
            try:
                self._sample_live()
            except Exception:
                pass
            self._stop.wait(0.25)

    def _cache_path(self, name):
        return os.path.join(self.cache_dir, f"{name}_{self.interval}.json")

    def _load_cache(self):
        for name in self.underlyings:
            try:
                path = self._cache_path(name)
                if os.path.exists(path):
                    with open(path) as fh:
                        self.data[name] = json.load(fh)
                    # Not marked as fetched: live bars are saved here every
                    # minute, so the file's age says nothing about whether the
                    # history in it is current. One fetch per start is cheap.
            except Exception:
                continue

    def _save_cache(self, name, rows):
        try:
            tmp = self._cache_path(name) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(rows, fh)
            os.replace(tmp, self._cache_path(name))
        except Exception:
            pass

    def _fetch(self, u):
        end = now_ist()
        start = end - timedelta(days=self.lookback_days)
        params = {
            "exchange": u.spot_exchange,
            "symboltoken": u.spot_token,
            "interval": INTERVALS.get(self.interval, "FIVE_MINUTE"),
            "fromdate": start.strftime("%Y-%m-%d 09:15"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        }
        r = candle_data(self.api, params)
        # SmartApi reports API errors as a dict with status False rather than
        # raising, so without this check a refused request is an empty success.
        if isinstance(r, dict) and r.get("status") is False:
            raise RuntimeError(f"{r.get('errorCode', '')} {r.get('message', '')}".strip())
        rows = (r or {}).get("data") or []
        return [[str(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]),
                 float(x[5] or 0)] for x in rows if x and len(x) >= 5]

    def refresh_once(self, force=False):
        for name, u in self.underlyings.items():
            fresh = time.time() - self.fetched_at.get(name, 0)
            if not force and fresh < self.refresh_sec * 0.9:
                continue                      # cache still current; spend nothing
            try:
                rows = self._complete_only(self._fetch(u))
                if rows:
                    with self._lock:
                        # Merge rather than replace: history is authoritative for
                        # the bars it has, and live-built bars newer than its
                        # last row are kept instead of discarded.
                        mine = self.data.get(name) or []
                        tail = [r for r in mine if r[0] > rows[-1][0]]
                        self.data[name] = (rows + tail)[-int(self.max_rows):]
                        self.fetched_at[name] = time.time()
                        snap = list(self.data[name])
                    self._save_cache(name, snap)
                    if self._fetch_errors.pop(name, None):
                        self.log(f"[candles] {name}: historical API recovered")
                else:
                    # An accepted request with no bars is never right for a
                    # multi-day window, and silently ignoring it leaves the
                    # index on live bars alone with nothing in the log to say so.
                    raise RuntimeError(f"empty response - no candles returned for "
                                       f"token {u.spot_token} on {u.spot_exchange}")
            except Exception as e:
                self._report_fetch_error(name, e)
            # Spacing between calls is candle_data's shared budget, not a sleep here.

    def _report_fetch_error(self, name, err):
        """Say it once per outage, and say what it means.

        The engine keeps trading off live-built bars when history is refused,
        so this is a degraded mode rather than a stop - but it has to be
        visible, or a key without historical-data permission reads as a slow
        morning for days.
        """
        text = str(err)
        if self._fetch_errors.get(name) == text:
            return
        self._fetch_errors[name] = text
        hint = ""
        if "AG8004" in text or "Invalid API Key" in text:
            hint = (" - Angel rejected the API key for historical data. Check the"
                    " SmartAPI app this key belongs to has historical data"
                    " enabled. Running on live-built bars until it does.")
        self.log(f"[candles] {name}: history unavailable: {text[:160]}{hint}")

    def _run(self):
        while not self._stop.is_set():
            self.refresh_once()
            # An index whose history was refused is tried again in a minute,
            # not at the next 5-minute refresh; the ones already current are
            # skipped by refresh_once, so this costs only the failed calls.
            self._stop.wait(min(60.0, self.refresh_sec) if self._fetch_errors
                            else self.refresh_sec)

    def start(self):
        self._thread = threading.Thread(target=self._run, name="candles", daemon=True)
        self._thread.start()
        if self._store is not None:
            self._live_thread = threading.Thread(target=self._run_live,
                                                 name="candles-live", daemon=True)
            self._live_thread.start()
        return self

    def stop(self):
        self._stop.set()

    def get(self, name, limit=120):
        with self._lock:
            rows = list(self.data.get(name) or [])
        live = self.live.get(name)
        if live:
            # Replace the bucket the live bar belongs to, or append it. History
            # wins on ties only if it is newer, which it never is for the open bar.
            if rows and rows[-1][0] == live[0]:
                rows[-1] = list(live)
            elif not rows or rows[-1][0] < live[0]:
                rows.append(list(live))
        return rows[-limit:]

    def seed(self, name, rows):
        """Merge fetched 1-minute history under the live bars (a new stock pick).
        History is authoritative for the minutes it covers; newer live bars stay."""
        rows = self._complete_only(rows)
        if not rows:
            return
        with self._lock:
            mine = self.data.get(name) or []
            tail = [r for r in mine if r[0] > rows[-1][0]]
            self.data[name] = (list(rows) + tail)[-int(self.max_rows):]

    def _complete_only(self, rows):
        """History rows whose minute has fully elapsed. Angel's history API
        also returns the minute still in progress; merged as the last row it
        became the "closed" bar the engine decided on, with a wrong close and
        a missing wick, until the next refresh (review D-H1, 2 Oct 2026)."""
        if not rows:
            return rows
        cur = self._bucket_iso(time.time())
        return [r for r in rows if str(r[0]) < cur]

    def premium(self, slot, limit=30):
        """Closing mid of one option per minute, oldest first, live mid last."""
        with self._lock:
            rows = [float(m[slot]) for _, m in list(self._premium)[1:]]
        st = self._store
        if st is not None and st.bid[slot] > 0 and st.ask[slot] > 0:
            rows.append(float(st.bid[slot] + st.ask[slot]) / 2)
        return [x for x in rows if x > 0][-limit:]

    def premium_series(self, slots, limit=60):
        """Per-minute values for several slots on the SAME minutes, oldest first.

        Index slots carry their LTP (they quote no bid/ask), so the spot can be
        passed alongside the options it moves. A minute on which any slot had
        no price is dropped for all of them: a regression of one premium on the
        index is only meaningful on minutes where both were observed.
        Returns {slot: [values]}, every list the same length.
        """
        slots = [int(s) for s in slots]
        with self._lock:
            snaps = [m for _, m in list(self._premium)[1:]]
        rows = [[float(m[s]) for s in slots] for m in snaps]
        st = self._store
        if st is not None:
            live = []
            for s in slots:
                b, a = float(st.bid[s]), float(st.ask[s])
                live.append((b + a) / 2 if b > 0 and a > 0 else float(st.ltp[s]))
            rows.append(live)
        rows = [r for r in rows if all(x > 0 for x in r)][-limit:]
        return {s: [r[i] for r in rows] for i, s in enumerate(slots)}

    def all(self, limit=120):
        with self._lock:
            names = list(self.data)
        return {n: self.get(n, limit) for n in names}
