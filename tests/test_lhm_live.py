#!/usr/bin/env python3
"""last_half_hour = "on": the 15:00 momentum entry with real orders (owner,
6 Oct 2026, on research/portfolio_result.txt).

What these prove:

  1. The signal: a 14:59 close lhm_adr_frac x ADR from the previous close, in
     the 4-hour trend's direction; ADR over FULL sessions only.
  2. The gates: flat (no position, no pending entry), not halted, inside the
     daily loss limit, F4, and the 15:00:00-15:00:15 window - one attempt a day.
  3. The order goes through enter() and a real LiveBroker (over a fake Angel):
     exactly one BUY, the contract hunt() would pick under max_risk_pct_per_trade
     with costs inside, the next expiry on expiry day.
  4. The position is managed like research/lab/realistic.py reprice: the index
     stop, the premium stop, the +1R cost-covered breakeven, the square-off -
     no target, no trail, no time stop - and journalled as last_half_hour.
  5. "shadow" still never orders; "off" and unknown values do nothing.

The live config (config/intraday.json) is loaded so the test runs under the
settings the engine trades with; only last_half_hour (and the noise-breakout
shadow, which would start a history fetch) are overridden. No network, no
orders: the broker is arb/execution.py LiveBroker over a fake SmartAPI.
"""
import os
import sys
import threading
import time as _time
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import arb.execution as _X
import arb.intraday_engine as _IE
import arb.positions as _P
import arb.regime as _RG
from arb.config import CostModel, IntradayLimits
from arb.execution import LiveBroker
from arb.instruments import IST
from arb.intraday_engine import IntradayEngine
from arb.positions import EXIT, HOLD, Position
from arb.regime import atr
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


def guarded(label, fn):
    try:
        fn()
    except BaseException as ex:                          # noqa: BLE001
        if isinstance(ex, KeyboardInterrupt):
            raise
        import traceback
        traceback.print_exc()
        check(f"{label} (raised)", False, repr(ex)[:200])


# ── a fake clock for the engine, the position book and the broker ───────────
class Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, d):
        self.t += max(0.0, float(d))

    def __getattr__(self, k):
        return getattr(_time, k)

    def at(self, hh, mm, ss=0, day=1):
        self.t = datetime(2026, 10, day, hh, mm, ss, tzinfo=IST).timestamp()

    def now(self):
        return datetime.fromtimestamp(self.t, IST)


CLK = Clock()
TODAY = "2026-10-01"


# ── candles ─────────────────────────────────────────────────────────────────
def bars(day, n, f, h, start="09:15"):
    t0 = datetime.fromisoformat(f"{day}T{start}:00+05:30")
    out = []
    for i in range(n):
        px = f(i)
        out.append([(t0 + timedelta(minutes=i)).isoformat(), px, px + h, px - h, px, 0.0])
    return out


def history(end=24100.0, h_today=10.0, tail=False):
    """Three full prior sessions climbing 23800 -> 24000 (each 100 points
    high-low: ADR 100, previous close 24000), then today 09:15-14:59 going
    linearly from 24000 to `end`. tail: a cut-off oldest session (15:00-15:29
    only, 10 points wide) as the live store holds when full."""
    out = []
    if tail:
        out += bars("2026-09-25", 30, lambda i: 23700.0, 5.0, start="15:00")
    for d, base in (("2026-09-28", 23800.0), ("2026-09-29", 23870.0), ("2026-09-30", 23940.0)):
        out += bars(d, 375, lambda i, b=base: b + 60.0 * i / 374, 20.0)
    out += bars(TODAY, 345, lambda i: 24000.0 + (end - 24000.0) * i / 344, h_today)
    return out


class Candles:
    interval = "1m"

    def __init__(self, rows):
        self.rows = rows
        c = rows[-1][4]
        self.live = {"NIFTY": [f"{TODAY}T15:00:00+05:30", c, c + 1.0, c - 1.0, c, 0.0]}

    def get(self, name, limit=120):
        out = [list(r) for r in self.rows]
        lv = self.live.get(name)
        if lv and lv[0] > out[-1][0]:
            out.append(list(lv))
        return out[-limit:]


