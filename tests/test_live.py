#!/usr/bin/env python3
"""Live-mode safety: the broker must report what FILLED, not what was accepted,
and the engine must never believe it closed a position that is still open.

A fake SmartAPI plays the broker. No network, no orders.
"""
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb.capital_book import CapitalBook
from arb.config import CostModel, IntradayLimits
from arb.execution import LiveBroker
from arb.intraday_engine import IntradayEngine, _Ticket
from arb.smc import BULL

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


class FakeAngel:
    """Each placed order plays out the next scripted outcome:
    (status sequence, filled qty, avg price) or "refuse"."""

    def __init__(self, script):
        self.script = list(script)
        self.orders, self.cancelled, self.params = {}, [], []

    def placeOrderFullResponse(self, params):
        self.params.append(dict(params))
        out = self.script.pop(0)
        if out == "refuse":
            return {"status": False, "message": "RMS: margin exceeds"}
        n = str(len(self.params))
        self.orders["u" + n] = {"seq": list(out[0]), "filled": out[1], "avg": out[2], "id": n}
        return {"status": True, "data": {"orderid": n, "uniqueorderid": "u" + n}}

    def individual_order_details(self, uid):
        o = self.orders.get(uid)
        if o is None:
            return None
        st = o["seq"].pop(0) if len(o["seq"]) > 1 else o["seq"][0]
        final = st in ("complete", "cancelled", "rejected")
        return {"data": {"status": st, "filledshares": str(o["filled"] if final else 0),
                         "averageprice": str(o["avg"] if final else 0), "text": ""}}

    def cancelOrder(self, oid, variety):
        self.cancelled.append(oid)
        for o in self.orders.values():
            if o["id"] == oid:
                o["seq"] = ["cancelled"]

    def orderBook(self):
        return {"data": []}


def leg(side="BUY", qty=65, price=100.0, **kw):
    return dict({"symbol": "NIFTY29SEP2622800CE", "token": "1", "side": side, "price": price,
                 "qty": qty, "instrument": "OPT", "exchange": "NFO"}, **kw)


def place(script, **legkw):
    api = FakeAngel(script)
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True, confirm_timeout=0.6)
    legs, errs = b.place_structure(_Ticket([leg(**legkw)], legkw.get("qty", 65), "T", "t", ""), 1)
    return api, legs[0], errs


# ─────────────────────────────────────────────────────────────────────────────
section("1. The broker reports what filled, not what was accepted")

api, l, e = place([(["open", "complete"], 65, 101.5)])
check("a complete order is filled at the broker's average price",
      l["filled"] and l["qty"] == 65 and l["fill_price"] == 101.5, str(l.get("broker_status")))
api, l, e = place([(["cancelled"], 0, 0)])
check("an IOC that was ACCEPTED but CANCELLED unfilled is NOT a fill",
      not l["filled"] and e, e[0] if e else "")
api, l, e = place([(["rejected"], 0, 0)])
check("a rejected order is not a fill", not l["filled"])
api, l, e = place([(["open"], 30, 100.0)])
check("an order still working after the timeout is cancelled, then read again",
      api.cancelled == ["1"] and l["broker_status"] == "cancelled", f"cancelled {api.cancelled}")
api, l, e = place([(["complete"], 30, 100.2)])
check("a partial fill reports the filled quantity, not the order quantity",
      l["filled"] and l["qty"] == 30, f"qty {l['qty']}")
api, l, e = place(["refuse"])
check("an order the broker refuses is not a fill, and says why",
      not l["filled"] and "margin" in e[0], e[0])
api, l, e = place([(["complete"], 65, 99.0)], market=True)
check("a market leg goes as a marketable limit, touch + 2.5%, IOC (NSE refuses API MARKET orders)",
      api.params[0]["ordertype"] == "LIMIT" and api.params[0]["price"] == "102.50"
      and api.params[0]["duration"] == "IOC", str(api.params[0]))
api, l, e = place([(["complete"], 65, 99.0)], market=True, side="SELL", exchange="BFO")
check("a market SELL on BFO is a limit 2.5% under the touch, rounded down to the tick",
      api.params[0]["ordertype"] == "LIMIT" and api.params[0]["price"] == "97.50", str(api.params[0]))
check("intraday orders go as INTRADAY (MIS), IOC at a limit",
      place([(["complete"], 65, 99.0)])[0].params[0]["producttype"] == "INTRADAY"
      and place([(["complete"], 65, 99.0)])[0].params[0]["duration"] == "IOC")


class Lost(FakeAngel):
    def individual_order_details(self, uid):
        return None


b = LiveBroker(Lost([(["open"], 0, 0)]), CostModel(), confirm_fills=True, confirm_timeout=0.3)
legs, errs = b.place_structure(_Ticket([leg()], 65, "T", "t", ""), 1)
check("an order whose status cannot be read is treated as NOT filled, loudly",
      not legs[0]["filled"] and "UNKNOWN" in errs[0], errs[0])

# ─────────────────────────────────────────────────────────────────────────────
section("2. The engine never believes it closed a position that is still open")


class _Store:
    def __init__(self):
        self.ltp, self.bid, self.ask = [0.0, 24000.0], [110.0, 0.0], [110.5, 0.0]
        self.slot_of_token = {"1": 0}


class _Cap:
    capital = 10_000.0

    def __init__(self):
        self.recs = []

    def daily_target(self, pct):
        return self.capital * pct / 100.0

    def record(self, net, count=True):
        self.recs.append((net, count))


class _Journal:
    def __init__(self):
        self.rows = []

    def record_trade(self, r):
        self.rows.append(r)


class ScriptBroker:
    """Returns scripted exit outcomes: (filled?, qty, price)."""
    mode = "live"

    def __init__(self, outs):
        self.outs, self.seen = list(outs), []

    def place_structure(self, ticket, lots):
        l = dict(ticket.legs[0])
        self.seen.append(l)
        ok, q, px = self.outs.pop(0)
        l.update(filled=ok, qty=q if ok else l["qty"], fill_price=px if ok else 0.0,
                 broker_status="complete" if ok else "cancelled")
        return [l], []


def engine(outs):
    e = IntradayEngine(SimpleNamespace(store=_Store(), spot_slots={"NIFTY": 1}, tick_event=threading.Event()),
                       [], SimpleNamespace(session_start="09:15", session_end="15:15"), CostModel(),
                       IntradayLimits(), ScriptBroker(outs), _Journal(), _Cap(),
                       SimpleNamespace(get=lambda *a, **k: []))
    from arb.positions import Position
    pos = Position(trade_id=1, symbol="NIFTY29SEP2622800CE", token="1", strike=22800, opt="CE",
                   side=1, qty=130, entry=100.0, stop=90.0, target=130.0, risk_points=10.0,
                   index_invalidation=0.0, opened_at=0.0, underlying="NIFTY", segment="NFO")
    e.book.add(pos)
    return e, pos


e, pos = engine([(False, 0, 0), (False, 0, 0), (False, 0, 0), (True, 130, 109.0)])
for _ in range(3):
    e.close(pos, "stop_loss")
check("unfilled exits keep the position open and managed",
      e.book.count() == 1 and pos.exit_attempts == 3 and not e.journal.rows,
      f"{pos.exit_attempts} failed tries, still open")
