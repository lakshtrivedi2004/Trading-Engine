#!/usr/bin/env python3
"""Correctness suite for stocks in play: the scan, equity costs, and long/short
cash positions through the engine's own exit and close code.

Arithmetic on constructed inputs only: no network, no market.
"""
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb.config import CostModel, IntradayLimits
from arb.costs import leg_cost
from arb.execution import PaperBroker
from arb.intraday_engine import IntradayEngine
from arb.positions import EXIT
from arb.scanner import DailyStat, daily_stat, fo_universe, rank, same_time_volume, volume_done
from arb.smc import BEAR, BULL

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" | {detail}" if detail else ""))


def section(t):
    print(f"\n[{t}]")


CM = CostModel()
L = IntradayLimits()

# ─────────────────────────────────────────────────────────────────────────────
section("1. Equity intraday costs")

rt = leg_cost(1000, 50, "BUY", "EQ", CM) + leg_cost(1000, 50, "SELL", "EQ", CM)
check("Rs 50k round trip ~ Rs 65 (0.13%)", 60 < rt < 70, f"Rs {rt:.2f}")
small = leg_cost(100, 20, "BUY", "EQ", CM)
check("a tiny order pays the Rs 5 brokerage floor, not Rs 20",
      small < 7.0, f"Rs {small:.2f} on Rs 2,000")
check("STT only on the sell side",
      leg_cost(1000, 50, "SELL", "EQ", CM) - leg_cost(1000, 50, "BUY", "EQ", CM) > 10.0)
check("option costs untouched by the equity branch",
      abs(leg_cost(100, 65, "SELL", "OPT", CM) - 35.61) < 0.05)

# ─────────────────────────────────────────────────────────────────────────────
section("2. The scan")

master = [
    {"exch_seg": "NSE", "symbol": "AAA-EQ", "name": "AAA", "token": "11"},
    {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "AAA", "expiry": "27OCT2026", "lotsize": "500"},
    {"exch_seg": "NSE", "symbol": "ZZZ-EQ", "name": "ZZZ", "token": "12"},        # no F&O
    {"exch_seg": "NFO", "instrumenttype": "FUTSTK", "name": "011NSETEST", "expiry": "27NOV2036", "lotsize": "1"},
]
u = fo_universe(master)
check("universe = F&O stocks with a cash token, test symbols dropped",
      list(u) == ["AAA"] and u["AAA"]["eq_token"] == "11", str(u))

rows = [[f"2026-09-{d:02d}", 100, 103, 98, 100 + (d % 3), 1e6, 6e8, 40.0] for d in range(1, 22)]
st = daily_stat("AAA", rows)
check("daily stat: ATR% from true range", st is not None and 0.04 < st.atr_pct < 0.07,
      f"ATR {100 * st.atr_pct:.2f}%")

check("volume curve: a quarter of the day is not a quarter of the volume",
      volume_done(600) > 45 / 375 and volume_done(930) == 1.0, f"{volume_done(600):.3f} by 10:00")
mrows = []
for d in (21, 22, 23):
    for m in range(0, 30):
        mrows.append([f"2026-09-{d}T09:{15 + m if m < 45 else 0:02d}:00+05:30", 1, 1, 1, 1, 100.0])
check("same-time volume averages only the minutes before the pick time",
      abs(same_time_volume(mrows, "2026-09-25", 9 * 60 + 25, sessions=3) - 1000.0) < 1e-6,
      f"{same_time_volume(mrows, '2026-09-25', 9 * 60 + 25, sessions=3)}")


def S(sym, atr, turn=100.0, adv=1e6, pc=500.0):
    return DailyStat(sym, pc, atr, adv, turn)


stats = {"HOT": S("HOT", 0.03), "COLD": S("COLD", 0.016), "THIN": S("THIN", 0.04, turn=5.0),
         "CHEAP": S("CHEAP", 0.05, pc=40.0), "BAN": S("BAN", 0.05), "DOWN": S("DOWN", 0.03)}
live = {"HOT": dict(open=505, high=520, low=503, ltp=518, volume=4e5),
        "COLD": dict(open=500, high=502, low=499, ltp=501, volume=5e4),
        "THIN": dict(open=500, high=530, low=499, ltp=528, volume=9e5),
        "CHEAP": dict(open=40, high=44, low=40, ltp=43, volume=9e5),
        "BAN": dict(open=500, high=530, low=499, ltp=528, volume=9e5),
        "DOWN": dict(open=495, high=496, low=478, ltp=479, volume=4e5)}
r = rank(stats, live, 9 * 60 + 30, L, banned={"BAN"})
names = [x.symbol for x in r]
check("thin, cheap and banned names never make the list",
      not {"THIN", "CHEAP", "BAN"} & set(names), str(names))