# ── the chain, two expiries ─────────────────────────────────────────────────
KS = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0, 24300.0, 24400.0]
CE = [340.0, 255.0, 180.0, 120.0, 75.0, 44.0, 25.0]
PE = [28.0, 45.0, 78.0, 120.0, 175.0, 245.0, 330.0]


class CV:
    def __init__(self, expiry, tok0):
        n = len(KS)
        self.strikes = np.array(KS)
        self.ce_ask, self.pe_ask = np.array(CE), np.array(PE)
        self.ce_bid, self.pe_bid = self.ce_ask - 1.0, self.pe_ask - 1.0
        self.ce_askq = self.pe_askq = np.full(n, 500.0)
        self.ce_age = self.pe_age = np.full(n, 50.0)
        self.ce_symbol = [f"NIFTY{expiry}{int(k)}CE" for k in KS]
        self.pe_symbol = [f"NIFTY{expiry}{int(k)}PE" for k in KS]
        self.ce_token = [str(tok0 + i) for i in range(n)]
        self.pe_token = [str(tok0 + 100 + i) for i in range(n)]
        self.lot_size, self.expiry = 65, expiry
        self.underlying, self.segment = "NIFTY", "NFO"


class Binding:
    underlying = "NIFTY"
    strikes = KS

    def __init__(self, expiry, T, tok0):
        self.expiry, self.T, self.tok0 = expiry, T, tok0

    def view(self, *a):
        return CV(self.expiry, self.tok0)


class Store:
    """Slot 0 is the NIFTY spot; every option token has its own slot."""
    def __init__(self, bindings, spot):
        self.slot_of_token, self.ltp, self.bid, self.ask = {}, [spot], [0.0], [0.0]
        for b in bindings:
            cv = b.view()
            for toks, asks in ((cv.ce_token, CE), (cv.pe_token, PE)):
                for t, a in zip(toks, asks):
                    self.slot_of_token[t] = len(self.ltp)
                    self.ltp.append(a)
                    self.bid.append(a - 1.0)
                    self.ask.append(a)


# ── a fake SmartAPI: every order completes at once, at the price the test sets
class Angel:
    def __init__(self, store, fill=True):
        self.store, self.fill = store, fill
        self.params, self.orders = [], {}

    def _px(self, p):
        slot = self.store.slot_of_token[p["symboltoken"]]
        return self.store.ask[slot] if p["transactiontype"] == "BUY" else self.store.bid[slot]

    def placeOrderFullResponse(self, params):
        self.params.append(dict(params))
        n = str(len(self.params))
        q = int(params["quantity"])
        ok = self.fill if params["transactiontype"] == "BUY" else True
        self.orders["u" + n] = {"id": n, "status": "complete" if ok else "cancelled",
                                "filled": q if ok else 0, "avg": self._px(params) if ok else 0.0,
                                "params": dict(params)}
        return {"status": True, "data": {"orderid": n, "uniqueorderid": "u" + n}}

    def _row(self, o):
        p = o["params"]
        return {"orderid": o["id"], "uniqueorderid": "u" + o["id"], "status": o["status"],
                "orderstatus": o["status"], "filledshares": str(o["filled"]),
                "averageprice": str(o["avg"]), "quantity": p.get("quantity"),
                "unfilledshares": str(int(p.get("quantity") or 0) - o["filled"]),
                "variety": p.get("variety"), "tradingsymbol": p.get("tradingsymbol"),
                "transactiontype": p.get("transactiontype"), "ordertag": "",
                "ordertype": p.get("ordertype"), "price": p.get("price"),
                "triggerprice": "0", "text": ""}

    def individual_order_details(self, uid):
        o = self.orders.get(uid)
        return {"status": True, "data": self._row(o)} if o else None

    def orderBook(self):
        return {"status": True, "data": [self._row(o) for o in self.orders.values()]}

    def cancelOrder(self, oid, variety):
        return {"status": True, "data": {"orderid": oid}}

    def position(self):
        net = {}
        for o in self.orders.values():
            s = o["params"]["tradingsymbol"]
            net[s] = net.get(s, 0) + o["filled"] * (1 if o["params"]["transactiontype"] == "BUY" else -1)
        return {"status": True, "data": [{"tradingsymbol": s, "netqty": str(q)} for s, q in net.items()]}

    def buys(self):
        return [p for p in self.params if p["transactiontype"] == "BUY"]

    def sells(self):
        return [p for p in self.params if p["transactiontype"] == "SELL"]