check("...no P&L is booked for an exit that did not happen", not e.capital.recs)
e.close(pos, "stop_loss")
check("after exit_market_after misses, the exit goes at MARKET",
      e.broker.seen[-1].get("market") is True and not e.broker.seen[0].get("market"))
check("...and when it fills, the trade closes and is journalled",
      e.book.count() == 0 and len(e.journal.rows) == 1)

e, pos = engine([(True, 65, 111.0), (True, 65, 109.0)])
e.close(pos, "target")
check("a partial exit books the filled part and keeps the rest open",
      e.book.count() == 1 and pos.qty == 65 and len(e.capital.recs) == 1
      and e.capital.recs[0][1] is False and not e.journal.rows,
      f"{pos.qty} left, booked Rs {e.capital.recs[0][0]:+.2f}")
e.close(pos, "target")
r = e.journal.rows[-1] if e.journal.rows else {}
check("the final record covers the whole position at the average exit",
      r.get("qty") == 130 and abs(r.get("exit", 0) - 110.0) < 1e-9
      and abs(r.get("gross_inr", 0) - (11 * 65 + 9 * 65)) < 1e-6,
      f"qty {r.get('qty')} exit {r.get('exit')} gross {r.get('gross_inr')}")
check("the day's P&L equals the sum of what was booked",
      abs(e.realised_pnl - sum(n for n, _ in e.capital.recs)) < 1e-9
      and abs(e.realised_pnl - r.get("net_inr", 0)) < 0.01)



class UnknownBroker(ScriptBroker):
    def __init__(self, held):
        super().__init__([])
        self.held = held

    def place_structure(self, ticket, lots):
        l = dict(ticket.legs[0], filled=False, fill_price=0.0, broker_status="unknown")
        self.seen.append(l)
        return [l], ["status unknown"]

    def net_position(self, symbol):
        return self.held


e, pos = engine([])
e.broker = UnknownBroker(held=0)
e.close(pos, "stop_loss")
check("exit status unknown but the broker shows nothing held: booked as closed, not re-sold",
      e.book.count() == 0 and len(e.journal.rows) == 1)
e, pos = engine([])
e.broker = UnknownBroker(held=130)
e.close(pos, "stop_loss")
check("exit status unknown and the broker still shows it held: stays open, retried",
      e.book.count() == 1 and pos.exit_attempts == 1)
e, pos = engine([])
e.broker = UnknownBroker(held=None)
e.close(pos, "stop_loss")
check("exit status unknown and positions unreadable: new entries halt, nothing re-sent blindly",
      e.halted and e.book.count() == 1 and len(e.broker.seen) == 1, e.halt_reason)

# ─────────────────────────────────────────────────────────────────────────────
section("3. Live capital is the broker's money, not the paper ledger")

import tempfile
with tempfile.TemporaryDirectory() as d:
    cb = CapitalBook(10_000.0, path=os.path.join(d, "c.json"))
    cb.open_day()
    check("a fresh live day opens on the broker's funds", cb.sync_to_funds(25_000.0) == 25_000.0)
    cb.record(-500.0)
    check("a restart mid-day keeps the day's opening capital (the halt cannot move)",
          cb.sync_to_funds(24_500.0) == 25_000.0)

# ─────────────────────────────────────────────────────────────────────────────
section("4. ./run.sh_real blocks sleep only when the config asks")

check("live_block_sleep is off by default (no inhibitor lock, as before)",
      IntradayLimits().live_block_sleep is False)
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "intraday.json")
    with open(p, "w") as fh:
        fh.write('{"live_block_sleep": true}')
    check("live_block_sleep: true in the json turns the lock on",
          IntradayLimits.load(p).live_block_sleep is True)

# ─────────────────────────────────────────────────────────────────────────────
section("5. Exit safety: never a second exit over one that may still fill")

import json as _json
import arb.execution as _X
import arb.intraday_engine as _IE
from arb.positions import Position

SYM = "NIFTY29SEP2622800CE"


def guarded(label, fn):
    """Run one test; an exception is a failure of that test, not of the file
    (the parent commit has none of these methods)."""
    try:
        fn()
    except BaseException as ex:                          # noqa: BLE001
        if isinstance(ex, KeyboardInterrupt):
            raise
        check(f"{label} (raised)", False, repr(ex)[:200])


class Clock:
    """A fake clock for the broker, the engine and the start guard: sleeping
    moves it on, so the 6 s / 3 s / paced waits cost nothing."""
    def __init__(self, real):
        self.real, self.t = real, 1_800_000_000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, d):
        self.t += max(0.0, float(d))

    def __getattr__(self, k):
        return getattr(self.real, k)


class ExitAngel(FakeAngel):
    """Orders stay in whatever state the test puts them in. A cancel can be
    ignored (sticky), the place call can raise after Angel took the order,
    and the order and position books can be read (or not)."""

    def __init__(self, script=(), netqty=None):
        super().__init__(script)
        self.sticky, self.raise_place, self.book_ok = False, False, True
        # None: the position book is current (what the order book has filled);
        # a number: the position book shows that, however stale.
        self.netqty = netqty
        self.cancels, self.events = [], []
        self.opening = []                # the day's rows that opened the position

    def hold(self, qty, side="BUY"):
        """Today's completed order that opened the position (ids E1, E2...)."""
        n = "E%d" % (len(self.opening) + 1)
        self.opening.append({"id": n, "status": "complete", "filled": int(qty), "avg": 100.0,
                             "params": {"variety": "NORMAL", "tradingsymbol": SYM,
                                        "transactiontype": side, "quantity": str(qty),
                                        "price": "100.00", "ordertype": "LIMIT"}})

    def true_net(self):
        return sum(o["filled"] * (1 if o["params"].get("transactiontype") == "BUY" else -1)
                   for o in self.opening + list(self.orders.values()))

    def _add(self, params, status, filled, avg):
        n = str(len(self.orders) + 1)
        self.orders["u" + n] = {"id": n, "status": status, "filled": filled, "avg": avg,
                                "params": dict(params)}
        return n

    def seed(self, status="open", tag="", side="SELL", variety="NORMAL", symbol=SYM):
        """An order already at Angel (left by an earlier process, or by hand)."""
        return self._add({"variety": variety, "tradingsymbol": symbol, "transactiontype": side,
                          "quantity": "65", "price": "100.00", "ordertype": "LIMIT",
                          **({"ordertag": tag} if tag else {})}, status, 0, 0.0)

    def placeOrderFullResponse(self, params):
        self.params.append(dict(params))
        st, filled, avg = self.script.pop(0) if self.script else ("open", 0, 0.0)
        n = self._add(params, st, filled, avg)
        self.events.append(("place", n))
        if self.raise_place:
            self.raise_place = False
            raise TimeoutError("read timed out")       # ...but Angel has the order
        return {"status": True, "data": {"orderid": n, "uniqueorderid": "u" + n}}

    def _row(self, o):
        p = o["params"]
        return {"orderid": o["id"], "uniqueorderid": "u" + o["id"], "status": o["status"],
                "orderstatus": o["status"], "filledshares": str(o["filled"]),
                "averageprice": str(o["avg"]), "quantity": p.get("quantity"),
                "unfilledshares": str(int(p.get("quantity") or 0) - o["filled"]),
                "variety": p.get("variety"), "tradingsymbol": p.get("tradingsymbol"),
                "transactiontype": p.get("transactiontype"), "ordertag": p.get("ordertag", ""),
                "ordertype": p.get("ordertype"), "price": p.get("price"),
                "triggerprice": "0", "text": ""}

    def individual_order_details(self, uid):
        o = self.orders.get(uid)
        if o is None:
            return None
        self.events.append(("read", o["id"], o["status"]))
        return {"status": True, "data": self._row(o)}

    def orderBook(self):
        if not self.book_ok:
            raise ConnectionError("orderBook timed out")
        for o in self.orders.values():
            self.events.append(("read", o["id"], o["status"]))
        return {"status": True, "data": [self._row(o) for o in self.opening]
                + [self._row(o) for o in self.orders.values()]}

    def cancelOrder(self, oid, variety):
        self.cancels.append((oid, variety))
        self.cancelled.append(oid)
        o = self.orders.get("u" + str(oid))
        if o and not self.sticky and o["status"] not in ("complete", "cancelled", "rejected"):
            o["status"] = "cancelled"
        return {"status": True, "data": {"orderid": oid}}

    def position(self):
        net = self.true_net() if self.netqty is None else self.netqty
        return {"status": True, "data": [{"tradingsymbol": SYM, "netqty": str(net)}]}

    def set(self, oid, status, filled=None, avg=None):
        o = self.orders["u" + str(oid)]
        o["status"] = status
        if filled is not None:
            o["filled"] = filled
        if avg is not None:
            o["avg"] = avg

    def placed(self):
        return len(self.params)


