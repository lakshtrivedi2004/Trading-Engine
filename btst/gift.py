"""GIFT Nifty, for information (owner, 26 Oct 2026).

GIFT Nifty is the NIFTY 50 future on NSE IX (GIFT City), traded nearly round
the clock, so at night it shows how the Indian open is likely to go. The desk
reads it from NSE IX's own public site (no login): the near-month contract's
last price, its previous close and the day's change. With a trade open it is
read once at the entry day's close (the baseline) and once at gift_check_at
(01:00) before the next session; the move between the two, for or against the
trade's side (a fall is against a CE, a rise against a PE), is logged, sent to
Telegram and shown on the page. It decides nothing: the open's live price
already carries it, and the next-day rules act on that (btst/strategy.py).
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
    """{price, prev_close, day_change, underlying, expiry, high, low} for the
    near-month GIFT Nifty future (the first expiry on or after `today`)."""
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
    rows = q.get("data") or []
    if not rows:
        raise ValueError(f"no GIFT Nifty quote for {near}")
    r = rows[0]
    price = _num(r.get("LASTPRICE"))
    if not price:
        raise ValueError(f"no last price in the GIFT Nifty quote ({near})")
    return {"price": price, "prev_close": _num(r.get("PREVCLOSE")), "day_change": _num(r.get("DAYCHANGE")),
            "underlying": _num(r.get("UNDERLYINGVAL")), "expiry": near,
            "high": _num(r.get("DAYHIGH")), "low": _num(r.get("DAYLOW"))}


def against(side, move):
    """'against', 'with' or 'flat' for a move of GIFT Nifty points and a CE/PE trade."""
    if move is None or abs(move) < 1:
        return "flat"
    down = move < 0
    return "against" if (side == "CE" and down) or (side == "PE" and not down) else "with"
