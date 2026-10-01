"""Order execution: paper and live.

Arbitrage is only arbitrage if *every* leg fills. Neither broker here pretends
otherwise: a structure that fills partially is recorded as a broken structure
with its unwind cost, never as profit. The old bot booked the theoretical edge
straight to cash and never modelled a fill at all.
"""
import concurrent.futures
import math
import random
import threading
import time

from arb.costs import structure_cost, leg_cost
from datetime import datetime

from arb.instruments import IST, now_ist


class BrokerError(Exception):
    pass


def _scale_legs(opp, lots):
    """Expand an opportunity's per-lot legs to the traded size."""
    out = []
    for leg in opp.legs:
        l = dict(leg)
        l["qty"] = int(leg["qty"] * lots)
        l["quote_price"] = float(leg["price"])
        out.append(l)
    return out


class PaperBroker:
    """Simulated fills against the quotes actually displayed at detection time.

    Fills are optimistic by default: they assume the quote we saw is still there
    when our order lands. That assumption is the single biggest source of
    paper-to-live slippage, so it is made explicit and can be stressed with
    ``miss_prob`` rather than hidden.
    """
    mode = "paper"

    def __init__(self, cost_model, miss_prob=0.0, seed=None):
        self.cm = cost_model
        self.miss_prob = miss_prob
        self.rng = random.Random(seed)

    def place_structure(self, opp, lots):
        legs = _scale_legs(opp, lots)
        slip = self.cm.slippage_points()
        for l in legs:
            if self.miss_prob and self.rng.random() < self.miss_prob:
                l["filled"] = False
                l["fill_price"] = 0.0
                l["order_id"] = None
                continue
            # Crossing the spread costs us: buys fill above the ask, sells below
            # the bid, by the modelled slippage.
            l["filled"] = True
            l["fill_price"] = l["quote_price"] + (slip if l["side"] == "BUY" else -slip)
            l["order_id"] = f"PAPER-{int(time.time() * 1000) % 10**9}"
        return legs, []