class Notes:
    def __init__(self):
        self.msgs = []

    def send(self, m):
        self.msgs.append(m)


def live_engine(api, duration="IOC", fixes=True, market=False):
    """The section-2 engine (one 130-qty long, entry 100) on a real LiveBroker
    over a fake Angel."""
    e, pos = engine([])
    if hasattr(api, "hold") and not api.opening:
        api.hold(pos.qty)                 # the BUY that opened it is in today's order book
    e.L.exit_safety_fixes = fixes
    e.L.market_orders = market
    b = LiveBroker(api, CostModel(), product="INTRADAY", duration=duration,
                   confirm_fills=True, confirm_timeout=6.0)
    b.exit_safety_fixes = fixes
    e.broker = b
    e.notifier = Notes()
    return e, pos


_clk = Clock(_X.time)
_real_time = (_X.time, _IE.time)
_X.time = _IE.time = _clk


def t1_golden():
    b = LiveBroker(FakeAngel([]), CostModel(), product="INTRADAY", confirm_fills=True)
    entry = b._order_params(leg(side="BUY", qty=65, tag="TELE0109300001"), 100.0)
    exit_ = b._order_params(leg(side="SELL", qty=130, tag="TELX0109300002"), 110.0, market=True)
    want_entry = {"variety": "NORMAL", "tradingsymbol": SYM, "symboltoken": "1",
                  "transactiontype": "BUY", "exchange": "NFO", "ordertype": "LIMIT",
                  "producttype": "INTRADAY", "duration": "IOC", "price": "100.00",
                  "squareoff": "0", "stoploss": "0", "quantity": "65"}
    want_exit = dict(want_entry, transactiontype="SELL", price="107.25", quantity="130")
    check("1. golden: flags off, entry and exit order params are byte-identical to today's",
          _json.dumps(entry) == _json.dumps(want_entry) and _json.dumps(exit_) == _json.dumps(want_exit),
          f"{entry} / {exit_}")
    b.order_tags = True
    tagged = b._order_params(leg(side="SELL", qty=130, tag="TELX0109300002"), 110.0, market=True)
    check("1. ...with order_tags on, the one change is the ordertag",
          tagged == dict(want_exit, ordertag="TELX0109300002"), str(tagged))


def t2_pending_ioc():
    api = ExitAngel([("open", 0, 0.0), ("complete", 130, 108.0)])
    api.sticky = True                                 # Angel ignores the cancel
    e, pos = live_engine(api)
    e.close(pos, "stop_loss")
    check("2. an IOC exit still 'open' after its cancel is remembered on the position",
          pos.pending_exit_oid == "1" and api.placed() == 1 and e.book.count() == 1,
          f"pending {pos.pending_exit_oid!r}, placed {api.placed()}")
    e.close(pos, "stop_loss")
    _clk.t += 4.0
    e.close(pos, "stop_loss")
    check("2. ...the next close() places nothing over it, and re-sends the cancel",
          api.placed() == 1 and len(api.cancels) >= 2, f"placed {api.placed()}, cancels {api.cancels}")
    api.set("1", "cancelled", 0)
    e.close(pos, "stop_loss")
    check("2. ...once it reads cancelled with 0 filled, exactly one new exit goes out",
          api.placed() == 2 and e.book.count() == 0 and not pos.pending_exit_oid,
          f"placed {api.placed()}")
    api = ExitAngel([("open", 0, 0.0), ("complete", 130, 108.0)])
    api.sticky = True
    e, pos = live_engine(api, fixes=False)
    e.close(pos, "stop_loss")
    e.close(pos, "stop_loss")
    check("2. flag off: today's behaviour (the next close() sends a new order at once)",
          api.placed() == 2, f"placed {api.placed()}")


def t3_place_raises():
    api = ExitAngel([("open", 0, 0.0), ("complete", 130, 108.0)], netqty=130)
    api.sticky, api.raise_place = True, True
    e, pos = live_engine(api)
    e.close(pos, "stop_loss")
    check("3. the place call raised after Angel took the order: marked to find by symbol",
          pos.pending_exit_tag == "?" and api.placed() == 1 and e.book.count() == 1,
          f"tag {pos.pending_exit_tag!r}")
    e.close(pos, "stop_loss")
    _clk.t += 4.0
    e.close(pos, "stop_loss")
    check("3. ...no second order while the order book shows it working (it is found and cancelled)",
          api.placed() == 1 and pos.pending_exit_oid == "1" and ("1", "NORMAL") in api.cancels,
          f"placed {api.placed()}, oid {pos.pending_exit_oid!r}, cancels {api.cancels}")
    api.set("1", "cancelled", 0)
    e.close(pos, "stop_loss")
    check("3. ...resolved once it reads cancelled: one new exit, and the trade closes",
          api.placed() == 2 and e.book.count() == 0, f"placed {api.placed()}")
    api = ExitAngel([("open", 0, 0.0)], netqty=130)
    api.sticky, api.raise_place = True, True
    e, pos = live_engine(api)
    e.close(pos, "stop_loss")
    api.book_ok = False
    for _ in range(3):
        _clk.t += 3.0
        e.close(pos, "stop_loss")
    check("3. ...and while the order book cannot be read: nothing re-sent, entries halt, one alert",
          api.placed() == 1 and e.halted and "unreadable" in e.halt_reason
          and sum("cannot be read" in m for m in e.notifier.msgs) == 1, e.halt_reason)


