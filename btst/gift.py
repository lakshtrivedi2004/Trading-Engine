"""GIFT Nifty, for information (owner, 26 Oct 2026).

GIFT Nifty is the NIFTY 50 future on NSE IX (GIFT City), traded nearly round
the clock, so at night it shows how the Indian open is likely to go. The desk
reads it from NSE IX's own public site (no login): the near-month contract's
last price, its previous close and the day's change. With a trade open it is
read once at the entry day's close (the baseline) and once at gift_check_at
(01:00) before the next session; the move between the two, for or against the
trade's side (a fall is against a CE, a rise against a PE), is logged, sent to
Telegram and shown on the page. That read decides nothing. A third, at
gift_target_at (09:11) on the next session's date, sets the target: 160
points or more in the trade's favour raise it (owner, 3 Nov 2026 -
BtstStrategy._gift_target in btst/strategy.py), measured from GIFT Nifty's
first price after its evening session opens at 16:35 on the entry day
(owner, 12 Nov 2026). NSE IX trades it 06:30 - 15:40 and 16:35 - 02:45 IST.
"""
import http.cookiejar
import json
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://www.nseix.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch(today, timeout=20):
    """{price, prev_close, day_change, underlying, expiry, high, low, open,
    time} for the near-month GIFT Nifty future (the first expiry on or after
    `today`); open is the day's first price (06:30), time the quote's (IST)."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    head = {"User-Agent": UA, "Accept": "application/json,*/*", "Referer": BASE + "/"}

    def get(path):
        with op.open(urllib.request.Request(BASE + path, headers=head), timeout=timeout) as r:
            return r.read().decode("utf-8")

    get("/")                                            # the site's cookies first
    expiries = json.loads(get("/api/getquotes/expiry?symbol=NIFTY&inst=Ind")).get("data") or []
    days = sorted((datetime.strptime(e, "%d-%b-%Y").date(), e) for e in expiries)
    near = next((e for d, e in days if d >= today), None)
    if not near:
        raise ValueError(f"no GIFT Nifty expiry on or after {today} in {expiries}")
    q = json.loads(get("/api/getquotes?symbol=NIFTY&instrumenttype=Ind&expiry=" + near
                       + "&optiontype=-&strikeprice=-&inst=" + urllib.parse.quote("Index Futures")))
    return _quote(q, near)


class Feed:
    """GIFT Nifty read often (btst/giftrec.py, every few seconds): one opener
    kept with its cookies and the expiry list kept for expiry_ttl seconds, so
    a read is one request; any failure starts afresh on the next read."""

    def __init__(self, timeout=10, expiry_ttl=1800, clock=None):
        import time as _time
        self.timeout, self.expiry_ttl = timeout, expiry_ttl
        self.clock = clock or _time.time
        self.op, self.expiries, self.expiries_at = None, None, 0.0

    def _get(self, path):
        head = {"User-Agent": UA, "Accept": "application/json,*/*", "Referer": BASE + "/"}
        with self.op.open(urllib.request.Request(BASE + path, headers=head), timeout=self.timeout) as r:
            return r.read().decode("utf-8")

    def __call__(self, today):
        try:
            if self.op is None:
                self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                self._get("/")                          # the site's cookies first
                self.expiries = None
            if self.expiries is None or self.clock() - self.expiries_at > self.expiry_ttl:
                self.expiries = json.loads(self._get("/api/getquotes/expiry?symbol=NIFTY&inst=Ind")).get("data") or []
                self.expiries_at = self.clock()
            days = sorted((datetime.strptime(e, "%d-%b-%Y").date(), e) for e in self.expiries)
            near = next((e for d, e in days if d >= today), None)
            if not near:
                self.expiries = None
                raise ValueError(f"no GIFT Nifty expiry on or after {today} in {self.expiries}")
            q = json.loads(self._get("/api/getquotes?symbol=NIFTY&instrumenttype=Ind&expiry=" + near
                                     + "&optiontype=-&strikeprice=-&inst=" + urllib.parse.quote("Index Futures")))
            return _quote(q, near)
        except Exception:
            self.op = None                              # cookies, expiries: all afresh next time
            raise


MC_URL = "https://www.moneycontrol.com/live-index/gift-nifty?symbol=in;gsx"


def moneycontrol(timeout=15):
    """Moneycontrol's GIFT Nifty figures (owner, 20 Nov 2026 - the page's change
    is the one people read): {prev_close, price, change, time} from the page's
    embedded data (__NEXT_DATA__ stockData)."""
    import re
    req = urllib.request.Request(MC_URL, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise ValueError("no data in the Moneycontrol page")
    d = json.loads(m.group(1))["props"]["pageProps"]["consumptionData"]["stockData"]
    prev = _num(d.get("prevclose") or d.get("prev_close"))
    if not prev:
        raise ValueError("no previous close on the Moneycontrol page")
    return {"prev_close": prev, "price": _num(d.get("lastprice") or d.get("current_price")),
            "change": _num(d.get("change") or d.get("net_change")), "time": d.get("lastupdated")}


def _quote(q, near):
    rows = q.get("data") or []
    if not rows:
        raise ValueError(f"no GIFT Nifty quote for {near}")
    r = rows[0]
    price = _num(r.get("LASTPRICE"))
    if not price:
        raise ValueError(f"no last price in the GIFT Nifty quote ({near})")
    try:
        at = datetime.strptime(str(r.get("TIMESTMP")), "%d-%b-%Y %H:%M:%S").isoformat() + "+05:30"
    except ValueError:
        at = None
    return {"price": price, "prev_close": _num(r.get("PREVCLOSE")), "day_change": _num(r.get("DAYCHANGE")),
            "underlying": _num(r.get("UNDERLYINGVAL")), "expiry": near,
            "high": _num(r.get("DAYHIGH")), "low": _num(r.get("DAYLOW")), "open": _num(r.get("DAYOPEN")), "time": at}


def against(side, move):
    """'against', 'with' or 'flat' for a move of GIFT Nifty points and a CE/PE trade."""
    if move is None or abs(move) < 1:
        return "flat"
    down = move < 0
    return "against" if (side == "CE" and down) or (side == "PE" and not down) else "with"
