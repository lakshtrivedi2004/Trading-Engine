"""NSE's own public market data: movers, pre-open, index members, daily history.

None of this touches the broker API. Angel's historical endpoint is shared with
the live engine and already refuses calls for rate on a busy morning, so the
stock scanner reads the exchange's own pages instead - the same data NSE's
"Top gainers / losers", "Volume gainers" and "Pre-open market" pages show.

These endpoints are unofficial: they are what nseindia.com's own pages call,
not a published API. They can change shape or refuse a client without notice,
so every call here is paced, cached, time-limited, and returns an empty result
rather than raising. A scanner with no data abstains; it never invents a list.
"""
import csv
import io
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from arb.instruments import CACHE_DIR, IST

BASE = "https://www.nseindia.com/api/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
INDEX_CSV = {
    "NIFTY": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "BANKNIFTY": "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv",
}


class NseClient:
    def __init__(self, min_gap_sec=1.0, timeout=15.0, log=lambda *_: None):
        self.min_gap = float(min_gap_sec)
        self.timeout = float(timeout)
        self.log = log
        self._last = 0.0
        self._lock = threading.Lock()
        self.last_error = ""

    def _get(self, url, accept="application/json"):
        with self._lock:
            wait = self.min_gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": accept,
            "Referer": "https://www.nseindia.com/market-data/live-market-indices"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
            self.last_error = ""
            return body
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
            return ""

    def _json(self, path):
        body = self._get(BASE + path)
        try:
            return json.loads(body) if body else {}
        except ValueError:
            self.last_error = "not JSON (NSE refused or changed the page)"
            return {}

    # ── live lists ──────────────────────────────────────────────────────────
    def movers(self, direction="gainers", group="FOSec"):
        """Top gainers or losers. `group` is one of NSE's legends: NIFTY,
        BANKNIFTY, NIFTYNEXT50, FOSec (F&O securities), allSec, ..."""
        key = "gainers" if direction == "gainers" else "loosers"   # NSE's spelling
        d = self._json(f"live-analysis-variations?index={key}")
        return list((d.get(group) or {}).get("data") or [])

    def volume_gainers(self):
        """Stocks trading far above their 1- and 2-week average volume."""
        return list(self._json("live-analysis-volume-gainers").get("data") or [])

    def most_active(self, by="value"):
        return list(self._json(f"live-analysis-most-active-securities?index={by}").get("data") or [])

    def preopen(self, key="FO"):
        """Pre-open (09:00-09:08) indicative prices and buy/sell quantities."""
        return list(self._json(f"market-data-pre-open?key={key}").get("data") or [])

    def ban_list(self):
        """F&O securities in the ban period today (open interest past 95% of the
        market-wide limit): no new positions may be built in their derivatives,
        and they are exactly the names where the tape is being forced."""
        text = self._get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
                         accept="text/csv")
        out = []
        for line in text.splitlines()[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[1]:
                out.append(parts[1])
        return out

    # ── reference data ──────────────────────────────────────────────────────
    def index_members(self, index, max_age_hours=24):
        """Symbols in NIFTY 50 / NIFTY BANK, cached for a day. [] if unknown."""
        url = INDEX_CSV.get(index)
        if not url:
            return []
        path = os.path.join(CACHE_DIR, f"members_{index}.csv")
        text = ""
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600:
            text = open(path).read()
        if not text:
            text = self._get(url, accept="text/csv")
            if text.startswith("Company Name"):
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(path, "w") as fh:
                    fh.write(text)
            elif os.path.exists(path):
                text = open(path).read()     # stale beats nothing
        if not text.startswith("Company Name"):
            return []
        return [r["Symbol"].strip() for r in csv.DictReader(io.StringIO(text)) if r.get("Symbol")]

    def daily(self, symbol, days=370):
        """Daily bars [iso_date, o, h, l, c, volume, turnover, delivery_pct],
        oldest first. NSE serves ~70 sessions a request, so a year is several."""
        end = datetime.now(IST)
        start = end - timedelta(days=days)
        rows = {}
        t = start
        while t < end:
            e = min(t + timedelta(days=95), end)
            q = urllib.parse.urlencode({
                "from": t.strftime("%d-%m-%Y"), "to": e.strftime("%d-%m-%Y"),
                "symbol": symbol, "type": "priceVolumeDeliverable", "series": "ALL"})
            for x in self._json(f"historicalOR/generateSecurityWiseHistoricalData?{q}").get("data") or []:
                if x.get("CH_SERIES") != "EQ":
                    continue
                try:
                    d = datetime.strptime(x["mTIMESTAMP"], "%d-%b-%Y").strftime("%Y-%m-%d")
                    rows[d] = [d, float(x["CH_OPENING_PRICE"]), float(x["CH_TRADE_HIGH_PRICE"]),
                               float(x["CH_TRADE_LOW_PRICE"]), float(x["CH_CLOSING_PRICE"]),
                               float(x["CH_TOT_TRADED_QTY"]), float(x["CH_TOT_TRADED_VAL"]),
                               float(x.get("COP_DELIV_PERC") or 0.0)]
                except (KeyError, TypeError, ValueError):
                    continue
            t = e + timedelta(days=1)
        return [rows[k] for k in sorted(rows)]

    # ── option chains (cookie session) ─────────────────────────────────────
    # The option-chain API answers only a client holding the cookies NSE sets
    # on its own page, so it has its own opener, warmed on that page, kept
    # apart from the plain calls above (which work without cookies).
    OC_PAGE = "https://www.nseindia.com/option-chain"

    def _oc_get(self, url, warm_if_empty=True):
        import http.cookiejar
        if getattr(self, "_oc_opener", None) is None:
            self._oc_jar = http.cookiejar.CookieJar()
            self._oc_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self._oc_jar))
            self._oc_warm()
        for attempt in (0, 1):
            with self._lock:
                wait = self.min_gap - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept": "application/json", "Referer": self.OC_PAGE})
                with self._oc_opener.open(req, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "replace")
                d = json.loads(body) if body.strip() else {}
                if d or not warm_if_empty or attempt:
                    self.last_error = "" if d else "empty answer (cookies refused?)"
                    return d
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
                if attempt:
                    return {}
            self._oc_warm()                     # stale or missing cookies: once more
        return {}

    def _oc_warm(self):
        try:
            req = urllib.request.Request(self.OC_PAGE, headers={"User-Agent": UA, "Accept": "text/html"})
            with self._oc_opener.open(req, timeout=self.timeout) as r:
                r.read()
        except Exception as e:
            self.last_error = f"warm: {type(e).__name__}: {str(e)[:100]}"

    def option_chain(self, symbol, expiry=None):
        """A stock's option chain from NSE: {"underlying", "expiry", "expiries",
        "rows"} where rows are NSE's per-strike dicts ({"strikePrice", "CE":
        {...}, "PE": {...}} with lastPrice, buyPrice1, sellPrice1,
        impliedVolatility, totalTradedVolume). {} when NSE does not answer."""
        info = self._oc_get(BASE + "option-chain-contract-info?" + urllib.parse.urlencode({"symbol": symbol}))
        exps = list(info.get("expiryDates") or [])
        if not exps:
            return {}
        exp = expiry if expiry in exps else exps[0]
        d = self._oc_get(BASE + "option-chain-v3?" + urllib.parse.urlencode(
            {"type": "Equity", "symbol": symbol, "expiry": exp}))
        rec = d.get("records") or {}
        rows = rec.get("data") or (d.get("filtered") or {}).get("data") or []
        if not rows or not rec.get("underlyingValue"):
            return {}
        return {"underlying": float(rec["underlyingValue"]), "expiry": exp, "expiries": exps, "rows": rows}