class Cap:
    capital = 13466.0

    def __init__(self):
        self.recs = []

    def daily_target(self, pct):
        return self.capital * pct / 100.0

    def record(self, net, count=True):
        self.recs.append((net, count))


class Journal:
    dir = None

    def __init__(self):
        self.rows = []

    def record_trade(self, r):
        self.rows.append(r)


def limits(mode="on"):
    L = IntradayLimits.load()                    # the live settings
    L.last_half_hour = mode
    L.noise_breakout = "off"                     # its shadow would start a history fetch
    return L


def make(mode="on", rows=None, bindings=None, fill=True, spot=None):
    rows = rows or history()
    bindings = bindings or [Binding("06OCT2026", 0.02, 1000)]
    spot = rows[-1][4] if spot is None else spot
    store = Store(bindings, spot)
    api = Angel(store, fill=fill)
    b = LiveBroker(api, CostModel.load(), product="INTRADAY", duration="IOC",
                   confirm_fills=True, confirm_timeout=6.0)
    b.exit_safety_fixes = True
    b.order_rate_per_sec = 8.0
    lg = []
    feed = SimpleNamespace(store=store, spot_slots={"NIFTY": 0}, tick_event=threading.Event())
    e = IntradayEngine(feed, bindings, SimpleNamespace(session_start="09:15", session_end="15:15"),
                       CostModel.load(), limits(mode), b, Journal(), Cap(), Candles(rows),
                       log=lg.append)
    return e, api, lg


def entered(**kw):
    CLK.at(15, 0, 3)
    e, api, lg = make(**kw)
    e.hunt()
    pos = e.book.snapshot()[0] if e.book.count() else None
    return e, api, lg, pos


def set_bid(e, pos, bid):
    e.feed.store.bid[e.feed.store.slot_of_token[str(pos.token)]] = float(bid)


_saved = (_IE.time, _X.time, _P.time, _IE.now_ist, _P.now_ist, _RG.now_ist, _X.now_ist)
_IE.time = _X.time = _P.time = CLK
_IE.now_ist = _P.now_ist = _RG.now_ist = _X.now_ist = CLK.now


