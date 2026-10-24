"""Indian market trading holidays, fetched (owner, 24 Oct 2026).

config/holidays.json is a fixed list (2026, from NSE's circular); a new year's
list has to be added by hand. So the desk also reads Upstox's public market
holiday API every day (no login; NSE's own API refuses this server, HTTP 403)
and adds every trading holiday on which NFO or BFO is closed. The answer is
cached (.cache/holidays_upstox.json): a failed fetch falls back to the last
one, then to the fixed list. Weekends are never trading days anyway.
"""
import json
import os
import urllib.request

from arb.instruments import add_holidays, holidays, now_ist
from btst.logbook import fmt

URL = "https://api.upstox.com/v2/market/holidays"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "holidays_upstox.json")


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse(answer):
    """The F&O trading holidays in an Upstox answer, as 'YYYY-MM-DD' strings."""
    out = []
    for x in (answer or {}).get("data") or []:
        closed = set(x.get("closed_exchanges") or [])
        if x.get("holiday_type") == "TRADING_HOLIDAY" and closed & {"NFO", "BFO"} and x.get("date"):
            out.append(str(x["date"])[:10])
    return sorted(set(out))


class Holidays:
    def __init__(self, log=print, fetch=_get, cache=CACHE, now=now_ist):
        self.log, self.fetch, self.cache, self.now = log, fetch, cache, now
        self.day = None
        self.source, self.fetched, self.error = "config/holidays.json", [], None

    def refresh(self, force=False):
        """Once a day (or forced): fetch, cache, add to the desk's holidays."""
        today = self.now().date()
        if self.day == today and not force:
            return
        self.day = today
        try:
            days = parse(self.fetch(URL))
            if not days:
                raise ValueError("no trading holidays in the answer")
            os.makedirs(os.path.dirname(self.cache), exist_ok=True)
            with open(self.cache, "w") as fh:
                json.dump({"fetched_at": self.now().isoformat(timespec="seconds"), "source": URL, "holidays": days},
                          fh, indent=1)
            self.source, self.fetched, self.error = "Upstox market holidays (today)", days, None
        except Exception as e:
            self.error = repr(e)[:160]
            try:
                with open(self.cache) as fh:
                    c = json.load(fh)
                days = list(c.get("holidays") or [])
                self.source, self.fetched = f"Upstox, cached {c.get('fetched_at', '?')}", days
                self.log(fmt("WARN", "holidays", f"holiday fetch failed ({self.error}) - using the cached list "
                                                 f"({len(days)} days)"))
            except (OSError, ValueError):
                days = []
                self.log(fmt("WARN", "holidays", f"holiday fetch failed ({self.error}) and no cache - "
                                                 f"config/holidays.json only"))
        add_holidays(days)
        year = str(today.year)
        mine = sorted(d for d in holidays() if d.startswith(year))
        nxt = [d for d in sorted(holidays()) if d >= today.isoformat()][:3]
        self.log(fmt("INFO" if mine else "WARN", "holidays",
                     f"{len(mine)} trading holidays known for {year} ({self.source}); next: {', '.join(nxt) or 'none'}"
                     + ("" if mine else " - none known for this year: every weekday would look like a trading day")))

    def state(self):
        today = self.now().date().isoformat()
        return {"source": self.source, "error": self.error,
                "next": [d for d in sorted(holidays()) if d >= today][:3],
                "known_this_year": sum(1 for d in holidays() if d.startswith(today[:4]))}