class LiveBroker:
    """Real orders through Angel One SmartAPI.

    All legs are fired concurrently - sequential placement would let the market
    move between legs, which is precisely how an arbitrage turns into a directional
    position. Anything that fills while another leg does not is unwound at market.
    """
    mode = "live"

    def __init__(self, smart_connect, cost_model, product="CARRYFORWARD",
                 duration="IOC", exchange="NFO", confirm_fills=False,
                 confirm_timeout=6.0, log=lambda *_: None):
        self.api = smart_connect
        self.cm = cost_model
        self.product = product
        self.duration = duration
        self.exchange = exchange          # fallback only; legs carry their own
        # confirm_fills: ask the broker what actually happened before reporting
        # a leg as filled. The intraday engine needs this: an IOC order that is
        # ACCEPTED is not an order that FILLED, and a position the engine
        # believes it holds but does not is closed by a SELL - which, for a
        # stock under MIS, opens a real short nobody asked for.
        self.confirm_fills = confirm_fills
        self.confirm_timeout = float(confirm_timeout)
        self.log = log
        self._lock = threading.Lock()
        self._pace_lock = threading.Lock()
        self._last_call = {}

    # Polling pace per endpoint, in seconds between calls. Angel publishes 1/s
    # for orderBook and the position book; the old status loop asked every
    # 0.3 s and fell back to orderBook on each miss - 3/s against a 1/s cap,
    # on the engine thread, exactly when an order is in flight. Order status
    # has no figure we could confirm, so it is held to 2/s.
    POLL_GAP = {"orderBook": 1.1, "position": 1.1, "individual_order_details": 0.5}

    def _paced(self, name, *a):
        """Call api.<name>, waiting first if the last call was too recent."""
        with self._pace_lock:
            wait = self.POLL_GAP.get(name, 0.0) - (time.time() - self._last_call.get(name, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_call[name] = time.time()
        return getattr(self.api, name)(*a)

    # A plain MARKET order is refused for API orders on NSE F&O (NSE/FAOP/69296,
    # NSE retail-algo FAQ Q11) and Angel takes only limits on BFO. "At market"
    # is therefore a marketable limit: the touch plus market_protection_pct
    # (Angel's own MPP band for index options above Rs 50 is 2.5%), IOC, so it
    # fills now against the book or not at all. Cash legs get a 0.5% band.
    protection_pct = 2.5

    @staticmethod
    def _tick(px, instrument):
        if instrument != "EQ":
            return 0.05
        return 0.05 if px < 1000 else (0.10 if px < 5000 else (0.50 if px < 10000 else 1.0))

    def _protected(self, leg, price):
        eq = leg.get("instrument") == "EQ"
        pct = (0.5 if eq else self.protection_pct) / 100.0
        tick = self._tick(price, leg.get("instrument"))
        if leg["side"] == "BUY":
            return math.ceil(price * (1 + pct) / tick - 1e-9) * tick
        return max(tick, math.floor(price * (1 - pct) / tick + 1e-9) * tick)

    def _order_params(self, leg, price=None, market=False):
        if market and price and price > 0:
            price, market = self._protected(leg, float(price)), False
        return {
            "variety": "NORMAL",
            "tradingsymbol": leg["symbol"],
            "symboltoken": leg["token"],
            "transactiontype": leg["side"],
            # A SENSEX leg routes to BFO and a NIFTY leg to NFO; sending both to
            # one exchange would have every BSE order rejected.
            "exchange": leg.get("exchange", self.exchange),
            "ordertype": "MARKET" if market else "LIMIT",
            "producttype": self.product,
            "duration": "DAY" if market else self.duration,
            "price": "0" if market else f"{price:.2f}",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(int(leg["qty"])),
        }

    def _place_one(self, leg):
        try:
            oid = self.api.placeOrder(self._order_params(leg, leg["quote_price"]))
            return leg, oid, None
        except Exception as e:
            return leg, None, str(e)

    # ── confirmed single orders (the intraday engine) ───────────────────────
    _FINAL = ("complete", "cancelled", "rejected")

    def _status(self, uid, oid):
        """(status, filled_qty, avg_price, reason) from the broker, or None."""
        row = None
        if uid:
            try:
                r = self._paced("individual_order_details", uid) or {}
                row = r.get("data") if isinstance(r, dict) else None
            except Exception:
                row = None
        if not row:
            try:
                book = self._paced("orderBook") or {}
                row = next((x for x in (book.get("data") or [])
                            if str(x.get("orderid")) == str(oid)), None)
            except Exception:
                row = None
        if not row:
            return None
        return (str(row.get("status") or row.get("orderstatus") or "").lower(),
                int(float(row.get("filledshares") or 0)),
                float(row.get("averageprice") or 0.0),
                str(row.get("text") or ""))

    def _place_confirmed(self, leg):
        """Place one order and wait for its real outcome. The leg comes back with
        filled / qty / fill_price set from the broker, never from hope."""
        params = self._order_params(leg, leg.get("quote_price"), market=bool(leg.get("market")))
        leg["filled"], leg["fill_price"], leg["order_id"] = False, 0.0, None
        try:
            r = self.api.placeOrderFullResponse(params)
        except Exception as e:
            return f"{leg['symbol']}: place failed: {e}"
        data = (r or {}).get("data") if isinstance(r, dict) else None
        if not data or not data.get("orderid"):
            return f"{leg['symbol']}: order refused: {(r or {}).get('message') if isinstance(r, dict) else r}"
        oid, uid = data.get("orderid"), data.get("uniqueorderid")
        leg["order_id"] = oid
        st = None
        deadline = time.time() + self.confirm_timeout
        while time.time() < deadline:
            st = self._status(uid, oid)
            if st and st[0] in self._FINAL:
                break
            time.sleep(0.3)
        if not st or st[0] not in self._FINAL:
            # Still working after the timeout: pull it, then read what filled.
            try:
                self.api.cancelOrder(oid, "NORMAL")
            except Exception:
                pass
            end = time.time() + 3.0
            while time.time() < end:
                st = self._status(uid, oid)
                if st and st[0] in self._FINAL:
                    break
                time.sleep(0.3)
        if not st:
            leg["broker_status"] = "unknown"
            return (f"{leg['symbol']}: order {oid} status UNKNOWN - check the Angel app; "
                    f"treated as not filled")
        status, filled, avg, why = st
        leg["broker_status"], leg["reject_reason"] = status, why
        if filled > 0:
            leg["filled"], leg["qty"] = True, filled
            leg["fill_price"] = avg if avg > 0 else float(leg.get("quote_price") or 0.0)
            return None
        return f"{leg['symbol']}: {status or 'not filled'}{(' - ' + why) if why else ''}"

    def net_position(self, symbol):
        """Net quantity the broker shows in `symbol` today, or None if unknown.
        The last word when an order's own status cannot be read."""
        try:
            r = self._paced("position") or {}
        except Exception:
            return None
        if not isinstance(r, dict) or r.get("status") is False:
            return None
        q = 0
        for p in r.get("data") or []:
            if str(p.get("tradingsymbol")) == str(symbol):
                try:
                    q += int(float(p.get("netqty") or 0))
                except (TypeError, ValueError):
                    return None
        return q

    def place_structure(self, opp, lots):
        legs = _scale_legs(opp, lots)
        errors = []
        if self.confirm_fills:
            for leg in legs:
                err = self._place_confirmed(leg)
                if err:
                    errors.append(err)
                    self.log(f"[live] {err}")
            filled = [l for l in legs if l["filled"]]
            if filled and len(filled) != len(legs):
                errors.extend(self.unwind(filled))
            return legs, errors
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(legs)) as pool:
            results = list(pool.map(self._place_one, legs))

        for leg, oid, err in results:
            leg["order_id"] = oid
            leg["filled"] = oid is not None
            # A limit IOC that is accepted is assumed to have filled at its limit
            # until the order book says otherwise; reconcile() corrects this.
            leg["fill_price"] = leg["quote_price"] if oid else 0.0
            if err:
                errors.append(f"{leg['symbol']}: {err}")

        filled = [l for l in legs if l["filled"]]
        if filled and len(filled) != len(legs):
            errors.extend(self.unwind(filled))
        return legs, errors

    def unwind(self, legs):
        """Reverse whatever did fill, at market, immediately."""
        errs = []
        def rev(leg):
            flip = dict(leg)
            flip["side"] = "SELL" if leg["side"] == "BUY" else "BUY"
            try:
                oid = self.api.placeOrder(self._order_params(flip, market=True))
                return None if oid else f"unwind {leg['symbol']}: no order id"
            except Exception as e:
                return f"unwind {leg['symbol']}: {e}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(legs)) as pool:
            for r in pool.map(rev, legs):
                if r:
                    errs.append(r)
        return errs

    def reconcile(self, legs):
        """Correct assumed fills against the broker's order book."""
        try:
            book = self._paced("orderBook") or {}
            rows = {str(r.get("orderid")): r for r in (book.get("data") or [])}
        except Exception as e:
            return [f"orderBook: {e}"]
        notes = []
        for leg in legs:
            row = rows.get(str(leg.get("order_id")))
            if not row:
                continue
            status = str(row.get("status", "")).lower()
            filled_qty = float(row.get("filledshares") or 0)
            avg = float(row.get("averageprice") or 0)
            leg["broker_status"] = status
            leg["filled_qty"] = filled_qty
            if avg > 0:
                leg["fill_price"] = avg
            if filled_qty < leg["qty"]:
                leg["filled"] = False
                notes.append(f"{leg['symbol']}: {filled_qty}/{leg['qty']} ({status})")
        return notes


class ExecutionEngine:
    """Wraps a broker with costing, journalling, risk bookkeeping and alerting."""

    def __init__(self, broker, cost_model, risk, journal, notifier, lot_size,
                 total_capital=1_000_000.0, margin=None):
        self.broker = broker
        self.cm = cost_model
        self.risk = risk
        self.journal = journal
        self.notifier = notifier
        self.lot_size = lot_size
        self.total_capital = total_capital
        # arb.margin.MarginService. When present, the broker's exact margin for
        # the filled legs is fetched before the trade is journalled and alerted,
        # on a worker thread so the scan loop never waits on HTTP.
        self.margin = margin
        self.subscribers = []             # callbacks given the finished record
        self.trade_id = 0
        self._lock = threading.Lock()
        self._finaliser = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="finalise")

    def execute(self, opp, lots, tick_ns, detect_ns):
        order_ns = time.perf_counter_ns()
        order_wall = now_ist()
        legs, errors = self.broker.place_structure(opp, lots)
        placed_ns = time.perf_counter_ns()

        if getattr(self.broker, "mode", "") == "live":
            errors.extend(self.broker.reconcile(legs) or [])

        all_filled = all(l.get("filled") for l in legs)
        filled_legs = [l for l in legs if l.get("filled")]

        # Realised edge from the prices we actually got, not the ones we hoped
        # for. Slippage is what the fills cost against the quotes the edge was
        # computed on - a buy filled above the ask or a sell below the bid - and
        # it comes off the edge; the settled cash later has to agree with this.
        realised_slip = sum(abs(l["fill_price"] - l["quote_price"]) * l["qty"]
                            for l in filled_legs)
        cost_inr = sum(leg_cost(l["fill_price"], l["qty"], l["side"],
                                l.get("instrument", "OPT"), self.cm)
                       for l in filled_legs)

        if all_filled:
            gross_inr = opp.gross_edge_points * opp.qty * lots     # at the quotes
            net_inr = gross_inr - realised_slip - cost_inr
        else:
            # Broken structure: no arbitrage payoff, only the damage.
            gross_inr = 0.0
            net_inr = -realised_slip - cost_inr

        with self._lock:
            self.trade_id += 1
            tid = self.trade_id

        fill = {
            "trade_id": tid,
            "ts_ist": order_wall.isoformat(),
            # Millisecond fire time, and how far apart the leg quotes were.
            "executed_at": order_wall.strftime("%H:%M:%S.") + f"{order_wall.microsecond // 1000:03d}",
            "executed_date": order_wall.strftime("%Y-%m-%d"),
            "leg_spread_ms": round(opp.leg_spread_ms, 1),
            "mode": getattr(self.broker, "mode", "?"),
            "lots": lots,
            "legs": legs,
            "all_filled": all_filled,
            "unwound": bool(filled_legs) and not all_filled,
            "errors": errors,
            "gross_inr": gross_inr,
            "cost_inr": cost_inr,
            "net_inr": net_inr,
            "realised_slippage_inr": realised_slip,
            "latency_ms": {
                "tick_to_detect": (detect_ns - tick_ns) / 1e6 if tick_ns else 0.0,
                "detect_to_order": (order_ns - detect_ns) / 1e6,
                "order_roundtrip": (placed_ns - order_ns) / 1e6,
            },
            "opportunity": {
                "strategy": opp.strategy, "expiry": opp.expiry,
                "underlying": opp.legs[0].get("underlying", "")
                              if opp.legs else "",
                "description": opp.description,
                "gross_edge_points": opp.gross_edge_points,
                "est_cost_points": opp.est_cost_points,
                "max_age_ms": opp.max_age_ms,
                "min_depth_ratio": opp.min_depth_ratio,
                "lot_size": self.lot_size,
                "detail": opp.detail,
            },
        }

        for l in legs:
            qts = l.get("quote_ts")
            if qts:
                lt = datetime.fromtimestamp(qts, IST)
                l["quoted_at"] = lt.strftime("%H:%M:%S.") + f"{lt.microsecond // 1000:03d}"
                l["ms_before_fire"] = round(
                    (order_wall.timestamp() - qts) * 1000.0, 1)
            else:
                l["quoted_at"] = ""
                l["ms_before_fire"] = 0.0

        notional = sum(abs(l["fill_price"]) * l["qty"] for l in filled_legs)
        # Capital from the legs that actually filled, at the prices we got: the
        # broker's margin (calibrated estimate for now, exact figure below) or
        # the settlement obligation, whichever is larger.
        if filled_legs:
            capital, settlement, m_est, src = self.risk.capital_for(filled_legs, opp.expiry)
        else:
            capital, settlement, m_est, src = 0.0, 0.0, 0.0, "none"
        if capital == float("inf"):
            capital = notional
        self.risk.on_trade(net_inr, notional, capital)
        fill["capital_inr"] = capital
        fill["settlement_inr"] = settlement
        fill["margin_est_inr"] = m_est
        fill["capital_source"] = src
        fill["return_pct"] = round(100.0 * net_inr / capital, 3) \
            if (filled_legs and capital > 1.0) else 0.0
        cval, tdays = self.risk.contract_value(opp, filled_legs)
        fill["contract_value"] = cval
        fill["tenor_days"] = round(tdays, 3)
        fill["excess_return_pa"] = round(self.risk.excess_return_pa(net_inr, cval, tdays), 3)
        fill["free_carry"] = bool(filled_legs and capital <= 1.0)
        fill["total_capital"] = self.total_capital
        stake = self.risk.limits.stake_per_trade
        fill["stake_inr"] = stake
        fill["trade_pct_of_stake"] = round(100.0 * net_inr / stake, 3) if stake else 0.0
        fill["day_pct_of_stake"] = round(100.0 * self.risk.state.session_pnl / stake, 3) \
            if stake else 0.0
        fill["equity"] = self.total_capital + self.risk.state.session_pnl
        fill["capital_deployed"] = self.risk.state.capital_deployed
        fill["session_pnl"] = self.risk.state.session_pnl
        fill["session_trades"] = self.risk.state.trades_today

        if self.margin is not None and filled_legs:
            self._finaliser.submit(self._finalise, fill, filled_legs)
        else:
            self._finalise(fill, filled_legs)
        return fill

    def _finalise(self, fill, filled_legs):
        """Ask Angel One what it blocks for exactly these legs, correct the
        record, then journal, alert and publish. Runs off the scan thread."""
        if self.margin is not None and filled_legs:
            try:
                ex = self.margin.exact(filled_legs)
            except Exception as e:
                ex = None
                fill["margin_error"] = str(e)
            if ex is not None:
                fill["margin_inr"] = ex["total"]
                fill["margin_components"] = ex["components"]
                fill["margin_product"] = ex["product"]
                fill["margin_ms"] = ex["ms"]
                new_cap = max(fill.get("settlement_inr", 0.0), ex["total"])
                self.risk.adjust_capital(new_cap - fill["capital_inr"])
                fill["capital_inr"] = new_cap
                fill["capital_source"] = "angel_one"
                fill["return_pct"] = round(100.0 * fill["net_inr"] / new_cap, 3) \
                    if new_cap > 1.0 else 0.0
                fill["free_carry"] = new_cap <= 1.0
                fill["capital_deployed"] = self.risk.state.capital_deployed
            else:
                fill["margin_error"] = fill.get("margin_error") or \
                    (self.margin.last_error or "no answer from margin API")
        self.journal.record_trade(fill)
        if self.notifier:
            from arb.telegram import format_trade
            self.notifier.send(format_trade(fill, fill["mode"]))
        for cb in list(self.subscribers):
            try:
                cb(fill)
            except Exception:
                pass
        return fill

    def drain(self, timeout=10.0):
        """Wait for any in-flight finalisation (used at shutdown)."""
        self._finaliser.shutdown(wait=True, cancel_futures=False)