# ═════════════════════════════════════════════════════════════════════════════
def t_signal():
    section("1. The signal")
    e, api, lg = make()
    rows = history()
    sig = e._lhm_signal("NIFTY", rows)
    a = atr(rows[:-1], period=e.L.atr_period)
    check("a 14:59 close 1.0 ADR above the previous close is a CE signal",
          sig is not None and sig[0] == BULL and abs(sig[3] - 100.0) < 1e-6 and abs(sig[4] - 100.0) < 1e-6,
          str(sig))
    check("...stop = close - lhm_stop_atr x ATR of the 20 bars BEFORE the decision bar "
          "(screen.prep atr[i])",
          sig is not None and abs(sig[2] - (24100.0 - e.L.lhm_stop_atr * a)) < 1e-6,
          f"stop {sig[2] if sig else None:.2f}, ATR {a:.2f}")
    check("a 0.3 ADR move is no signal", e._lhm_signal("NIFTY", history(end=24030.0)) is None)
    check("a 0.5 ADR move is (the threshold is >=)",
          e._lhm_signal("NIFTY", history(end=24050.0)) is not None)
    dn = e._lhm_signal("NIFTY", history(end=23900.0))
    check("a 1.0 ADR fall is a PE signal with the stop above", dn is not None and dn[0] == BEAR
          and dn[2] > 23900.0)
    t = e._lhm_signal("NIFTY", history(tail=True))
    check("the store's cut-off oldest session (a 15:00-15:29 tail) does not count toward ADR",
          t is not None and abs(t[4] - 100.0) < 1e-6 and e._lhm_adr_n == 3, f"ADR {t[4] if t else None}")
    r = e._lhm_signal("NIFTY", history(), ranges={"2026-09-24": [24300.0, 24000.0],
                                                  "2026-09-25": [24300.0, 24000.0]})
    check("full-session ranges from the history fetch complete the ADR window",
          r is not None and abs(r[4] - (3 * 100.0 + 2 * 300.0) / 5) < 1e-6 and e._lhm_adr_n == 5,
          f"ADR {r[4] if r else None}")
    r2 = e._lhm_signal("NIFTY", history(), ranges={k: [24400.0, 24000.0] for k in
                                                   ("2026-09-22", "2026-09-23", "2026-09-24",
                                                    "2026-09-25", "2026-09-26")})
    check("...and with a 400-point ADR the same 100-point move is no signal", r2 is None)


def t_once():
    section("2. One order at 15:00:0x, through the live order path, none outside the window")
    for hh, mm, ss, label in ((14, 59, 50, "14:59:50"), (15, 0, 20, "15:00:20 (window is 15 s)"),
                              (15, 1, 3, "15:01:03")):
        CLK.at(hh, mm, ss)
        e, api, lg = make()
        e.hunt()
        check(f"no order at {label}", not api.params and e.book.count() == 0)
    # An engine started late: the decision passes with no order, said once.
    CLK.at(15, 0, 30)
    e, api, lg = make()
    e.hunt()
    e.hunt()
    check("an engine first passing at 15:00:30 places nothing and says so once",
          not api.params and sum("not taken today" in x for x in lg) == 1, str(lg[-1:]))
    e, api, lg, pos = entered()
    check("at 15:00:03 exactly one BUY goes to the broker", len(api.buys()) == 1 and len(api.params) == 1,
          str([p["tradingsymbol"] for p in api.params]))
    p = api.params[0] if api.params else {}
    check("...an IOC marketable limit, INTRADAY - the live entry path's order",
          p.get("duration") == "IOC" and p.get("producttype") == "INTRADAY" and p.get("ordertype") == "LIMIT",
          str(p))
    check("...and the position is on the book", pos is not None and pos.qty == 65)
    for ss in (5, 9, 14):
        CLK.at(15, 0, ss)
        e.hunt()
    check("second, third, fourth pass inside the window: no more orders", len(api.params) == 1)
    e.book.remove(pos.trade_id)                  # even flat again, not a second time today
    CLK.at(15, 0, 12)
    e.hunt()
    check("...even after the book is flat again", len(api.params) == 1)
    # The normal hunt closes no_new_trades_minutes before the session end (the
    # owner moved it from 15 to 5 on 6 Oct 2026: closed from 15:10); only the
    # 15:00 last_half_hour entry is exempt from that gate.
    _close_m = 15 - int(float(IntradayLimits.load().no_new_trades_minutes)) + 1
    CLK.at(15, _close_m, 3)
    e0, _, _ = make(mode="off")
    check(f"the normal hunt is closed at 15:{_close_m:02d} (too_close_to_squareoff not exempted for it)",
          e0._gates() == "too_close_to_squareoff", e0._gates())

    # An IOC that did not fill: one attempt, never chased.
    CLK.at(15, 0, 3)
    e, api, lg = make(fill=False)
    e.hunt()
    CLK.at(15, 0, 6)
    e.hunt()
    check("an IOC entry that did not fill is not retried", len(api.params) == 1 and e.book.count() == 0,
          f"{len(api.params)} orders")