def t3b_structure_raises():
    api = ExitAngel([("complete", 130, 108.0)])
    e, pos = live_engine(api)

    def boom(ticket, lots):
        api.seed("open", side="SELL")                 # it went, then something broke
        raise RuntimeError("bad status payload")
    real = e.broker.place_structure
    e.broker.place_structure = boom
    e.close(pos, "stop_loss")
    e.broker.place_structure = real
    e.close(pos, "stop_loss")
    check("3b. place_structure itself raised: no new exit while a SELL is working in the symbol",
          api.placed() == 0 and pos.pending_exit_oid == "1", f"placed {api.placed()}")
    api.set("1", "cancelled", 0)
    e.close(pos, "stop_loss")
    check("3b. ...and one new exit once that order reads cancelled",
          api.placed() == 1 and e.book.count() == 0, f"placed {api.placed()}")


def t4_late_fill():
    api = ExitAngel([("open", 0, 0.0)])
    api.sticky = True
    e, pos = live_engine(api)
    e.close(pos, "stop_loss")
    api.set("1", "complete", 130, 107.0)
    e.close(pos, "stop_loss")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("4. the pending exit completes late: booked once at Angel's price, nothing placed",
          e.book.count() == 0 and api.placed() == 1 and len(e.journal.rows) == 1
          and r.get("exit") == 107.0 and r.get("exit_order") == "late_fill",
          f"placed {api.placed()}, rows {len(e.journal.rows)}, exit {r.get('exit')}")
    check("4. ...the day's P&L is that one trade", abs(e.realised_pnl - r.get("net_inr", 1e9)) < 0.01)
    api = ExitAngel([("open", 65, 108.0)])            # half filled, rest still working
    api.sticky = True
    e, pos = live_engine(api)
    e.close(pos, "stop_loss")
    half = pos.qty
    api.set("1", "complete", 130, 107.0)
    e.close(pos, "stop_loss")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("4. partial then late fill: the rest is booked so the order averages Angel's 107.00",
          half == 65 and e.book.count() == 0 and api.placed() == 1
          and abs(r.get("exit", 0) - 107.0) < 1e-9 and r.get("qty") == 130,
          f"left {half}, exit {r.get('exit')}, qty {r.get('qty')}")


def t5_sign():
    e, pos = engine([])
    e.L.exit_safety_fixes = True
    e.notifier = Notes()
    e.broker = UnknownBroker(held=-130)
    e.close(pos, "stop_loss")
    e.close(pos, "stop_loss")
    check("5. long, broker shows -130: halted, NAKED alert, nothing booked, no further order",
          e.halted and "NAKED" in e.halt_reason and e.realised_pnl == 0 and not e.capital.recs
          and e.book.count() == 1 and len(e.broker.seen) == 1
          and any("NAKED" in m for m in e.notifier.msgs), e.halt_reason)
    e, pos = engine([])
    e.L.exit_safety_fixes = True
    e.broker = UnknownBroker(held=260)
    e.close(pos, "stop_loss")
    check("5. broker holds 260 of a 130 position: halted, nothing booked",
          e.halted and "MORE" in e.halt_reason and not e.capital.recs and e.book.count() == 1,
          e.halt_reason)
    e, pos = engine([])
    e.broker = UnknownBroker(held=-130)
    e.close(pos, "stop_loss")
    check("5. flag off: today's numbers (abs() books it closed)",
          e.book.count() == 0 and len(e.journal.rows) == 1 and not e.halted)


def t6_variety():
    api = ExitAngel([("open", 0, 0.0)])
    api.sticky = True
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True, confirm_timeout=1.0)
    b.exit_safety_fixes = True
    base = b._order_params
    b._order_params = lambda *a, **k: dict(base(*a, **k), variety="STOPLOSS")
    b.place_structure(_Ticket([leg(side="SELL")], 65, "T", "t", ""), 1)
    check("6. the confirm-timeout cancel uses the order's own variety, not a literal NORMAL",
          api.cancels and api.cancels[0] == ("1", "STOPLOSS"), str(api.cancels))
    e, pos = live_engine(api)
    e.broker = b
    api.cancels.clear()
    pos.pending_exit_oid, pos.pending_exit_uid, pos.pending_exit_at = "1", "u1", _clk.t - 10
    e.close(pos, "stop_loss")
    check("6. ...and so does the pending-exit cancel (from the order row's variety)",
          api.cancels == [("1", "STOPLOSS")] and api.placed() == 1, str(api.cancels))
    api = ExitAngel([("open", 0, 0.0)])
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True, confirm_timeout=1.0)
    base2 = b._order_params
    b._order_params = lambda *a, **k: dict(base2(*a, **k), variety="STOPLOSS")
    b.place_structure(_Ticket([leg(side="SELL")], 65, "T", "t", ""), 1)
    check("6. flag off: today's literal NORMAL", api.cancels == [("1", "NORMAL")], str(api.cancels))


def t7_partial_halt():
    e, pos = engine([(True, 65, 70.0), (True, 65, 70.0)])
    e.L.exit_safety_fixes = True
    e.close(pos, "stop_loss")
    check("7. a partial fill that takes the day past -15% halts before the full close",
          e.halted and e.halt_reason == "daily loss limit" and e.book.count() == 1
          and e.realised_pnl <= -1500, f"day Rs {e.realised_pnl:,.0f}, halted {e.halted}")


def _cand():
    return SimpleNamespace(symbol=SYM, token="1", ask=110.5, lot_size=65, segment="NFO",
                           underlying="NIFTY", strike=22800, opt="CE", depth=500, age_ms=0.0,
                           expiry="29SEP2026", delta=0.5, cheapness=0.0, offset=0,
                           detail={"spread": 0.5}, lot_cost=110.5 * 65)


def _plan():
    return {"risk_points": 20.0, "index_invalidation": 23950.0, "cost_points_round_trip": 1.0,
            "risk_inr": 1300.0, "reward_inr": 2600.0}


_struct = SimpleNamespace(reason="bos", last_event="bos", closes=[24000.0])


def _empty_engine(broker, adopt=True, fixes=True):
    e, pos = engine([])
    e.book.remove(pos.trade_id)
    e.L.exit_safety_fixes, e.L.adopt_unknown_entry = fixes, adopt
    e.broker = broker
    return e


def t8_adopt():
    e = _empty_engine(UnknownBroker(held=65))
    ok = e.enter(_cand(), _plan(), _struct, None)
    snap = e.book.snapshot()
    p = snap[0] if snap else None
    check("8. unknown-status entry, broker holds 65: adopted as one position under the software stop",
          not ok and p is not None and p.qty == 65 and p.detail.get("adopted") is True
          and p.detail.get("entry_estimated") is True and p.entry == 110.5
          and abs(p.stop - 90.5) < 1e-9 and e.halted and "adopted" in e.halt_reason,
          f"{[(x.qty, x.entry, x.stop) for x in snap]} {e.halt_reason}")
    e = _empty_engine(UnknownBroker(held=0))
    e.enter(_cand(), _plan(), _struct, None)
    check("8. ...broker holds 0: no position, no halt", e.book.count() == 0 and not e.halted)
    e = _empty_engine(UnknownBroker(held=65), adopt=False, fixes=False)
    e.enter(_cand(), _plan(), _struct, None)
    check("8. flag off: today's halt only, no position", e.book.count() == 0 and e.halted)
    e = _empty_engine(UnknownBroker(held=-65))
    e.enter(_cand(), _plan(), _struct, None)
    check("8. ...broker shows -65: not adopted, halted as today", e.book.count() == 0 and e.halted)


