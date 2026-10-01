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

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