check("in play beats quiet: the big mover on big volume ranks first, the quiet one is dropped for low rvol",
      names[:2] in (["HOT", "DOWN"], ["DOWN", "HOT"]) and "COLD" not in names, str(names))
check("a stock falling hard is in play too - both directions",
      "DOWN" in names and [x for x in r if x.symbol == "DOWN"][0].bias == "down")
r2 = rank(stats, live, 9 * 60 + 30, L, banned={"BAN"}, same_time={"HOT": 1e5, "DOWN": 1e6})
hot = [x for x in r2 if x.symbol == "HOT"][0]
check("with same-time history, relative volume is the paper's definition",
      abs(hot.rvol - 4.0) < 1e-9 and "DOWN" not in [x.symbol for x in r2],
      f"HOT rvol {hot.rvol:.2f}; DOWN at 0.4 of its normal is dropped")

# ─────────────────────────────────────────────────────────────────────────────
section("3. Cash positions: long and short through the engine's exits")


class _Store:
    def __init__(self):
        self.ltp, self.bid, self.ask = [0.0], [0.0], [0.0]
        self.slot_of_token = {"11": 0}


class _Cap:
    capital = 10_000.0

    def daily_target(self, pct):
        return self.capital * pct / 100.0


class _Journal:
    def __init__(self):
        self.rows = []

    def record_trade(self, r):
        self.rows.append(r)


class _Candles:
    def get(self, name, limit=120):
        return []


def engine():
    e = IntradayEngine(SimpleNamespace(store=_Store(), spot_slots={}, tick_event=threading.Event()),
                       [], SimpleNamespace(session_start="09:15", session_end="15:15"), CM,
                       IntradayLimits(), PaperBroker(CostModel(slippage_ticks_per_leg=0.0)),
                       _Journal(), _Cap(), _Candles())
    e.book.time_to_close = lambda *_: 200.0
    return e


def open_stock(e, side, px=500.0, risk=4.0, qty=50, target=0.0):
    v = SimpleNamespace(side=side, reason="t", grade="C", reversal=False,
                        names=lambda: [], plays=lambda: ["test"], chain=lambda: [])
    pick = SimpleNamespace(score=1.0, rvol=2.0, atr_pct=0.02, move=0.01, rs=0.0, reasons=[])
    inv = px - risk if side > 0 else px + risk
    tgt = target or (px + 3 * risk if side > 0 else px - 3 * risk)
    e.feed.store.bid[0], e.feed.store.ask[0], e.feed.store.ltp[0] = px - 0.05, px + 0.05, px
    ok = e._enter_stock("AAA", {"eq_symbol": "AAA-EQ", "eq_token": "11"}, 0, v,
                        SimpleNamespace(last_event=""), px, inv, risk, tgt, qty,
                        px + 0.05 if side > 0 else px - 0.05, 20.0, [["t", 0, 0, 0, px, 0]], pick)
    return ok, e.book.snapshot()[0] if ok else None


e = engine()
ok, pos = open_stock(e, BEAR)
check("a short opens with a SELL and a margin of notional / leverage",
      ok and pos.side < 0 and abs(pos.margin - pos.entry * pos.qty / 5.0) < 1e-6,
      f"entry {pos.entry:.2f} margin Rs {pos.margin:,.0f}")
check("deployed capital counts the margin, not the notional",
      abs(e.book.deployed() - pos.margin) < 1e-6)
e.feed.store.bid[0], e.feed.store.ask[0], e.feed.store.ltp[0] = 487.85, 487.95, 487.9
m = e._mark_of(pos)
check("a short is marked mirrored: price down 12 reads as +12 in its own terms",
      abs(m - (2 * pos.entry - 487.95)) < 1e-9 and m > pos.entry, f"mark {m:.2f} vs entry {pos.entry:.2f}")
e.manage()
check("price falling to the target closes the short at a profit",
      not e.book.snapshot() and e.journal.rows and e.journal.rows[-1]["net_inr"] > 0
      and e.journal.rows[-1]["strategy"] == "INTRADAY_EQ_SHORT"
      and e.journal.rows[-1]["legs"][0]["side"] == "BUY",
      f"net Rs {e.journal.rows[-1]['net_inr']:+.2f} ({e.journal.rows[-1]['exit_reason']})"
      if e.journal.rows else "no exit")
rec = e.journal.rows[-1]
want = (pos.entry - rec["exit"]) * pos.qty - (leg_cost(pos.entry, pos.qty, "SELL", "EQ", CM)
                                              + leg_cost(rec["exit"], pos.qty, "BUY", "EQ", CM))
check("short P&L = (sold - bought back) x qty - equity costs both legs",
      abs(rec["net_inr"] - want) < 0.02, f"{rec['net_inr']:.2f} vs {want:.2f}")