def t8b_entry_may_work():
    api = ExitAngel([("open", 0, 0.0)], netqty=0)
    api.raise_place = True
    b = LiveBroker(api, CostModel(), product="INTRADAY", duration="DAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.enter(_cand(), _plan(), _struct, None)
    check("8b. an entry whose place call raised, nothing held yet: its working order is cancelled "
          "and entries halt",
          ("1", "NORMAL") in api.cancels and e.halted and e.book.count() == 0, e.halt_reason)
    api = ExitAngel([("open", 0, 0.0)])
    api.sticky = True
    b = LiveBroker(api, CostModel(), product="INTRADAY", duration="DAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.enter(_cand(), _plan(), _struct, None)
    check("8b. a DAY entry still working after its cancel: cancelled again, entries halt",
          len(api.cancels) >= 2 and e.halted and e.book.count() == 0, f"{api.cancels} {e.halt_reason}")


def t9_start_guard():
    import run_bot
    real = run_bot.time
    run_bot.time = _clk
    try:
        def broker(api):
            return LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
        api = ExitAngel()
        api.seed("trigger pending", tag="TELS0109300001", variety="STOPLOSS")
        api.seed("cancelled", tag="")                 # final: not a working order
        logs = []
        run_bot.refuse_working_orders(broker(api), log=logs.append)
        check("9. start guard: the engine's stale tagged order on a flat symbol is cancelled "
              "with its variety, and confirmed",
              api.cancels == [("1", "STOPLOSS")] and any("stale engine order cancelled" in x for x in logs),
              f"{api.cancels} {logs}")

        def refused(api):
            try:
                run_bot.refuse_working_orders(broker(api), log=lambda *_: None)
            except SystemExit as ex:
                return str(ex)
            return ""
        api = ExitAngel()
        api.seed("open", tag="")
        why = refused(api)
        check("9. ...an untagged working order refuses the start", "LIVE START REFUSED" in why
              and not api.cancels, why)
        api = ExitAngel()
        api.book_ok = False
        why = refused(api)
        check("9. ...an unreadable order book refuses the start", "LIVE START REFUSED" in why, why)
        api = ExitAngel()
        api.seed("open", tag="TELX0109300001")
        api.sticky = True
        why = refused(api)
        check("9. ...a stale engine order that will not cancel refuses the start",
              "LIVE START REFUSED" in why and api.cancels, why)
        api = ExitAngel()
        api.seed("open", tag="TELX0109300001")
        api.netqty = 65
        why = refused(api)
        check("9. ...a tagged order on a symbol still held is not touched: refused",
              "LIVE START REFUSED" in why and not api.cancels, why)
    finally:
        run_bot.time = real


def t10_bucket():
    b = LiveBroker(FakeAngel([]), CostModel())
    b.order_rate_per_sec = 8.0
    t0 = _clk.t
    for _ in range(20):
        b._order_gate()
    took = _clk.t - t0
    b.order_rate_per_sec = 0.0
    t1 = _clk.t
    for _ in range(20):
        b._order_gate()
    check("10. rate bucket: 20 order calls at 8/s take >= 2.375 s; rate 0 adds no wait",
          took >= 2.375 - 1e-9 and _clk.t == t1, f"took {took:.3f}s, off {_clk.t - t1:.3f}s")
    check("10. the code default is off (0), as before", IntradayLimits().order_rate_per_sec == 0.0
          and LiveBroker.order_rate_per_sec == 0.0)


def t12_day():
    api = ExitAngel([("open", 0, 0.0), ("complete", 130, 108.0)])
    api.sticky = True
    e, pos = live_engine(api, duration="DAY", market=True)
    e.close(pos, "stop_loss")
    check("12. DAY: the exit goes as a DAY limit; still 'open' past the timeout, it is cancelled",
          api.params[0]["duration"] == "DAY" and api.params[0]["ordertype"] == "LIMIT"
          and ("1", "NORMAL") in api.cancels and pos.pending_exit_oid == "1",
          f"{api.params[0]['duration']} cancels {api.cancels}")
    for _ in range(4):
        _clk.t += 4.0
        e.close(pos, "stop_loss")
    check("12. ...while it reads 'open' no second order is placed; the cancel is repeated",
          api.placed() == 1 and len(api.cancels) >= 3 and e.book.count() == 1,
          f"placed {api.placed()}, cancels {len(api.cancels)}")
    api.set("1", "cancelled", 0)
    e.close(pos, "stop_loss")
    ev = api.events
    second = ev.index(("place", "2")) if ("place", "2") in ev else -1
    final_read = next((i for i, x in enumerate(ev) if x[0] == "read" and x[1] == "1"
                       and x[2] in ("complete", "cancelled", "rejected")), -1)
    check("12. ...the re-send comes only after a status read shows the first one final",
          api.placed() == 2 and 0 <= final_read < second and api.params[1]["duration"] == "DAY"
          and e.book.count() == 0, f"final read at {final_read}, second place at {second}")


def t13_flags_default():
    L = IntradayLimits()
    check("C3 flags default to today's behaviour",
          (L.exit_safety_fixes, L.order_tags, L.order_duration, L.adopt_unknown_entry,
           L.order_rate_per_sec) == (False, False, "IOC", False, 0.0))


# ─────────────────────────────────────────────────────────────────────────────
# Review group O (2 Oct 2026): live order paths. Each test below fails on
# 30692f6 and passes with the fixes.

class ModeAngel(ExitAngel):
    """Each place call plays the next mode: "ok"; "null" (Angel executed it
    but answered with no data, SmartAPI forum 1189); "uid" (executed, only a
    uniqueorderid came back); "raise" (executed, then the call timed out);
    "lost" (never reached Angel, the call raised); "refuse" (not executed,
    no data)."""

    def __init__(self, script=(), netqty=None, modes=()):
        super().__init__(script, netqty)
        self.modes = list(modes)

    def placeOrderFullResponse(self, params):
        mode = self.modes.pop(0) if self.modes else "ok"
        if mode in ("lost", "refuse"):
            self.params.append(dict(params))
            if mode == "lost":
                raise TimeoutError("read timed out")
            return {"status": False, "errorcode": "AB4036", "message": "RMS: price outside band",
                    "data": None}
        r = super().placeOrderFullResponse(params)
        n = r["data"]["orderid"]
        if mode == "null":
            return {"status": False, "errorcode": "AB2001", "message": "", "data": None}
        if mode == "uid":
            return {"status": True, "message": "", "data": {"orderid": "", "uniqueorderid": "u" + n}}
        if mode == "raise":
            raise TimeoutError("read timed out")
        return r


def _sells(api):
    return sum(1 for p in api.params if p.get("transactiontype") == "SELL")


def tO1_null_answer():
    api = ModeAngel([("complete", 130, 107.0)] * 3, netqty=130, modes=["null"])
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    for _ in range(12):
        _clk.t += 1.0
        e.close(pos, "stop_loss")
    sent, net = _sells(api), api.true_net()
    api.netqty = None                                  # the position book catches up
    _clk.t += 3.0
    e.close(pos, "stop_loss")
    check("O1. a stop SELL Angel executed but answered with no data: never a second SELL, "
          "the account is never short, booked once the books agree",
          sent == 1 and net == 0 and api.true_net() == 0 and e.book.count() == 0
          and len(e.journal.rows) == 1, f"sells {sent}, net {net} -> {api.true_net()}, "
          f"book {e.book.count()}")
    api = ModeAngel([("complete", 130, 107.0)], modes=["uid"])
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    check("O1. ...only a uniqueorderid came back: read by it, the fill is booked at once",
          api.placed() == 1 and e.book.count() == 0 and e.journal.rows
          and e.journal.rows[-1].get("exit") == 107.0, f"placed {api.placed()}")
    api = ModeAngel([("complete", 65, 110.5)], modes=["null"])
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.notifier = Notes()
    e.enter(_cand(), _plan(), _struct, None)
    check("O1. an executed BUY answered with no data: adopted under the stop, entries halt, alert",
          e.book.count() == 1 and e.halted and e.notifier.msgs and api.true_net() == 65,
          f"book {e.book.count()}, halted {e.halted}")


def tO2_raise_lagging_book():
    api = ModeAngel([("complete", 130, 107.0)] * 3, netqty=130, modes=["raise"])
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    for _ in range(40):
        _clk.t += 1.0
        e.close(pos, "stop_loss")
    sent, net = _sells(api), api.true_net()
    check("O2. the place call raised, the IOC is COMPLETE and the position book lags: nothing "
          "re-sent; entries halt with an alert while the books disagree",
          sent == 1 and net == 0 and e.book.count() == 1 and e.halted and e.notifier.msgs,
          f"sells {sent}, net {net}, halted {e.halted}")
    api.netqty = None
    _clk.t += 3.0
    e.close(pos, "stop_loss")
    check("O2. ...booked closed once both books show it gone, still one SELL",
          _sells(api) == 1 and e.book.count() == 0 and len(e.journal.rows) == 1
          and e.journal.rows[-1].get("exit_order") == "reconciled", f"sells {_sells(api)}")


def tO3_unknown_entry_lag():
    api = ModeAngel([("complete", 65, 110.5)], netqty=0, modes=["raise"])
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.notifier = Notes()
    e.enter(_cand(), _plan(), _struct, None)
    check("O3. an entry whose place call raised, COMPLETE at Angel, position book still 0: "
          "adopted from the order book, entries halt, alert",
          e.book.count() == 1 and e.book.snapshot()[0].qty == 65 and e.halted
          and e.notifier.msgs, f"book {e.book.count()}, halted {e.halted}")


def tO4_entry_late_fill():
    api = ExitAngel([("open", 0, 0.0)], netqty=0)
    api.sticky = True
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.L.market_orders = True
    e.enter(_cand(), _plan(), _struct, None)
    api.set("1", "complete", 65, 111.0)
    api.netqty = 65
    for _ in range(20):
        _clk.t += 1.0
        e.manage()
    p = e.book.snapshot()[0] if e.book.count() else None
    check("O4. an entry still working after its cancel fills later: manage() follows it and "
          "adopts the 65 under the software stop",
          p is not None and p.qty == 65 and e.halted and not e._pending_entries,
          f"book {e.book.count()}")


def tO5_backoff_budget():
    api = ModeAngel(netqty=None, modes=["refuse"] * 2000)
    e, pos = live_engine(api, market=True)
    t_end = _clk.t + 60.0
    while _clk.t < t_end:
        e.close(pos, "stop_loss")
        _clk.t += 0.125
    alerts = [m for m in e.notifier.msgs if "EXIT NOT GOING THROUGH" in m]
    check("O5. a refused exit backs off (0.5 s doubling to 5 s): a minute of passes sends at most "
          "20, and one alert", api.placed() <= 20 and len(alerts) == 1 and api.true_net() == 130,
          f"placed {api.placed()}, alerts {len(alerts)}")
    b = LiveBroker(FakeAngel([]), CostModel())
    b.exit_safety_fixes = True
    entries = sum(1 for _ in range(600) if b._order_gate("E"))
    exits = sum(1 for _ in range(600) if b._order_gate("X"))
    cap_min = LiveBroker.ORDER_BUDGET[0][1]
    check("O5. order budget: entries stop at their share of the minute; exits keep the rest, "
          "under Angel's 500 a minute", 0 < entries < cap_min and entries + exits == cap_min
          and cap_min < 500, f"entries {entries}, exits {exits}")
    _clk.t += 61.0
    check("O5. ...the minute clears, entries may go again", b._order_gate("E"))


def tO6_silent_stall():
    class NoPos(ExitAngel):
        pos_ok = True

        def position(self):
            if not self.pos_ok:
                raise ConnectionError("position timed out")
            return super().position()
    api = NoPos([("cancelled", 0, 0.0)], netqty=130)
    api.raise_place = True
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    api.pos_ok = False
    for _ in range(120):
        _clk.t += 1.0
        e.close(pos, "stop_loss")
    check("O6. exit pending and the position book unreadable: one alert, nothing sent",
          len(e.notifier.msgs) == 1 and api.placed() == 1, f"{len(e.notifier.msgs)} alerts")
    api = ExitAngel([("open", 0, 0.0)], netqty=130)
    api.sticky = True
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    for _ in range(300):
        _clk.t += 1.0
        e.close(pos, "stop_loss")
    n = len(e.notifier.msgs)
    check("O6. an exit order that will not cancel: one alert after ~30 s, not one per cancel",
          n == 1 and api.placed() == 1, f"{n} alerts")
    logs = []
    e.log = logs.append
    e._running.set()
    e.feed.tick_event.set()
    e.run(stop_predicate=lambda: True)
    check("O6. run()'s square-off that cannot close says so: log line and alert",
          e.book.count() == 1 and any("SQUARE-OFF INCOMPLETE" in x for x in logs)
          and any("SQUARE-OFF INCOMPLETE" in m for m in e.notifier.msgs), str(logs[-1:]))


def tO7_ab07():
    import run_bot
    real = run_bot.time
    run_bot.time = _clk
    try:
        def go(status):
            api = ExitAngel()
            api.seed(status, tag="", side="BUY")
            b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
            try:
                run_bot.refuse_working_orders(b, log=lambda *_: None)
                return "allowed"
            except SystemExit:
                return "refused"
        check("O7. a cancelled AMO ('cancelled after market order') does not refuse the start; "
              "'cancel pending' still does",
              go("cancelled after market order") == "allowed" and go("cancel pending") == "refused"
              and go("cancel after market order req received") == "refused")
    finally:
        run_bot.time = real


def tO8_late_reason():
    api = ExitAngel([("open", 0, 0.0)], netqty=130)
    api.sticky = True
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    api.set("1", "complete", 130, 80.0)
    _clk.t += 5.0
    e.close(pos, "squareoff")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("O8. a stop_loss exit that fills late is booked as stop_loss, cooldown set, "
          "whichever close() finds it", r.get("exit_reason") == "stop_loss"
          and pos.symbol in e._symbol_cooldown, str(r.get("exit_reason")))


def tO9_exit_all():
    api = ExitAngel([("open", 0, 0.0), ("complete", 130, 104.0)], netqty=130)
    api.sticky = True
    e, pos = live_engine(api, market=True)
    e.close(pos, "stop_loss")
    e._mark_of = lambda p: 105.0
    e.book.time_to_close = lambda *_: 300.0
    e.manual_exit_all()
    _clk.t += 1.0
    e.manage()
    kept = e.exit_all_requested
    api.set("1", "cancelled", 0)
    for _ in range(5):
        _clk.t += 1.0
        e.manage()
    check("O9. exit-all held by a pending exit is kept, and closes the position once it can",
          kept and e.book.count() == 0 and not e.exit_all_requested and api.placed() == 2,
          f"kept {kept}, book {e.book.count()}, placed {api.placed()}")


def tO10_paced():
    api = ExitAngel()
    api.seed("complete")
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b._status("u1", "1")
    b._last_call["individual_order_details"] += 600.0     # a clock step back of 600 s
    t0 = _clk.t
    b._status("u1", "1")
    check("O10. a 600 s clock step does not make the next poll sleep 600 s",
          _clk.t - t0 <= LiveBroker.POLL_GAP["individual_order_details"] + 1e-9,
          f"slept {_clk.t - t0:.1f}s")


def tO11_stop_timeout():
    e, pos = engine([])
    e.L.exit_safety_fixes = True
    seen = []
    e._thread = SimpleNamespace(join=lambda timeout=None: seen.append(timeout))
    e.stop()
    check("O11. stop() waits long enough for a slow live square-off (>= 45 s)",
          seen and seen[0] >= 45.0, str(seen))


def tOadv1_double_adopt():
    # A 3-lot entry fills 65 and stays working after its cancel; 65 more fill
    # while it works; the stop's SELL 65 has its place call time out after
    # Angel filled it; then the entry order ends cancelled with 130 filled.
    api = ExitAngel([("open", 65, 110.5)])
    api.sticky = True
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.notifier = Notes()
    e.L.market_orders = True
    e.enter(_cand(), _plan(), _struct, None, lots=3)
    e.book.time_to_close = lambda *_: 300.0
    mark = [111.0]
    e._mark_of = lambda p: mark[0]
    _clk.t += 2.5
    e.manage()
    api.set("1", "open", 130, 110.5)
    api.script = [("complete", 65, 85.0)] * 3
    api.raise_place = True
    mark[0] = 85.0
    low = 0
    for _ in range(8):
        _clk.t += 0.5
        e.manage()
        low = min(low, api.true_net())
    mid = api.true_net(), _sells(api)
    api.sticky = False
    api.set("1", "cancelled", 130, 110.5)
    for _ in range(10):
        _clk.t += 1.0
        e.manage()
        low = min(low, api.true_net())
    held = sum(p.qty for p in e.book.snapshot())
    check("O-ADV-1. fills of a working entry are not read as the position a lost stop SELL "
          "closed, nor taken on twice: no second SELL over the filled one, the late 65 adopted "
          "once, the account never short and level with the engine",
          low >= 0 and mid == (65, 1) and api.true_net() == held == 0 and _sells(api) == 2
          and not e._pending_entries and e.journal.rows and e.journal.rows[0]["exit"] == 85.0,
          f"low {low}, mid {mid}, account {api.true_net()}, engine {held}, sells {_sells(api)}")
    # The entry poll takes on only what the account holds beyond the engine.
    api = ExitAngel()
    api._add({"variety": "NORMAL", "tradingsymbol": SYM, "transactiontype": "BUY",
              "quantity": "130", "ordertype": "LIMIT"}, "complete", 130, 110.5)
    api._add({"variety": "NORMAL", "tradingsymbol": SYM, "transactiontype": "SELL",
              "quantity": "130", "ordertype": "LIMIT"}, "complete", 130, 85.0)
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.notifier = Notes()
    taken = []
    rec = e._pend_entry({"order_id": "1", "uid": "u1", "filled": True, "qty": 65}, SYM,
                        lambda q: taken.append(q) or True, 1)
    e._follow_entry(rec)
    check("O-ADV-1. ...an entry order that ends with 65 more filled, already sold at the "
          "account: nothing taken on, entries halt with the account's real position",
          not taken and e.book.count() == 0 and not e._pending_entries and e.halted
          and "broker shows 0" in e.halt_reason, f"taken {taken}, {e.halt_reason}")


def tOobs1_lost_exit_price():
    api = ExitAngel([("complete", 130, 108.0)], netqty=130)
    api.raise_place = True
    e, pos = live_engine(api, market=True)
    e._mark_of = lambda p: 110.0
    e.close(pos, "stop_loss")
    for _ in range(3):
        _clk.t += 1.0
        e.close(pos, "stop_loss")
    api.netqty = None                                  # the position book catches up
    _clk.t += 3.0
    e.close(pos, "stop_loss")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("O-obs1. a lost exit settled once the books agree is booked at the order book's "
          "fill (108), not the mark (110)",
          e.book.count() == 0 and _sells(api) == 1 and r.get("exit") == 108.0
          and r.get("exit_order") == "reconciled", str(r.get("exit")))
    api = ExitAngel([("complete", 130, 108.0)])
    api.raise_place = True
    e, pos = live_engine(api, market=True)
    e._mark_of = lambda p: 110.0
    e.close(pos, "stop_loss")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("O-obs1. ...settled at once (books current): 108 too",
          e.book.count() == 0 and r.get("exit") == 108.0, str(r.get("exit")))
    api = ExitAngel([("complete", 130, 108.0)])
    api.raise_place = True
    e, pos = live_engine(api, market=True)
    api.hold(30)
    api.hold(30, side="SELL")                          # an earlier SELL no id of ours explains
    e._mark_of = lambda p: 110.0
    e.close(pos, "stop_loss")
    r = e.journal.rows[-1] if e.journal.rows else {}
    check("O-obs1. ...unknown SELL rows that do not add up to what went: the mark, as before",
          e.book.count() == 0 and r.get("exit") == 110.0, str(r.get("exit")))


def tOadv2_unit_stop_timeout():
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    e, pos = engine([])
    e.L.exit_safety_fixes = True
    e.broker = SimpleNamespace(confirm_timeout=6.0)
    e.book.count = lambda: 4
    seen = []
    e._thread = SimpleNamespace(join=lambda timeout=None: seen.append(timeout))
    e.stop()
    need = seen[0] + 20.0                              # run_bot's shutdown after the join
    got = {}
    for name in ("trading-engine.service", "trading-engine-dev.service"):
        txt = open(os.path.join(root, "deployment", name)).read()
        m = re.search(r"^TimeoutStopSec=(\d+)", txt, re.M)
        got[name] = int(m.group(1)) if m else 0
    check("O-ADV-2. the systemd units give a live square-off (4 open positions) time to "
          "finish before SIGKILL", all(v >= need for v in got.values()), f"{got} need {need}")


def tOadv3_miss_gap():
    api = ExitAngel([("cancelled", 0, 0.0)] * 80)
    e, pos = live_engine(api, market=False)
    e.log = lambda *_: None
    t0, sends = _clk.t, []
    while _clk.t - t0 < 20:
        n = api.placed()
        e.close(pos, "stop_loss")
        if api.placed() > n:
            sends.append(_clk.t - t0)
        _clk.t += 0.25
    gaps = [b - a for a, b in zip(sends, sends[1:])]
    check("O-ADV-3. an IOC exit that misses (cancelled, 0 filled) is tried again about every "
          "1 s, not backed off to 5 s like a refusal",
          len(sends) >= 15 and gaps and max(gaps) <= 1.25 and pos.exit_attempts >= 15,
          f"{len(sends)} sends, longest gap {max(gaps) if gaps else None}")


class PropAngel(ModeAngel):
    """Random answers, fills, late fills and a lagging position book. Records
    every SELL sent over more than is held, and any moment the account is net
    short."""

    def __init__(self, rng, lag, sticky_p):
        super().__init__()
        self.rng, self.lag, self.sticky_p = rng, lag, sticky_p
        self.hist, self.bad = [], []

    def _seen(self, what):
        net = self.true_net()
        self.hist.append((_clk.t, net))
        if net < 0:
            self.bad.append(f"{what}: net {net}")

    def hold(self, qty, side="BUY"):
        super().hold(qty, side)
        self.hist.append((-1e18, self.true_net()))

    def placeOrderFullResponse(self, params):
        rng, qty = self.rng, int(params["quantity"])
        working = sum(int(o["params"]["quantity"]) - o["filled"] for o in self.orders.values()
                      if o["status"] == "open" and o["params"]["transactiontype"] == "SELL")
        if params["transactiontype"] == "SELL" and qty + working > self.true_net():
            self.bad.append(f"SELL {qty} sent with {self.true_net()} held, {working} working")
        self.modes = [rng.choice(["ok", "ok", "ok", "null", "uid", "raise", "lost", "refuse"])]
        part = (qty // 2) if qty >= 2 else 0
        self.script = [rng.choice([("complete", qty, 99.0), ("cancelled", part, 99.0),
                                   ("cancelled", 0, 0.0), ("open", 0, 0.0)])]
        try:
            return super().placeOrderFullResponse(params)
        finally:
            self._seen("place")

    def cancelOrder(self, oid, variety):
        o = self.orders.get("u" + str(oid))
        if o and o["status"] == "open" and self.rng.random() < self.sticky_p:
            self.cancels.append((oid, variety))
            return {"status": True, "data": {"orderid": oid}}
        if o and o["status"] == "open" and self.rng.random() < 0.5:
            o["status"], o["filled"], o["avg"] = "complete", int(o["params"]["quantity"]), 98.0
            self._seen("late fill")
            return {"status": True, "data": {"orderid": oid}}
        r = super().cancelOrder(oid, variety)
        return r

    def individual_order_details(self, uid):
        if self.rng.random() < 0.15:
            return None                                   # the status endpoint hiccups
        return super().individual_order_details(uid)

    def position(self):
        at = _clk.t - self.lag
        net = next((n for t, n in reversed(self.hist) if t <= at), self.hist[0][1])
        return {"status": True, "data": [{"tradingsymbol": SYM, "netqty": str(net)}]}


def tP_property():
    import random
    bad, unsettled, runs = [], 0, int(os.environ.get("PROP_RUNS", "300"))
    for seed in range(runs):
        rng = random.Random(seed)
        api = PropAngel(rng, lag=rng.choice([0.0, 1.5, 4.0]), sticky_p=rng.choice([0.0, 0.3]))
        e, pos = live_engine(api, market=rng.random() < 0.5)
        for _ in range(400):
            if not e._in_book(pos):
                break
            e.close(pos, "stop_loss")
            _clk.t += rng.choice([0.1, 0.5, 1.0, 3.0])
        held = sum(p.qty for p in e.book.snapshot())
        if api.bad:
            bad.append((seed, api.bad[:2]))
        elif not e._in_book(pos) and api.true_net() != 0:
            bad.append((seed, f"engine flat, account holds {api.true_net()}"))
        elif e._in_book(pos) and not pos.pending_exit_oid and not pos.pending_exit_tag \
                and held != api.true_net():
            bad.append((seed, f"engine holds {held}, account {api.true_net()}"))
        unsettled += e._in_book(pos)
    check(f"P. property: {runs} random runs of null answers, raised places, refusals, partial "
          f"and late fills and a lagging position book never sell more than is held, never "
          f"leave the account short, and agree with it at the end",
          not bad, f"{len(bad)} bad, first {bad[:3]}; {unsettled} still open")


def tOsq_pending_entry():
    """Shutdown square-off with an entry order still being followed: an empty
    book is not the end - the entry fills during the square-off and that fill
    is taken on and sold, not left at the broker (reverify O, 5 Oct 2026)."""
    class LateFill(ExitAngel):
        def individual_order_details(self, uid):
            o = self.orders.get(uid)
            if o is not None and o["id"] == "1" and _clk.t >= self.fill_at and o["status"] == "open":
                o["status"], o["filled"], o["avg"] = "cancelled", 65, 111.0
            return super().individual_order_details(uid)
    api = LateFill([("open", 0, 0.0), ("complete", 65, 109.0)])
    api.sticky = True
    api.fill_at = _clk.t + 30.0        # after enter()'s own confirm window
    b = LiveBroker(api, CostModel(), product="INTRADAY", confirm_fills=True)
    b.exit_safety_fixes = True
    e = _empty_engine(b)
    e.L.market_orders = True
    e.enter(_cand(), _plan(), _struct, None)
    pending = bool(e._pending_entries) and e.book.count() == 0
    logs = []
    e.log = logs.append
    e._running.set()
    e.feed.tick_event.set()
    e.run(stop_predicate=lambda: True)
    check("square-off waits for a pending entry: its late fill is taken on and sold, the account "
          "ends flat", pending and api.true_net() == 0 and e.book.count() == 0
          and not any("SQUARE-OFF INCOMPLETE" in x for x in logs),
          f"account {api.true_net()}, book {e.book.count()}, pending {len(e._pending_entries)}")


_O_TESTS = [("O1", tO1_null_answer), ("O2", tO2_raise_lagging_book), ("O3", tO3_unknown_entry_lag),
            ("O4", tO4_entry_late_fill), ("O5", tO5_backoff_budget), ("O6", tO6_silent_stall),
            ("O7", tO7_ab07), ("O8", tO8_late_reason), ("O9", tO9_exit_all),
            ("O10", tO10_paced), ("O11", tO11_stop_timeout), ("P", tP_property),
            ("O-ADV-1", tOadv1_double_adopt), ("O-obs1", tOobs1_lost_exit_price),
            ("O-ADV-2", tOadv2_unit_stop_timeout), ("O-ADV-3", tOadv3_miss_gap),
            ("O-SQ", tOsq_pending_entry)]


try:
    for _name, _fn in [("1", t1_golden), ("2", t2_pending_ioc), ("3", t3_place_raises),
                       ("3b", t3b_structure_raises), ("4", t4_late_fill), ("5", t5_sign),
                       ("6", t6_variety), ("7", t7_partial_halt), ("8", t8_adopt),
                       ("8b", t8b_entry_may_work), ("9", t9_start_guard), ("10", t10_bucket),
                       ("12", t12_day), ("defaults", t13_flags_default)]:
        guarded(f"exit safety test {_name}", _fn)
    section("6. Review group O: live order paths")
    for _name, _fn in _O_TESTS:
        guarded(f"review test {_name}", _fn)
finally:
    _X.time, _IE.time = _real_time

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
