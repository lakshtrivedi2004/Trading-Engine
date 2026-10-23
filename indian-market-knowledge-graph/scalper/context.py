"""Market-day context from the repo's research platform (india_market_research):
holidays, F&O expiry weekday, calendar events, plus live India VIX.

Rules (from the event studies + Varsity risk modules in the knowledge graph):
  * holiday                -> don't trade
  * Union Budget / RBI policy / election-result day -> don't trade (gap-and-whipsaw days)
  * NIFTY weekly expiry day -> trade, but halve the per-trade budget (pin/gamma whipsaws)
  * India VIX >= 20         -> halve the per-trade budget; VIX >= 28 -> no new trades
"""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import PROJECT

IMR = PROJECT / "india_market_research"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NO_TRADE_EVENTS = ("budget", "rbi", "election")
VIX_TOKEN = "99926017"


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _db_rows(query: str) -> list[tuple]:
    db = IMR / "data" / "market.db"
    if not db.exists():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            return c.execute(query).fetchall()
    except sqlite3.Error:
        return []


@dataclass
class DayContext:
    day: date
    holiday: str | None = None
    events: list[str] = field(default_factory=list)
    expiry: bool = False
    vix: float | None = None
    vix_prev_close: float | None = None

    @property
    def no_trade_reason(self) -> str | None:
        if self.holiday:
            return f"holiday: {self.holiday}"
        for e in self.events:
            if any(k in e.lower() for k in NO_TRADE_EVENTS):
                return f"event day: {e}"
        if self.vix is not None and self.vix >= 28:
            return f"India VIX {self.vix:.1f} >= 28"
        return None

    @property
    def budget_mult(self) -> float:
        m = 1.0
        if self.expiry:
            m *= 0.5
        if self.vix is not None and self.vix >= 20:
            m *= 0.5
        return m

    def tags(self) -> list[str]:
        out = []
        if self.expiry:
            out.append("NIFTY expiry")
        out += self.events
        if self.holiday:
            out.append(f"holiday: {self.holiday}")
        return out


def load(day: date) -> DayContext:
    ctx = DayContext(day)
    ds = day.isoformat()
    hol = {r[0]: r[1] for r in _db_rows("select date, description from holidays where exchange='NSE'")}
    hol.update({r["date"]: r.get("description", "") for r in _csv_rows(IMR / "config" / "holidays.csv") if r.get("exchange", "NSE") == "NSE"})
    ctx.holiday = hol.get(ds)
    ev = [(r[0], f"{r[1]}: {r[2]}") for r in _db_rows("select date, type, description from events")]
    ev += [(r["date"], f"{r['type']}: {r.get('description', '')}") for r in _csv_rows(IMR / "config" / "events.csv")]
    ctx.events = sorted({e for d, e in ev if d == ds})
    # NIFTY weekly expiry weekday: latest open-ended rule (repo marks it a draft; Tuesday since Sept 2025)
    wd = "Tuesday"
    for r in _db_rows("select weekday, valid_from from expiry_rules where exchange='NSE' and contract='NIFTY' and valid_to is null order by valid_from desc"):
        wd = r[0]
        break
    ctx.expiry = WEEKDAYS[day.weekday()] == wd
    # if the expiry weekday is a holiday NSE moves expiry to the previous trading day
    if not ctx.expiry and day.weekday() < 4:
        nxt = date.fromordinal(day.toordinal() + 1)
        if WEEKDAYS[nxt.weekday()] == wd and nxt.isoformat() in hol:
            ctx.expiry = True
    vix = _db_rows("select close from prices where series='India VIX' and date < '%s' order by date desc limit 1" % ds)
    ctx.vix_prev_close = float(vix[0][0]) if vix else None
    return ctx
