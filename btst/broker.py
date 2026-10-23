"""Orders for the BTST strategy: Angel One (live) or simulated (paper), one interface.

Live orders are CARRYFORWARD (NRML) - a BTST position is held overnight, and
an INTRADAY (MIS) one would be squared off by the broker the same afternoon.
"Market" is a marketable limit, the touch +/- protection_pct, IOC: a plain
MARKET order is refused for API orders on NSE F&O (NSE/FAOP/69296), as
arb/execution.py does it. The target is a LIMIT DAY sell: Indian exchanges
cancel every order at the close, so the strategy places it again each morning.
Every fill is read back from Angel (individual_order_details), never assumed.
"""
import math
import time

from arb.execution import ip_refused

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
    return {"status": status, "final": status in FINAL or status.startswith("cancelled"),
            "filled": int(_num(row.get("filledshares"))), "avg": _num(row.get("averageprice")),
            "text": str(row.get("text") or "")}


class AngelBtstBroker:
    mode = "live"

    def __init__(self, api, protection_pct=2.5, product="CARRYFORWARD", confirm_sec=8.0,
                 log=print, sleep=time.sleep, clock=time.time):
        self.api, self.protection_pct, self.product = api, float(protection_pct), product
        self.confirm_sec, self.log, self.sleep, self.clock = float(confirm_sec), log, sleep, clock

    def ltp(self, c):
        try:
            r = self.api.ltpData(c["exchange"], c["symbol"], c["token"]) or {}
            v = _num((r.get("data") or {}).get("ltp"))
            return v if v > 0 else None
        except Exception as e:
            self.log(f"[btst] LTP {c['symbol']} failed: {e!r}")
            return None

    def funds(self):
        from run_bot import live_funds
        return live_funds(self.api)[0]

    def net_qty(self, c):
        """The account's net quantity in the contract, or None if unreadable."""
        try:
            r = self.api.position() or {}
        except Exception:
            return None
        if isinstance(r, dict) and r.get("status") is False:
            return None
        rows = (r.get("data") or []) if isinstance(r, dict) else []
        return sum(int(_num(p.get("netqty"))) for p in rows if str(p.get("tradingsymbol")) == c["symbol"])

    def _params(self, c, side, qty, price, duration):
        return {"variety": "NORMAL", "tradingsymbol": c["symbol"], "symboltoken": c["token"],
                "transactiontype": side, "exchange": c["exchange"], "ordertype": "LIMIT",
                "producttype": self.product, "duration": duration, "price": f"{price:.2f}",
                "squareoff": "0", "stoploss": "0", "quantity": str(int(qty))}

    def _send(self, params):
        try:
            r = self.api.placeOrderFullResponse(params)
        except Exception as e:
            return None, None, f"place failed: {e}"
        data = (r or {}).get("data") if isinstance(r, dict) else None
        data = data if isinstance(data, dict) else {}
        oid, uid = data.get("orderid"), data.get("uniqueorderid")
        if not oid:
            why = (r or {}).get("message") if isinstance(r, dict) else r
            if ip_refused(r):
                why = f"refused - this machine's IP is not registered with Angel (AG7002): {why}"
            return None, uid, f"refused: {why}"
        return str(oid), str(uid or ""), None

    def order_state(self, order):
        row = None
        uid, oid = order.get("uid"), order.get("order_id")
        try:
            if uid:
                r = self.api.individual_order_details(uid) or {}
                row = r.get("data") if isinstance(r, dict) else None
            if not row and oid:
                book = self.api.orderBook() or {}
                row = next((x for x in (book.get("data") or []) if str(x.get("orderid")) == str(oid)), None)
        except Exception:
            row = None
        return _state(row)

    def _market(self, c, side, qty):
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
            try:
                self.api.cancelOrder(oid, "NORMAL")
            except Exception:
                pass
            self.sleep(1.0)
            st = self.order_state(order)
        if not st:
            return dict(order, ok=False, error="status unknown - check the Angel app", filled=0,
                        status="unknown")
        return dict(order, ok=st["filled"] > 0, filled=st["filled"], avg_price=st["avg"] or ltp,
                    status=st["status"], error=None if st["filled"] > 0 else (st["text"] or st["status"]))

    def buy_market(self, c, qty):
        return self._market(c, "BUY", qty)

    def sell_market(self, c, qty):
        return self._market(c, "SELL", qty)

    def place_limit_sell(self, c, qty, price):
        oid, uid, err = self._send(self._params(c, "SELL", qty, price, "DAY"))
        order = {"side": "SELL", "order_id": oid, "uid": uid, "qty": int(qty), "price_sent": round(price, 2),
                 "duration": "DAY"}
        if err:
            return dict(order, ok=False, error=err, status="refused")
        self.sleep(1.0)
        st = self.order_state(order)
        if st and st["status"] == "rejected":
            return dict(order, ok=False, error=st["text"] or "rejected", status="rejected")
        return dict(order, ok=True, status=(st or {}).get("status", "open"), error=None)

    def cancel(self, order):
        """Cancel a resting order; its final state (it may have filled first)."""
        try:
            self.api.cancelOrder(order["order_id"], "NORMAL")
        except Exception as e:
            self.log(f"[btst] cancel {order.get('order_id')} failed: {e!r}")
        end = self.clock() + self.confirm_sec
        st = None
        while self.clock() < end:
            st = self.order_state(order)
            if st and st["final"]:
                return st
            self.sleep(0.5)
        return st