e = engine()
ok, pos = open_stock(e, BEAR)
e.feed.store.bid[0], e.feed.store.ask[0], e.feed.store.ltp[0] = 504.95, 505.05, 505.0
e.manage()
check("price rising through the short's stop closes it at a loss",
      not e.book.snapshot() and e.journal.rows[-1]["net_inr"] < 0
      and e.journal.rows[-1]["exit_reason"] == "structure_broken",
      f"{e.journal.rows[-1]['exit_reason']} Rs {e.journal.rows[-1]['net_inr']:+.2f}")

e = engine()
ok, pos = open_stock(e, BULL)
e.feed.store.bid[0], e.feed.store.ask[0], e.feed.store.ltp[0] = 512.0, 512.1, 512.05
e.manage()
check("a long closes at its target with a SELL at the bid",
      e.journal.rows and e.journal.rows[-1]["strategy"] == "INTRADAY_EQ_LONG"
      and e.journal.rows[-1]["legs"][0]["side"] == "SELL" and e.journal.rows[-1]["net_inr"] > 0,
      f"net Rs {e.journal.rows[-1]['net_inr']:+.2f}" if e.journal.rows else "no exit")

e = engine()
ok, pos = open_stock(e, BEAR, target=480.0)
e.feed.store.bid[0], e.feed.store.ask[0], e.feed.store.ltp[0] = 495.4, 495.5, 495.45
e.manage()
check("a short at +1R moves its stop to breakeven plus costs, in mirrored terms",
      e.book.snapshot() and pos.trailed and pos.stop > pos.entry,
      f"stop {pos.stop:.2f} entry {pos.entry:.2f}")

check("stocks are off by default in code (config/intraday.json turns them on)",
      IntradayLimits().stocks_enabled is False)

# ─────────────────────────────────────────────────────────────────────────────
section("4. The desk: whole-day figures after a restart, retried history")

import numpy as np
from arb.stockdesk import StockDesk


class _DeskCandles:
    def __init__(self):
        self.seeded = {}
        self._store = SimpleNamespace(day_open=np.array([500.0]), day_high=np.array([520.0]),
                                      day_low=np.array([495.0]), ltp=np.array([518.0]),
                                      volume=np.array([4e5]))

    def get(self, name, limit=120):
        return []                      # no bars since "restart"

    def seed(self, name, rows):
        self.seeded[name] = rows


class _Api:
    def __init__(self, fail):
        self.fail, self.calls = fail, 0

    def getCandleData(self, params):
        self.calls += 1
        if self.calls <= self.fail:
            return {"status": False, "errorCode": "AB1004", "message": "rate"}
        return {"status": True, "data": [["2026-09-28T09:15:00+05:30", 500, 501, 499, 500, 100]]}


c = _DeskCandles()
desk = StockDesk({"AAA": {"eq_token": "11"}}, {"AAA": 0}, c, IntradayLimits(), api=_Api(fail=2))
got = desk._live()
check("a scan after a mid-day restart reads the exchange's day open/high/low and day volume",
      got.get("AAA") == {"open": 500.0, "high": 520.0, "low": 495.0, "ltp": 518.0, "volume": 4e5},
      str(got))
desk._seed_all(["AAA"], gap_sec=0.0, tries=4, backoff_sec=0.0)
check("history refused for rate is retried, not given up on",
      "AAA" in c.seeded and desk.api.calls == 3,
      f"{desk.api.calls} calls")

# Refused on every try: trade on saved bars now, but ask again at the next
# rescan - saved bars end yesterday, so today's morning would stay a gap.
c = _DeskCandles()
desk = StockDesk({"AAA": {"eq_token": "11"}}, {"AAA": 0}, c, IntradayLimits(), api=_Api(fail=99))
saved = [["2026-09-28T15:29:00+05:30", 500, 501, 499, 500, 100]]
desk._disk_history = lambda sym: saved
desk._seed_all(["AAA"], gap_sec=0.0, tries=2, backoff_sec=0.0)
check("history refused on every try falls back to the saved bars",
      c.seeded.get("AAA") == saved, str(c.seeded))
check("...without counting the pick as seeded, so a rescan asks the API again",
      "AAA" not in desk._seeded and "AAA" in desk._disk_only)
calls = desk.api.calls
desk.api.fail = 0
desk._seed_all(["AAA"], gap_sec=0.0, tries=2, backoff_sec=0.0)
check("...and the next rescan loads today's history once the API answers",
      desk.api.calls == calls + 1 and "AAA" in desk._seeded and "AAA" not in desk._disk_only
      and c.seeded["AAA"] != saved, f"{desk.api.calls - calls} more calls")

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
