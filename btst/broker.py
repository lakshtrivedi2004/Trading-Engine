"""Orders for the BTST strategy: Angel One (live) or simulated (paper), one interface.

Live orders are CARRYFORWARD (NRML) - a BTST position is held overnight, and
an INTRADAY (MIS) one would be squared off by the broker the same afternoon.
"Market" is a marketable limit, the touch +/- protection_pct, IOC: a plain
MARKET order is refused for API orders on NSE F&O (NSE/FAOP/69296), as
arb/execution.py does it. The target is a LIMIT DAY sell: Indian exchanges
cancel every order at the close, so the strategy places it again each morning -
live, as an AMO after the close, so it is at the exchange for the next open and
an opening spike fills it. Angel's order APIs take variety NORMAL, STOPLOSS or
ROBO only - no AMO (SmartAPI docs, Orders: "All the orders placed after market
hours will be treated as AMO orders", sent to the exchange at 09:00): an AMO is
a NORMAL order sent after the close, and is modified and cancelled as NORMAL
(tried on Angel, 3 Nov 2026). The desk still records it as variety AMO. Sent
to the exchange, an AMO gets a new order id (its uid stays): cancels and
modifies first read the id now by the uid (4 Nov 2026).
Every fill is read back from Angel (individual_order_details), never assumed.
An exchange refuses an order above the index's freeze quantity (config/btst.json
"freeze_qty" - NSE from 2 Nov 2026: NIFTY 1,800 = 27 lots of 65): a bigger one
goes in as several orders of the most whole lots allowed, recorded as one
order with its parts in `legs` - its state, cancel and modify cover them all
(owner, 9 Nov 2026). At or under the freeze nothing changes.
"""
import math
import time

from arb.execution import ip_refused
from btst.logbook import fmt

FINAL = ("complete", "cancelled", "rejected")


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _state(row):
    if not isinstance(row, dict) or not row:
        return None
    status = str(row.get("orderstatus") or row.get("status") or "").lower()
    # an AMO Angel holds, not yet sent: "open" with no exchange time (7 Nov 2026), or Angel's AMO status
    waiting = status.startswith("after market") or (status == "open" and "exchtime" in row
                                                     and not row.get("exchtime") and not row.get("exchorderupdatetime"))
    return {"status": status, "final": status in FINAL or status.startswith("cancelled"),
            "filled": int(_num(row.get("filledshares"))), "avg": _num(row.get("averageprice")),
            "text": str(row.get("text") or ""), "not_sent": waiting}


def _api_variety(variety):
    """The variety an order API is sent: an AMO (or none) is NORMAL."""
    return variety if variety in ("STOPLOSS", "ROBO") else "NORMAL"