def t_gates():
    section("3. Gates: flat, not halted, inside the daily loss limit, 4h trend, F4")
    CLK.at(15, 0, 3)
    e, api, lg = make(rows=history(end=23900.0))
    e.hunt()
    check("a PE signal against the 4-hour (up) trend places nothing",
          not api.params and any("4-hour" in x for x in lg), str(lg[-1:]))
    e, api, lg = make(rows=history(end=24030.0))
    e.hunt()
    check("no signal (0.3 ADR) places nothing", not api.params and any("no signal" in x for x in lg))

    e, api, lg = make()
    e.book.add(Position(trade_id=99, symbol="X", token="1000", strike=0.0, opt="CE", side=1, qty=65,
                        entry=10.0, stop=5.0, target=20.0, risk_points=5.0, index_invalidation=0.0,
                        opened_at=CLK.t, underlying="NIFTY"))
    e.L.max_open_positions = 1
    e.hunt()
    check("a position already open: no order", len(api.params) == 0
          and any("position_open" in x for x in lg), str(lg[-1:]))
    e, api, lg = make()
    e._pending_entries = [{"symbol": "X"}]
    e._poll_entries = lambda: None
    e.hunt()
    check("an entry order still pending: no order", not api.params and any("entry_pending" in x for x in lg))
    e, api, lg = make()
    e.halted, e.halt_reason = True, "manual stop (dashboard)"
    e.hunt()
    check("halted: no order", not api.params and any("halted" in x for x in lg))
    e, api, lg = make()
    e.realised_pnl = -e.capital.capital * e.L.max_daily_loss_pct / 100.0
    e.hunt()
    check("at the daily loss limit (not yet flagged halted): no order",
          not api.params and any("daily loss limit" in x for x in lg))
    e, api, lg = make()
    e.trades_today = e.L.max_trades_per_day
    e.hunt()
    check("max_trades_per_day reached: no order", not api.params)
    e, api, lg = make()
    e.last_loss_ts = CLK.t - 10.0
    e.hunt()
    check("inside cooldown_after_loss: no order", not api.params and any("cooldown_after_loss" in x for x in lg))
    e, api, lg = make()
    e._loss_side, e._loss_side_ts = BULL, CLK.t - 600.0
    e.hunt()
    check("F4: a CE loss 10 minutes ago blocks a CE", not api.params
          and any("same side" in x for x in lg), str(lg[-1:]))
    # A gate that clears inside the window: retried, then taken.
    e, api, lg = make()
    e.halted = True
    e.hunt()
    e.halted = False
    CLK.at(15, 0, 6)
    e.hunt()
    check("a gate that clears inside the window: the entry is taken then", len(api.params) == 1)