class PaperBtstBroker:
    """Fills at the live LTP; a resting target fills when the LTP reaches it."""
    mode = "paper"

    def __init__(self, ltp_fn, funds=1e9, log=print):
        self.ltp_fn, self._funds, self.log = ltp_fn, funds, log
        self.orders, self.pos, self.n = {}, {}, 0

    def ltp(self, c):
        v = self.ltp_fn(c)
        return float(v) if v and v > 0 else None

    def funds(self):
        return self._funds

    def net_qty(self, c):
        return self.pos.get(c["symbol"], 0)

    def _new(self, c, side, qty, price, duration):
        self.n += 1
        oid = f"PAPER-{self.n}"
        self.orders[oid] = {"symbol": c["symbol"], "contract": c, "side": side, "qty": int(qty),
                            "price": price, "status": "open", "filled": 0, "avg": 0.0}
        return oid

    def _fill(self, oid, px):
        o = self.orders[oid]
        o.update(status="complete", filled=o["qty"], avg=round(px, 2))
        self.pos[o["symbol"]] = self.pos.get(o["symbol"], 0) + (o["qty"] if o["side"] == "BUY" else -o["qty"])

    def _market(self, c, side, qty):
        ltp = self.ltp(c)
        if not ltp:
            return {"ok": False, "error": "no LTP for the contract", "filled": 0}
        oid = self._new(c, side, qty, ltp, "IOC")
        self._fill(oid, ltp)
        return {"side": side, "order_id": oid, "uid": oid, "qty": int(qty), "price_sent": ltp,
                "ltp_at_send": ltp, "duration": "IOC", "ok": True, "filled": int(qty), "avg_price": ltp,
                "status": "complete", "error": None}

    def buy_market(self, c, qty):
        return self._market(c, "BUY", qty)

    def sell_market(self, c, qty):
        return self._market(c, "SELL", qty)

    def place_limit_sell(self, c, qty, price):
        oid = self._new(c, "SELL", qty, price, "DAY")
        return {"side": "SELL", "order_id": oid, "uid": oid, "qty": int(qty), "price_sent": price,
                "duration": "DAY", "ok": True, "status": "open", "error": None}

    def order_state(self, order):
        o = self.orders.get(order.get("order_id"))
        if o is None:
            return None
        if o["status"] == "open" and o["side"] == "SELL":
            ltp = self.ltp(o["contract"])
            if ltp and ltp >= o["price"]:
                self._fill(order["order_id"], o["price"])
        return {"status": o["status"], "final": o["status"] in FINAL, "filled": o["filled"],
                "avg": o["avg"], "text": ""}

    def cancel(self, order):
        st = self.order_state(order)
        o = self.orders.get(order.get("order_id"))
        if o is not None and o["status"] == "open":
            o["status"] = "cancelled"
        return self.order_state(order) if o is not None else st