class AngelBtstBroker:
    mode = "live"

    def __init__(self, api, protection_pct=2.5, product="CARRYFORWARD", confirm_sec=8.0,
                 log=print, sleep=time.sleep, clock=time.time, freeze_qty=None):
        self.api, self.protection_pct, self.product = api, float(protection_pct), product
        self.confirm_sec, self.log, self.sleep, self.clock = float(confirm_sec), log, sleep, clock
        self.freeze = {str(k).upper(): int(v) for k, v in (freeze_qty or {}).items()}

    def quote(self, c):
        """{ltp, high}: the last price and the exchange's day high, or None."""
        try:
            r = self.api.ltpData(c["exchange"], c["symbol"], c["token"]) or {}
            d = (r.get("data") or {}) if isinstance(r, dict) else {}
            v = _num(d.get("ltp"))
            if v <= 0:
                self.log(fmt("WARN", "broker", f"LTP {c['symbol']}: no price in the answer "
                                               f"({(r or {}).get('message') if isinstance(r, dict) else r})"))
                return None
            return {"ltp": v, "high": _num(d.get("high"))}
        except Exception as e:
            self.log(fmt("WARN", "broker", f"LTP {c['symbol']} failed: {e!r}"))
            return None

    def ltp(self, c):
        q = self.quote(c)
        return q["ltp"] if q else None

    def funds(self):
        from run_bot import live_funds
        v, src = live_funds(self.api)
        self.log(fmt("INFO" if v else "WARN", "broker", f"funds Rs {v:,.0f} ({src})"))
        return v

    def net_qty(self, c):
        """The account's net quantity in the contract, or None if unreadable."""
        try:
            r = self.api.position() or {}
        except Exception as e:
            self.log(fmt("WARN", "broker", f"positions unreadable: {e!r}"))
            return None
        if isinstance(r, dict) and r.get("status") is False:
            self.log(fmt("WARN", "broker", f"positions refused: {r.get('message')}"))
            return None
        rows = (r.get("data") or []) if isinstance(r, dict) else []
        return sum(int(_num(p.get("netqty"))) for p in rows if str(p.get("tradingsymbol")) == c["symbol"])

    def _params(self, c, side, qty, price, duration, variety="NORMAL"):
        return {"variety": variety, "tradingsymbol": c["symbol"], "symboltoken": c["token"],
                "transactiontype": side, "exchange": c["exchange"], "ordertype": "LIMIT",
                "producttype": self.product, "duration": duration, "price": f"{price:.2f}",
                "squareoff": "0", "stoploss": "0", "quantity": str(int(qty))}

    def _send(self, params):
        self.log(fmt("TRADE", "broker", f"send {params['variety']} {params['transactiontype']} "
                                        f"{params['tradingsymbol']} ({params['symboltoken']}, {params['exchange']}) "
                                        f"x{params['quantity']} LIMIT {params['price']} {params['duration']} "
                                        f"{params['producttype']}"))
        try:
            r = self.api.placeOrderFullResponse(params)
        except Exception as e:
            self.log(fmt("ERROR", "broker", f"place failed: {e!r}"))
            return None, None, f"place failed: {e}"
        data = (r or {}).get("data") if isinstance(r, dict) else None
        data = data if isinstance(data, dict) else {}
        oid, uid = data.get("orderid"), data.get("uniqueorderid")
        if not oid:
            why = (r or {}).get("message") if isinstance(r, dict) else r
            if ip_refused(r):
                why = f"refused - this machine's IP is not registered with Angel (AG7002): {why}"
            self.log(fmt("ERROR", "broker", f"order refused: {why} (answer {r})"))
            return None, uid, f"refused: {why}"
        self.log(fmt("TRADE", "broker", f"accepted: order {oid} (uid {uid})"))
        return str(oid), str(uid or ""), None

    # ── above the freeze quantity: one order in several ─────────────────────
    def _slices(self, c, qty):
        """The quantities `qty` goes in as: itself, or - above the index's
        freeze quantity - the most whole lots allowed per order, then the rest."""
        qty, lot = int(qty), int(c.get("lot_size") or 1)
        per = self.freeze.get(str(c.get("index") or "").upper(), 0) // lot * lot
        if per <= 0 or qty <= per:
            return [qty]
        return [per] * (qty // per) + ([qty % per] if qty % per else [])

    @staticmethod
    def _joined(legs, **fields):
        """One order of its parts: the ids joined, each part kept in `legs`."""
        sent = [o for o in legs if o.get("order_id")]
        return dict(legs[0], order_id=" + ".join(o["order_id"] for o in sent), uid=" + ".join(o.get("uid") or ""
                    for o in sent), legs=[{k: o.get(k) for k in ("order_id", "uid", "qty", "filled", "avg_price",
                                                                 "status") if k in o} for o in sent], **fields)

    def _sliced(self, one, c, qty, price):
        """A resting order above the freeze: `one` for each slice, all at the
        price. All or none - a slice refused cancels those already in, so a
        target is never only partly at the exchange."""
        cut, legs = self._slices(c, qty), []
        for n in cut:
            legs.append(one(c, n, price))
            if not legs[-1].get("ok"):
                break
        bad = legs[-1]
        if bad.get("ok"):
            self.log(fmt("TRADE", "broker", f"{bad['side']} {c['symbol']} x{int(qty)} @ {price:.2f}: {len(legs)} orders "
                                            f"of at most {cut[0]} (the freeze quantity)"))
            return self._joined(legs, qty=int(qty))
        for o in legs[:-1]:
            st = self.cancel(o)
            if st and st.get("filled"):
                self.log(fmt("ERROR", "broker", f"order {o['order_id']} filled {st['filled']} before its cancel"))
        self.log(fmt("ERROR", "broker", f"slice {len(legs)} of {len(cut)} refused ({bad.get('error')}) - "
                                        f"the {len(legs) - 1} already in cancelled"))
        return dict(bad, qty=int(qty), error=f"slice {len(legs)} of {len(cut)}: {bad.get('error')}")

    def _market_sliced(self, c, side, qty, per):
        """Above the freeze: IOCs one after another, each at most `per` at a
        fresh price; what one leaves unfilled goes in the next. It stops at one
        that fills nothing (refused, no price, nothing there)."""
        legs, left = [], qty
        while left > 0 and len(legs) < qty // per + 3:
            legs.append(self._market(c, side, min(left, per)))
            if not legs[-1].get("filled"):
                break
            left -= int(legs[-1]["filled"])
        done = qty - left
        avg = sum(float(o["avg_price"]) * int(o["filled"]) for o in legs if o.get("filled")) / done if done else 0.0
        self.log(fmt("TRADE" if not left else "WARN", "broker", f"{side} {c['symbol']} x{qty} in {len(legs)} orders "
                                                                f"of at most {per} (the freeze quantity): filled {done}"
                                                                + (f" @ {avg:.2f}" if done else "")))
        last = legs[-1]
        return self._joined(legs, qty=qty, ok=done > 0, filled=done, avg_price=round(avg, 4) if done else
                            last.get("avg_price"), status="complete" if not left else last.get("status"),
                            error=None if done else last.get("error"))

    def _legs_state(self, legs):
        """A sliced order's state: filled and the average over its parts; final
        when every part is, complete when every part is; not sent if a resting
        part is not."""
        sts = [self.order_state(o) for o in legs]
        if any(s is None for s in sts):
            return None
        live = [s for s in sts if not s["final"]]
        short = [s for s in sts if s["status"] != "complete"]
        filled = sum(s["filled"] for s in sts)
        return {"status": live[0]["status"] if live else (short[0]["status"] if short else "complete"),
                "final": not live, "filled": filled,
                "avg": round(sum(s["avg"] * s["filled"] for s in sts) / filled, 4) if filled else 0.0,
                "text": "; ".join(f"{o.get('order_id')}: {s['text']}" for o, s in zip(legs, sts) if s["text"]),
                "not_sent": any(s.get("not_sent") for s in live)}

    def order_state(self, order):
        if order.get("legs"):
            return self._legs_state(order["legs"])
        row = None
        uid, oid = order.get("uid"), order.get("order_id")
        try:
            if uid:
                r = self.api.individual_order_details(uid) or {}
                row = r.get("data") if isinstance(r, dict) else None
            if not row and oid:
                book = self.api.orderBook() or {}
                row = next((x for x in (book.get("data") or []) if str(x.get("orderid")) == str(oid)), None)
        except Exception as e:
            self.log(fmt("WARN", "broker", f"order {oid} state unreadable: {e!r}"))
            row = None
        return _state(row)

    def _market(self, c, side, qty):
        cut = self._slices(c, qty)
        if len(cut) > 1:
            return self._market_sliced(c, side, int(qty), cut[0])
        ltp = self.ltp(c)
        if not ltp:
            return {"ok": False, "error": "no LTP for the contract", "filled": 0}
        pct = self.protection_pct / 100.0
        tick = float(c.get("tick") or 0.05)
        px = (math.ceil(ltp * (1 + pct) / tick - 1e-9) * tick if side == "BUY"
              else max(tick, math.floor(ltp * (1 - pct) / tick + 1e-9) * tick))
        oid, uid, err = self._send(self._params(c, side, qty, px, "IOC"))
        order = {"kind": "", "side": side, "order_id": oid, "uid": uid, "qty": int(qty),
                 "price_sent": round(px, 2), "ltp_at_send": ltp, "duration": "IOC"}
        if err:
            return dict(order, ok=False, error=err, filled=0, status="refused")
        st, end = None, self.clock() + self.confirm_sec
        while self.clock() < end:
            st = self.order_state(order)
            if st and st["final"]:
                break
            self.sleep(0.5)
        if not st or not st["final"]:
            self.log(fmt("WARN", "broker", f"order {oid} not final after {self.confirm_sec:g}s "
                                           f"({(st or {}).get('status', 'unreadable')}) - cancelling the rest"))
            try:
                self.api.cancelOrder(oid, "NORMAL")
            except Exception as e:
                self.log(fmt("WARN", "broker", f"cancel {oid} failed: {e!r}"))
            self.sleep(1.0)
            st = self.order_state(order)
        if not st:
            self.log(fmt("ERROR", "broker", f"order {oid}: status unknown - check the Angel app"))
            return dict(order, ok=False, error="status unknown - check the Angel app", filled=0,
                        status="unknown")
        self.log(fmt("TRADE" if st["filled"] else "WARN", "broker",
                     f"order {oid} {st['status']}: filled {st['filled']}/{int(qty)} @ {st['avg']}"
                     + (f" - {st['text']}" if st["text"] else "")))
        return dict(order, ok=st["filled"] > 0, filled=st["filled"], avg_price=st["avg"] or ltp,
                    status=st["status"], error=None if st["filled"] > 0 else (st["text"] or st["status"]))

    def buy_market(self, c, qty):
        return self._market(c, "BUY", qty)

    def sell_market(self, c, qty):
        return self._market(c, "SELL", qty)

    def place_limit_sell(self, c, qty, price):
        if len(self._slices(c, qty)) > 1:
            return self._sliced(self.place_limit_sell, c, qty, price)
        oid, uid, err = self._send(self._params(c, "SELL", qty, price, "DAY"))
        order = {"side": "SELL", "order_id": oid, "uid": uid, "qty": int(qty), "price_sent": round(price, 2),
                 "duration": "DAY"}
        if err:
            return dict(order, ok=False, error=err, status="refused")
        self.sleep(1.0)
        st = self.order_state(order)
        if st and st["status"] == "rejected":
            self.log(fmt("ERROR", "broker", f"target {oid} rejected by the exchange: {st['text']}"))
            return dict(order, ok=False, error=st["text"] or "rejected", status="rejected")
        self.log(fmt("TRADE", "broker", f"target {oid} resting: {(st or {}).get('status', 'open')}"))
        return dict(order, ok=True, status=(st or {}).get("status", "open"), error=None)

    def place_limit_buy(self, c, qty, price):
        """A resting LIMIT buy (DAY, CARRYFORWARD): the limit top-up at -53%."""
        if len(self._slices(c, qty)) > 1:
            return self._sliced(self.place_limit_buy, c, qty, price)
        oid, uid, err = self._send(self._params(c, "BUY", qty, price, "DAY"))
        order = {"side": "BUY", "order_id": oid, "uid": uid, "qty": int(qty), "price_sent": round(price, 2),
                 "duration": "DAY"}
        if err:
            return dict(order, ok=False, error=err, status="refused")
        self.sleep(1.0)
        st = self.order_state(order)
        if st and st["status"] in ("rejected", "cancelled"):
            self.log(fmt("ERROR", "broker", f"limit buy {oid} {st['status']}: {st['text']}"))
            return dict(order, ok=False, error=st["text"] or st["status"], status=st["status"])
        self.log(fmt("TRADE", "broker", f"limit buy {oid} resting: {(st or {}).get('status', 'open')}"))
        return dict(order, ok=True, status=(st or {}).get("status", "open"), error=None)

    def place_amo_sell(self, c, qty, price):
        """An after-market LIMIT sell for the next session (DAY, CARRYFORWARD):
        a NORMAL order sent after the close, which Angel holds as an AMO and
        sends to the exchange for the open. Recorded as variety AMO."""
        if len(self._slices(c, qty)) > 1:
            return self._sliced(self.place_amo_sell, c, qty, price)
        oid, uid, err = self._send(self._params(c, "SELL", qty, price, "DAY"))
        order = {"side": "SELL", "order_id": oid, "uid": uid, "qty": int(qty), "price_sent": round(price, 2),
                 "duration": "DAY", "variety": "AMO"}
        if err:
            return dict(order, ok=False, error=err, status="refused")
        self.sleep(1.0)
        st = self.order_state(order)
        if st and st["status"] in ("rejected", "cancelled"):
            self.log(fmt("ERROR", "broker", f"AMO {oid} {st['status']}: {st['text']}"))
            return dict(order, ok=False, error=st["text"] or st["status"], status=st["status"])
        self.log(fmt("TRADE", "broker", f"AMO {oid} accepted for the next open: {(st or {}).get('status', '?')}"))
        return dict(order, ok=True, status=(st or {}).get("status", "amo"), error=None)

    def _live_id(self, order):
        """The order's id at Angel now, read by its uid: an AMO gets a new one
        when Angel sends it to the exchange at 09:00, and the AMO's id is then
        "OrderId not found" (4 Nov 2026). The stored id if unreadable."""
        oid, uid = order.get("order_id"), order.get("uid")
        if not uid:
            return oid
        try:
            r = self.api.individual_order_details(uid) or {}
            row = r.get("data") if isinstance(r, dict) else None
        except Exception as e:
            self.log(fmt("WARN", "broker", f"order {oid}: its id now unreadable ({e!r}) - using {oid}"))
            return oid
        now = str(row.get("orderid") or "") if isinstance(row, dict) else ""
        if now and now != str(oid):
            self.log(fmt("INFO", "broker", f"order {oid} is {now} at the exchange now"))
            return now
        return oid

    def modify_sell(self, order, c, qty, price):
        """Move a resting LIMIT sell to `price` (the trailing target, the GIFT
        Nifty target on the AMO): Angel's modifyOrder - one order all along,
        never a second sell. Sliced: each part still open, at its own quantity."""
        if order.get("legs"):
            errs, n = [], 0
            for o in order["legs"]:
                st = self.order_state(o)
                if st and st["final"]:
                    continue
                n += 1
                r = self.modify_sell(dict(o, variety=order.get("variety"), duration=order.get("duration")), c,
                                     o["qty"], price)
                if not r.get("ok"):
                    errs.append(r.get("error"))
            if not n:
                return {"ok": False, "error": "none of its orders is open"}
            return {"ok": not errs, "error": "; ".join(errs) or None}
        oid = self._live_id(order)
        params = {"variety": _api_variety(order.get("variety")), "orderid": oid, "ordertype": "LIMIT",
                  "producttype": self.product, "duration": order.get("duration") or "DAY", "price": f"{price:.2f}",
                  "quantity": str(int(qty)), "tradingsymbol": c["symbol"], "symboltoken": c["token"],
                  "exchange": c["exchange"]}
        self.log(fmt("TRADE", "broker", f"modify {oid}: SELL {c['symbol']} x{int(qty)} LIMIT -> {price:.2f}"))
        try:
            r = self.api.modifyOrder(params)
        except Exception as e:
            self.log(fmt("ERROR", "broker", f"modify {oid} failed: {e!r}"))
            return {"ok": False, "error": f"modify failed: {e}"}
        if isinstance(r, dict) and r.get("status") is not False and (r.get("data") or r.get("status")):
            self.log(fmt("TRADE", "broker", f"modify {oid} accepted - target now {price:.2f}"))
            return {"ok": True, "error": None}
        why = (r or {}).get("message") if isinstance(r, dict) else r
        self.log(fmt("ERROR", "broker", f"modify {oid} refused: {why} (answer {r})"))
        return {"ok": False, "error": f"refused: {why}"}

    def cancel(self, order):
        """Cancel a resting order; its final state (it may have filled first).
        An AMO is cancelled as NORMAL, by its id at the exchange. Sliced: each
        part still open, then the state of them all."""
        if order.get("legs"):
            for o in order["legs"]:
                st = self.order_state(o)
                if not (st and st["final"]):
                    self.cancel(dict(o, variety=order.get("variety")))
            return self.order_state(order)
        oid, variety = self._live_id(order), _api_variety(order.get("variety"))
        try:
            r = self.api.cancelOrder(oid, variety)
            if isinstance(r, dict) and r.get("status") is False:
                self.log(fmt("WARN", "broker", f"cancel {oid} ({variety}) refused: {r.get('message')}"))
            else:
                self.log(fmt("INFO", "broker", f"cancel {oid} ({variety}) sent"))
        except Exception as e:
            self.log(fmt("WARN", "broker", f"cancel {oid} ({variety}) failed: {e!r}"))
        end = self.clock() + self.confirm_sec
        st = None
        while self.clock() < end:
            st = self.order_state(order)
            if st and st["final"]:
                self.log(fmt("INFO", "broker", f"cancel {oid}: {st['status']}, filled {st['filled']}"))
                return st
            self.sleep(0.5)
        self.log(fmt("WARN", "broker", f"cancel {oid}: not final after {self.confirm_sec:g}s ({st})"))
        return st


class PaperBtstBroker:
    """Fills at the live price. A resting target fills at its price when the
    highest price traded since it was placed reaches it (btst/ticks.py: every
    websocket tick and the exchange's day high, so a touch between two looks
    still counts) - or at once, at the market, if placed below the market.
    Like the exchange, a DAY target dies at the day's close. Orders and the
    position live in this process: after a restart adopt() rebuilds the open
    job's from MongoDB."""
    mode = "paper"

    def __init__(self, ltp_fn, funds=1e9, log=print, watch=None, today=None):
        from arb.instruments import now_ist
        self.ltp_fn, self._funds, self.log, self.watch = ltp_fn, funds, log, watch
        self.today = today or (lambda: now_ist().date())
        self.orders, self.pos, self.n = {}, {}, 0
        self.run = format(int(time.time()) % 1_000_000, "x")    # ids unique across restarts

    def ltp(self, c):
        v = self.ltp_fn(c)
        return float(v) if v and v > 0 else None

    def funds(self):
        return self._funds

    def net_qty(self, c):
        return self.pos.get(c["symbol"], 0)

    def _new(self, c, side, qty, price, duration, oid=None, day=None):
        if oid is None:
            self.n += 1
            oid = f"PAPER-{self.run}-{self.n}"
        self.orders[oid] = {"symbol": c["symbol"], "contract": c, "side": side, "qty": int(qty),
                            "price": price, "status": "open", "filled": 0, "avg": 0.0, "duration": duration,
                            "day": day or self.today()}
        return oid

    def _fill(self, oid, px):
        o = self.orders[oid]
        o.update(status="complete", filled=o["qty"], avg=round(px, 2))
        self.pos[o["symbol"]] = self.pos.get(o["symbol"], 0) + (o["qty"] if o["side"] == "BUY" else -o["qty"])

    def _market(self, c, side, qty):
        ltp = self.ltp(c)
        if not ltp:
            self.log(fmt("ERROR", "broker", f"paper {side} {c['symbol']} x{qty}: no LTP - not filled"))
            return {"ok": False, "error": "no LTP for the contract", "filled": 0}
        oid = self._new(c, side, qty, ltp, "IOC")
        self._fill(oid, ltp)
        self.log(fmt("TRADE", "broker", f"paper {oid} {side} {c['symbol']} x{qty} filled @ {ltp} (the LTP)"))
        return {"side": side, "order_id": oid, "uid": oid, "qty": int(qty), "price_sent": ltp,
                "ltp_at_send": ltp, "duration": "IOC", "ok": True, "filled": int(qty), "avg_price": ltp,
                "status": "complete", "error": None}

    def buy_market(self, c, qty):
        return self._market(c, "BUY", qty)

    def sell_market(self, c, qty):
        return self._market(c, "SELL", qty)

    def place_limit_sell(self, c, qty, price):
        oid = self._new(c, "SELL", qty, price, "DAY")
        out = {"side": "SELL", "order_id": oid, "uid": oid, "qty": int(qty), "price_sent": price,
               "duration": "DAY", "ok": True, "status": "open", "error": None}
        ltp = self.ltp(c)
        if ltp and ltp >= price:                    # below the market: a limit sell fills at once, at the market
            self._fill(oid, ltp)
            self.log(fmt("TRADE", "broker", f"paper {oid} SELL {c['symbol']} x{qty} LIMIT {price:.2f}: the price "
                                            f"is already {ltp} - filled at once @ {ltp}"))
            return dict(out, status="complete")
        if self.watch is not None:
            self.watch.mark()
        self.log(fmt("TRADE", "broker", f"paper {oid} SELL {c['symbol']} x{qty} LIMIT {price:.2f} resting "
                                        f"(LTP {ltp}; fills when a trade reaches {price:.2f})"))
        return out

    def place_limit_buy(self, c, qty, price):
        oid = self._new(c, "BUY", qty, price, "DAY")
        out = {"side": "BUY", "order_id": oid, "uid": oid, "qty": int(qty), "price_sent": price,
               "duration": "DAY", "ok": True, "status": "open", "error": None}
        ltp = self.ltp(c)
        if ltp and ltp <= price:                    # above the market: a limit buy fills at once, at the market
            self._fill(oid, ltp)
            self.log(fmt("TRADE", "broker", f"paper {oid} BUY {c['symbol']} x{qty} LIMIT {price:.2f}: the price "
                                            f"is already {ltp} - filled at once @ {ltp}"))
            return dict(out, status="complete")
        if self.watch is not None:
            self.watch.mark_low()
        self.log(fmt("TRADE", "broker", f"paper {oid} BUY {c['symbol']} x{qty} LIMIT {price:.2f} resting "
                                        f"(LTP {ltp}; fills when a trade reaches {price:.2f})"))
        return out

    def modify_sell(self, order, c, qty, price):
        """Move the resting paper target to `price`; the highest price is
        counted again from now (a touch of the old, higher price never fills it)."""
        o = self.orders.get(order.get("order_id"))
        if o is None or o["status"] != "open":
            return {"ok": False, "error": f"no open paper order {order.get('order_id')}"}
        old, o["price"] = o["price"], float(price)
        if self.watch is not None:
            self.watch.mark()
        self.log(fmt("TRADE", "broker", f"paper {order.get('order_id')} target {old:.2f} -> {price:.2f}"))
        return {"ok": True, "error": None}

    def adopt(self, job):
        """After a restart: the open job's position and its resting target, from MongoDB."""
        c, p = job["contract"], job["position"]
        self.pos[c["symbol"]] = int(p["qty"])
        to = job.get("target_order") or {}
        note = "no resting target"
        if to.get("ok") and to.get("order_id") and to.get("status") not in FINAL:
            day = str(to.get("at") or job.get("trade_date") or "")[:10]
            from datetime import date
            try:
                d = date.fromisoformat(day)
            except ValueError:
                d = self.today()
            self._new(c, "SELL", to["qty"], float(job.get("target_price") or to["price_sent"]), "DAY",
                      oid=to["order_id"], day=d)
            if self.watch is not None and d == self.today():
                self.watch.track(c)             # the contract first: a new one would forget the mark
                self.watch.mark()
            note = f"target {to['order_id']} @ {job.get('target_price')} placed {d}"
        lt = job.get("limit_topup") or {}
        if lt.get("ok") and lt.get("order_id") and not lt.get("final") and not lt.get("booked"):
            from datetime import date
            try:
                d = date.fromisoformat(str(lt.get("at") or "")[:10])
            except ValueError:
                d = self.today()
            self._new(c, "BUY", lt["qty"], float(lt["price"]), "DAY", oid=lt["order_id"], day=d)
            if self.watch is not None and d == self.today():
                self.watch.track(c)
                self.watch.mark_low()
            note += f"; limit top-up {lt['order_id']} BUY x{lt['qty']} @ {lt['price']}"
        self.log(fmt("INFO", "broker", f"paper: adopted {job['job_id']} after the restart - {c['symbol']} "
                                       f"x{p['qty']}, {note} (a touch while the desk was down is not seen)"))

    def order_state(self, order):
        oid = order.get("order_id")
        o = self.orders.get(oid)
        if o is None:
            return None
        if o["status"] == "open" and o["side"] == "BUY" and o["duration"] == "DAY":
            if o["day"] < self.today():
                o["status"] = "cancelled"
                self.log(fmt("INFO", "broker", f"paper {oid} expired at the close of {o['day']} (a DAY order)"))
            else:
                ltp = self.ltp(o["contract"])
                low = self.watch.trough() if self.watch is not None else None
                best = min([x for x in (ltp, low) if x] or [float("inf")])
                if best <= o["price"]:
                    self._fill(oid, o["price"])
                    self.log(fmt("TRADE", "broker", f"paper {oid} limit buy filled @ {o['price']:.2f} "
                                                    f"(LTP {ltp}, lowest traded since placed {low})"))
        if o["status"] == "open" and o["side"] == "SELL":
            if o["duration"] == "DAY" and o["day"] < self.today():
                o["status"] = "cancelled"
                self.log(fmt("INFO", "broker", f"paper {oid} expired at the close of {o['day']} (a DAY order)"))
            else:
                ltp = self.ltp(o["contract"])
                peak = self.watch.peak() if self.watch is not None else None
                best = max([x for x in (ltp, peak) if x] or [0.0])
                if best >= o["price"]:
                    self._fill(oid, o["price"])
                    self.log(fmt("TRADE", "broker", f"paper {oid} target filled @ {o['price']:.2f} "
                                                    f"(LTP {ltp}, highest traded since placed {peak})"))
        return {"status": o["status"], "final": o["status"] in FINAL, "filled": o["filled"],
                "avg": o["avg"], "text": ""}

    def cancel(self, order):
        st = self.order_state(order)
        o = self.orders.get(order.get("order_id"))
        if o is not None and o["status"] == "open":
            o["status"] = "cancelled"
            self.log(fmt("INFO", "broker", f"paper {order['order_id']} cancelled"))
        return self.order_state(order) if o is not None else st