def t_contract():
    section("4. The contract: hunt()'s choice under the per-trade cap, costs inside")
    e, api, lg, pos = entered()
    L = e.L
    cap = e.capital.capital * L.max_risk_pct_per_trade / 100.0
    rows = history()
    a = atr(rows[:-1], period=L.atr_period)
    idx_risk = L.lhm_stop_atr * a
    cv = CV("06OCT2026", 1000)
    lot = 65
    ok = []
    for i, k in enumerate(KS):
        lo, hi = max(0, i - 1), min(len(KS) - 1, i + 1)
        d = abs(CE[hi] - CE[lo]) / (KS[hi] - KS[lo])
        sp = 1.0
        ceiling = (cap / lot - e._extra_risk_points(CE[i], sp, lot)) / idx_risk
        if L.min_delta <= d <= ceiling and abs(i - 3) <= L.max_strike_offset and CE[i] * lot <= e.capital.capital:
            ok.append((CE[i], k, d))
    want = max(ok)[1] if ok else None
    check("the strike is the dearest CE whose delta fits the cap at the rule's index stop",
          pos is not None and pos.strike == want, f"chose {pos.strike if pos else None}, want {want} of {ok}")
    if pos is None:
        return
    extra = e._extra_risk_points(pos.entry, 1.0, lot)
    check("premium stop + costs + exit slip x qty is inside max_risk_pct_per_trade",
          (pos.risk_points + extra) * pos.qty <= cap + 1e-6,
          f"Rs {(pos.risk_points + extra) * pos.qty:,.0f} <= Rs {cap:,.0f} ({L.max_risk_pct_per_trade:g}%)")
    check("premium risk = max(index risk x delta, 2 spreads), capped at max_risk_frac_of_premium",
          abs(pos.risk_points - min(max(idx_risk * pos.detail["delta"], 2.0), pos.entry * L.max_risk_frac_of_premium)) < 1e-6,
          f"{pos.risk_points:.2f} pt")
    check("one lot (hunt()'s sizing for an ungraded setup)", pos.qty == 65 and pos.detail.get("lots") == 1)
    # Next expiry on expiry day (expiry_trade_next).
    CLK.at(15, 0, 3)
    e2, api2, lg2 = make(bindings=[Binding("01OCT2026", 0.001, 1000), Binding("06OCT2026", 0.02, 3000)])
    e2.hunt()
    p2 = e2.book.snapshot()[0] if e2.book.count() else None
    check("on expiry day the contract is the NEXT expiry's",
          p2 is not None and p2.expiry == "06OCT2026" and "06OCT2026" in p2.symbol,
          p2.symbol if p2 else str(lg2[-2:]))


def t_position():
    section("5. The position: the rule's index stop, no target, tagged last_half_hour")
    e, api, lg, pos = entered()
    if pos is None:
        check("entered", False, str(lg[-3:]))
        return
    rows = history()
    stop = 24100.0 - e.L.lhm_stop_atr * atr(rows[:-1], period=e.L.atr_period)
    check("index stop = close - lhm_stop_atr x ATR", abs(pos.index_invalidation - stop) < 1e-6,
          f"{pos.index_invalidation:.2f}")
    check("no target: index target 0 and the premium target parked out of reach",
          pos.index_target == 0.0 and pos.target >= pos.entry * 50)
    check("exit rules 'lhm', playbook last_half_hour",
          pos.exit_rules == "lhm" and pos.detail.get("playbook") == "last_half_hour"
          and pos.detail.get("playbooks") == ["last_half_hour"], str(pos.detail.get("playbooks")))
    check("premium stop = fill - premium risk", abs(pos.stop - (pos.entry - pos.risk_points)) < 1e-9)
    # The index rises for many bars: a swing trail would move the index stop.
    e.candles.rows = rows + bars(TODAY, 10, lambda i: 24100.0 + 8.0 * i, 3.0, start="15:00")
    e.candles.live = {}
    for k in range(5):
        CLK.at(15, 1 + k, 1)
        e.feed.store.ltp[0] = 24100.0 + 8.0 * k
        set_bid(e, pos, pos.entry + 0.5 * pos.risk_points)
        e.manage()
    check("the index stop never trails", pos.index_invalidation == stop and not pos.index_trailed)


def reprice_rule(entry, prem_risk, cost_pts, bids):
    """research/lab/realistic.py reprice, its per-bar premium rule:
        stop = ask0 - prem_risk
        if not trailed and bid - ask0 >= prem_risk: stop, trailed = max(stop, ask0 + cost_pts), True
        if bid <= stop and k < j: exit (prem_trail if trailed else prem_stop)
    Returns (index of the exiting bid, reason) or (None, "")."""
    stop, trailed = entry - prem_risk, False
    for k, bid in enumerate(bids):
        if not trailed and bid - entry >= prem_risk:
            stop, trailed = max(stop, entry + cost_pts), True
        if bid <= stop:
            return k, ("prem_trail" if trailed else "prem_stop")
    return None, ""


