"""The noise-area breakout's sigma profile, fetched once a trading day per index.

The live CandleStore holds ~5 sessions (lookback_days 7, max_rows 3000) and
every live playbook reads it, so it is not touched. The rule's sigma(t) needs
14 (research/noise_breakout_result.txt: with 5 or 4 sessions it loses out of
sample), so this fetches ONE_MINUTE history ending yesterday on its own
background thread - one call per index per day, through arb/candles.py
candle_data (the shared rate budget and its retries) - and caches the profile
in .cache/noise_sigma/<date>_<index>.json, so a restart spends no calls.

Never on the hunt() thread: ensure() only starts a worker and returns. A
failure is logged once and retried at most every RETRY_SEC.
"""
import json
import os
import threading
import time
from datetime import date, timedelta

from arb import noise_breakout as nb
from arb.candles import candle_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SigmaHistory:
    RETRY_SEC = 600.0

    def __init__(self, api_fn, underlyings, ndays=14, min_days=10, history_days=28,
                 cache_dir=None, log=print, fetch=None, chunk_days=10):
        """api_fn() -> the SmartConnect (read at fetch time); underlyings:
        {name: Underlying} with spot_exchange / spot_token; fetch(api, params)
        defaults to candle_data."""
        self.api_fn = api_fn
        self.underlyings = underlyings or {}
        self.ndays, self.min_days = int(ndays), int(min_days)
        self.history_days = int(history_days)
        # 6 Oct: one 28-day ONE_MINUTE request for NIFTY timed out three times
        # at the SDK's 7 s read timeout (BANKNIFTY/SENSEX got through). Smaller
        # windows answer faster; the shared budget spaces them.
        self.chunk_days = max(1, int(chunk_days))
        self.cache_dir = cache_dir or os.path.join(ROOT, ".cache", "noise_sigma")
        self.log = log
        self.fetch = fetch or candle_data
        self.ready = {}                 # (day, name) -> profile
        self.errors = {}                # (day, name) -> (text, when)
        self._logged = {}               # (day, name) -> the error text last logged
        self._thread = None
        self._lock = threading.Lock()
        self.calls = 0                  # history requests made (tests, the log)

    # ── called from hunt(): never blocks ───────────────────────────────────
    def get(self, day, name):
        return self.ready.get((day, name))

    def status(self, day, name):
        if (day, name) in self.ready:
            return "ready"
        e = self.errors.get((day, name))
        return f"failed: {e[0]}" if e else "fetching"

    def ensure(self, day, names, now=None):
        """Start the worker for the names not ready (or cached) yet, unless it
        is running or every one of them failed less than RETRY_SEC ago."""
        now = time.time() if now is None else now
        want = [n for n in names if (day, n) not in self.ready
                and now - self.errors.get((day, n), ("", -1e18))[1] >= self.RETRY_SEC]
        if not want:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._work, args=(day, want),
                                            name="noise-sigma", daemon=True)
            self._thread.start()
        return True

    # ── the worker ─────────────────────────────────────────────────────────
    def _path(self, day, name):
        return os.path.join(self.cache_dir, f"{day}_{name}.json")

    def _work(self, day, names):
        for name in names:
            try:
                prof = self._cached(day, name) or self._fetch(day, name)
                self.ready[(day, name)] = prof
                self.errors.pop((day, name), None)
                if self._logged.pop((day, name), None):
                    self.log(f"[shadow] noise_breakout {name}: sigma history recovered")
            except Exception as e:
                text = str(e)[:200] or repr(e)
                self.errors[(day, name)] = (text, time.time())
                if self._logged.get((day, name)) != text:
                    self._logged[(day, name)] = text
                    self.log(f"[shadow] noise_breakout {name}: sigma history unavailable: {text} "
                             f"- retried in {self.RETRY_SEC / 60:.0f} min, no shadow signal until then")

    def _cached(self, day, name):
        path = self._path(day, name)
        try:
            with open(path) as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return None
        if d.get("date") != day or d.get("ndays") != self.ndays or d.get("min_days") != self.min_days:
            return None
        d["sigma"] = {int(k): float(v) for k, v in (d.get("sigma") or {}).items()}
        if not d["sigma"]:
            return None
        d["source"] = "cache"
        return d

    def _fetch(self, day, name):
        u = self.underlyings.get(name)
        if u is None:
            raise RuntimeError("no spot token for this index")
        d0 = date.fromisoformat(day)
        merged = {}
        a, end = d0 - timedelta(days=self.history_days), d0 - timedelta(days=1)
        while a <= end:                       # oldest window first; any failure fails the fetch
            b = min(end, a + timedelta(days=self.chunk_days - 1))
            params = {
                "exchange": u.spot_exchange,
                "symboltoken": u.spot_token,
                "interval": "ONE_MINUTE",
                "fromdate": a.strftime("%Y-%m-%d 09:15"),
                "todate": b.strftime("%Y-%m-%d 15:30"),
            }
            self.calls += 1
            r = self.fetch(self.api_fn(), params)
            if isinstance(r, dict) and r.get("status") is False:
                raise RuntimeError(f"{r.get('errorCode', '')} {r.get('message', '')}".strip())
            for x in (r or {}).get("data") or []:
                if x and len(x) >= 5:
                    merged[str(x[0])] = [str(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])]
            a = b + timedelta(days=1)
        rows = [merged[k] for k in sorted(merged)]
        rows = [r_ for r_ in nb.in_session(rows) if r_[0][:10] < day]     # never today
        days = nb.by_day(rows)
        keys = sorted(days)
        sigma = nb.sigma_profile(days, self.ndays, self.min_days)
        if not sigma:
            raise RuntimeError(f"only {len(keys)} sessions of history (need {self.min_days})")
        used = keys[-self.ndays:]
        prof = {"date": day, "index": name, "ndays": self.ndays, "min_days": self.min_days,
                "days": used, "prev_day": keys[-1], "prev_close": float(days[keys[-1]][-1][4]),
                "sigma": sigma, "fetched_at": time.time(),
                # Each full session's high and low: last_half_hour's ADR(10)
                # needs more sessions than the live candle store holds (~7).
                "ranges": {k: [max(float(r[2]) for r in days[k]), min(float(r[3]) for r in days[k])]
                           for k in keys[-15:]}}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self._path(day, name) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(dict(prof, sigma={str(k): v for k, v in sigma.items()}), fh)
            os.replace(tmp, self._path(day, name))
        except OSError as e:
            self.log(f"[shadow] noise_breakout {name}: sigma cache not written: {e}")
        prof["source"] = "history"
        self.log(f"[shadow] noise_breakout {name}: sigma from {len(used)} sessions "
                 f"{used[0]}..{used[-1]} ready")
        return prof