def t_exits():
    section("6. Exits match reprice: premium stop, +1R cost-covered breakeven, index stop, square-off")
    names = {"prem_stop": "stop_loss", "prem_trail": "trailed_stop"}
    paths = {
        "straight down to the premium stop": [-0.3, -0.7, -1.0, -1.2],
        "+1R, then back under entry + costs": [0.4, 1.0, 0.6, 0.2, -0.1],
        "+0.99R, then down to the stop": [0.5, 0.99, 0.2, -0.5, -1.01],
        "+1R and on to +2.5R, never back": [0.5, 1.0, 1.8, 2.5, 2.2],
        "+1.5R, a pullback to +0.2R holds": [1.5, 0.6, 0.2, 0.9],
    }
    for label, rs in paths.items():
        e, api, lg, pos = entered()
        if pos is None:
            check(f"{label}: entered", False)
            continue
        R, entry, cost = pos.risk_points, pos.entry, pos.cost_points
        bids = [entry + r * R for r in rs]
        if label.startswith("+1R, then back"):
            bids[-2] = entry + cost + 0.05          # just above the breakeven: holds
            bids[-1] = entry + cost - 0.05          # just under: out
        want_k, want = reprice_rule(entry, R, cost, bids)
        got_k, got = None, ""
        e.feed.store.ltp[0] = 24100.0             # index stop not touched
        for k, bid in enumerate(bids):
            CLK.at(15, 2 + k, 0)
            set_bid(e, pos, bid)
            e.manage()
            if e.book.count() == 0:
                got_k, got = k, e.journal.rows[-1]["exit_reason"]
                break
        check(f"{label}: engine exits where reprice does",
              got_k == want_k and got == names.get(want, ""),
              f"engine ({got_k}, {got}) reprice ({want_k}, {want})")
        if pos.trailed:
            check(f"{label}: ...breakeven = entry + the exact round trip (reprice ask0 + cost_pts)",
                  abs(pos.stop - (entry + cost)) < 1e-9 and abs(cost - e._round_trip_points(entry, 65)) < 1e-9,
                  f"stop {pos.stop:.2f} entry {entry:.2f} cost {cost:.3f}")
    # Pinned against the config: a premium ratchet or another trigger does not apply.
    e, api, lg, pos = entered()
    e.L.trail_mode, e.L.trail_trigger_r = "premium", 0.5
    R, entry = pos.risk_points, pos.entry
    CLK.at(15, 2)
    set_bid(e, pos, entry + 0.6 * R)
    e.manage()
    check("trail_trigger_r 0.5 in the config: no breakeven at +0.6R (reprice's is +1R)", not pos.trailed)
    for k, r in enumerate((1.0, 3.0)):
        CLK.at(15, 3 + k)
        set_bid(e, pos, entry + r * R)
        e.manage()
    check("trail_mode premium in the config: no ratchet behind the best price after breakeven",
          pos.trailed and abs(pos.stop - (entry + pos.cost_points)) < 1e-9, f"stop {pos.stop:.2f}")
    # No time stop (reprice has none); a retest position would be cut.
    e, api, lg, pos = entered()
    e.L.time_stop_min = 5.0
    pos.opened_at = CLK.t - 600.0
    act, why = e.book.review(pos, pos.entry, index_px=24100.0, minutes_to_close=10.0)
    other = Position(trade_id=50, symbol="Y", token="1", strike=0.0, opt="CE", side=1, qty=65, entry=100.0,
                     stop=90.0, target=150.0, risk_points=10.0, index_invalidation=0.0,
                     opened_at=CLK.t - 600.0)
    act2, why2 = e.book.review(other, 100.0, minutes_to_close=10.0)
    check("no time stop for last_half_hour (a retest position is cut at the same age)",
          act == HOLD and act2 == EXIT and why2 == "time_stop", f"{act}/{why} vs {act2}/{why2}")
    # The index stop.
    e, api, lg, pos = entered()
    CLK.at(15, 5)
    set_bid(e, pos, pos.entry)
    e.feed.store.ltp[0] = pos.index_invalidation - 0.5
    e.manage()
    check("the index through the rule's stop: out (structure_broken), a SELL at the broker",
          e.book.count() == 0 and e.journal.rows and e.journal.rows[-1]["exit_reason"] == "structure_broken"
          and len(api.sells()) == 1)
    # The square-off.
    e, api, lg, pos = entered()
    set_bid(e, pos, pos.entry + 0.5 * pos.risk_points)
    CLK.at(15, 13, 30)
    e.manage()
    check("held at 15:13:30", e.book.count() == 1)
    CLK.at(15, 14, 1)
    e.manage()
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("square-off at 15:14 closes it with a SELL", e.book.count() == 0 and r.get("exit_reason") == "squareoff"
          and len(api.sells()) == 1 and int(api.sells()[0]["quantity"]) == 65)
    check("the journal row is tagged last_half_hour",
          r.get("playbook") == "last_half_hour" and r.get("playbooks") == ["last_half_hour"]
          and r.get("exit_rules") == "lhm" and r.get("mode") == "live", str({k: r.get(k) for k in
                                                                              ("playbook", "playbooks", "mode")}))
    check("...with the index stop and no target", r.get("index_target") == 0.0
          and abs(r.get("index_stop_final", 0) - round(pos.index_invalidation, 2)) < 1e-9)


def t_adopt():
    section("7. An entry adopted after an unknown status keeps the rule's exits")
    e, api, lg, pos = entered()
    if pos is None:
        check("entered", False)
        return
    plan = {"risk_points": pos.risk_points, "index_invalidation": pos.index_invalidation,
            "cost_points_round_trip": pos.cost_points, "risk_inr": 1.0, "reward_inr": 0.0,
            "index_entry": 24100.0, "index_risk": 30.0, "exit_rules": "lhm", "playbook": "last_half_hour"}
    cand = SimpleNamespace(symbol=pos.symbol, token=pos.token, strike=pos.strike, opt=pos.opt, ask=pos.entry,
                           lot_size=65, expiry=pos.expiry, underlying="NIFTY", segment="NFO", delta=0.4,
                           cheapness=0.0, offset=1, detail={"spread": 1.0})
    e.book.remove(pos.trade_id)
    e._adopt_entry(cand, plan, _IE._LhmFrame(24100.0, pos.index_invalidation, "lhm"), None, None, 65)
    p = e.book.snapshot()[0]
    check("adopted: exit rules lhm, no target", p.exit_rules == "lhm" and p.index_target == 0.0
          and p.target >= p.entry * 50 and p.detail.get("adopted"))


def t_modes():
    section("8. shadow never orders; off and unknown values do nothing")
    CLK.at(15, 0, 3)
    e, api, lg = make(mode="shadow")
    e.L.lhm_risk_pct = 25.0
    e.hunt()
    check("shadow: the would-trade is noted, no order, no position",
          any("WOULD BUY" in x and "NO ORDER SENT" in x for x in lg) and not api.params and e.book.count() == 0,
          str([x for x in lg if "last_half_hour" in x][-1:]))
    CLK.at(15, 14, 30)
    e.book.time_to_close = lambda *_: 0.5
    e.hunt()
    check("shadow: still nothing sent at its exit", not api.params and e.trades_today == 0)
    CLK.at(15, 0, 3)
    e, api, lg = make(mode="off")
    e.hunt()
    check("off: nothing noted, nothing sent", not api.params and not any("last_half_hour" in x for x in lg))
    e, api, lg = make(mode="yes")
    e.hunt()
    e.hunt()
    check("an unknown value is off, said once", not api.params
          and sum("is not a mode" in x for x in lg) == 1, str([x for x in lg if "mode" in x]))


try:
    for name, fn in (("signal", t_signal), ("once", t_once), ("gates", t_gates), ("contract", t_contract),
                     ("position", t_position), ("exits", t_exits), ("adopt", t_adopt), ("modes", t_modes)):
        guarded(name, fn)
finally:
    (_IE.time, _X.time, _P.time, _IE.now_ist, _P.now_ist, _RG.now_ist, _X.now_ist) = _saved

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
