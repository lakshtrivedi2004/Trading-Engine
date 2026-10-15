"""Order management. PaperExecutor and LiveExecutor share one interface so the
engine logic is identical in both modes.

Live safety rules:
  * entries are marketable LIMIT orders (bounded slippage), cancelled if not filled fast
  * the moment an entry fills, a broker-side STOPLOSS-LIMIT order is placed, so the
    stop survives an engine crash / network loss
  * if the protective stop cannot be placed, the position is closed immediately
"""
from __future__ import annotations

import logging
import time

from .broker import AngelClient, BrokerError
from .config import Config
from .risk import Position, round_tick
from .strategies import Signal

log = logging.getLogger("scalper.exec")

DONE = {"complete"}
DEAD = {"rejected", "cancelled"}


def _status(o: dict | None) -> str:
    return str((o or {}).get("orderstatus") or (o or {}).get("status") or "").lower()


def _filled(o: dict | None) -> int:
    try:
        return int(float((o or {}).get("filledshares") or 0))
    except ValueError:
        return 0


def _avg(o: dict | None) -> float:
    try:
        return float((o or {}).get("averageprice") or 0)
    except ValueError:
        return 0.0


class PaperExecutor:
    live = False

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def open(self, sig: Signal, qty: int, inst: dict, ltp: float, now) -> Position | None:
        tick = inst["tick"]
        fill = round_tick(ltp + sig.side * self.cfg.slippage_ticks * tick, tick)
        if (sig.side > 0 and fill <= sig.stop) or (sig.side < 0 and fill >= sig.stop):
            return None
        return Position(sig.symbol, inst["token"], sig.strategy, sig.side, qty, fill, sig.stop, sig.target,
                        sig.atr, tick, now, init_risk=abs(fill - sig.stop), tradingsymbol=inst["tradingsymbol"],
                        exchange=inst.get("exchange", self.cfg.exchange), kind=inst.get("kind", "EQ"))

    def move_stop(self, pos: Position, new_stop: float) -> bool:
        pos.stop = new_stop
        return True

    def close(self, pos: Position, reason: str, ltp: float) -> float:
        ref = pos.stop if reason == "stop" else ltp
        return round_tick(ref - pos.side * self.cfg.slippage_ticks * pos.tick, pos.tick)

    def broker_stop_filled(self, pos: Position) -> float | None:
        return None

    def flatten_all(self, symbols: dict):
        pass

    def open_positions(self, symbols: dict) -> dict[str, int]:
        return {}


class LiveExecutor:
    live = True

    def __init__(self, cfg: Config, client: AngelClient):
        self.cfg, self.c = cfg, client
        self.my_orders: set[str] = set()
        self.foreign: dict[str, int] = {}      # token -> qty that existed before the engine started (left alone)
        self.rejections: list[str] = []         # consecutive entry rejections (circuit breaker in the engine)
        self.traded: set[str] = set()           # tokens the ENGINE got fills in - the only ones it may reconcile/close

    # ------------------------------------------------------------ helpers
    def _wait(self, oid: str, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        o = None
        while True:
            try:
                o = self.c.order(oid)
            except BrokerError as e:
                log.warning("order poll failed: %s", e)
            st = _status(o)
            if st in DONE or st in DEAD or time.monotonic() >= end:
                return o
            time.sleep(0.25)

    def _place(self, exchange: str | None = None, **kw) -> str:
        oid = self.c.place_order(exchange=exchange or self.cfg.exchange, **kw)
        self.my_orders.add(oid)
        return oid

    def _sl_prices(self, side: int, stop: float, tick: float) -> tuple[float, float]:
        """Exit-side SL-L: trigger at stop, limit a little beyond so it actually fills."""
        buf = max(stop * self.cfg.sl_limit_buffer_pct, 2 * tick)
        return round_tick(stop, tick), round_tick(stop - side * buf, tick)

    # ------------------------------------------------------------ interface
    def open(self, sig: Signal, qty: int, inst: dict, ltp: float, now) -> Position | None:
        tick, sym, tok = inst["tick"], inst["tradingsymbol"], inst["token"]
        exch, kind = inst.get("exchange", self.cfg.exchange), inst.get("kind", "EQ")
        side = "BUY" if sig.side > 0 else "SELL"
        px = round_tick(ltp + sig.side * self.cfg.entry_buffer_ticks * tick, tick)
        try:
            oid = self._place(exchange=exch, symbol=sym, token=tok, side=side, qty=qty, order_type="LIMIT", price=px)
        except BrokerError as e:
            log.error("entry rejected: %s", e)
            self.rejections.append(str(e))
            return None
        self.rejections.clear()
        o = self._wait(oid, self.cfg.entry_timeout_s)
        if _status(o) not in DONE and _status(o) not in DEAD:
            try:
                self.c.cancel_order(oid)
            except BrokerError as e:
                log.warning("cancel entry: %s", e)
            o = self._wait(oid, 2.0)
        filled = _filled(o)
        if filled <= 0:
            log.info("entry not filled (%s) %s", _status(o), (o or {}).get("text", ""))
            return None
        fill = _avg(o) or px
        self.traded.add(tok)
        pos = Position(sig.symbol, tok, sig.strategy, sig.side, filled, fill, sig.stop, sig.target,
                       sig.atr, tick, now, init_risk=abs(fill - sig.stop), entry_order_id=oid,
                       tradingsymbol=sym, exchange=exch, kind=kind)
        # protective stop at the broker
        trig, lim = self._sl_prices(sig.side, sig.stop, tick)
        try:
            pos.sl_order_id = self._place(exchange=exch, symbol=sym, token=tok, side="SELL" if sig.side > 0 else "BUY", qty=filled,
                                          order_type="STOPLOSS_LIMIT", price=lim, trigger=trig, variety="STOPLOSS")
        except BrokerError as e:
            log.critical("PROTECTIVE STOP FAILED (%s) - closing position now", e)
            self._exit_aggressive(pos, ltp)
            return None
        return pos

    def move_stop(self, pos: Position, new_stop: float) -> bool:
        if not pos.sl_order_id:
            return False
        trig, lim = self._sl_prices(pos.side, new_stop, pos.tick)
        try:
            self.c.modify_order(order_id=pos.sl_order_id, symbol=pos.tradingsymbol, token=pos.token, qty=pos.qty,
                                order_type="STOPLOSS_LIMIT", price=lim, trigger=trig, variety="STOPLOSS",
                                exchange=pos.exchange)
            pos.stop = new_stop
            return True
        except BrokerError as e:
            log.warning("stop modify failed: %s", e)
            return False

    def broker_stop_filled(self, pos: Position) -> float | None:
        """Exit price if the broker-side stop has executed, else None."""
        if not pos.sl_order_id:
            return None
        try:
            o = self.c.order(pos.sl_order_id)
        except BrokerError:
            return None
        return (_avg(o) or pos.stop) if _status(o) in DONE else None

    def close(self, pos: Position, reason: str, ltp: float) -> float:
        # 1) pull the protective stop (or discover it already filled)
        if pos.sl_order_id:
            filled = self.broker_stop_filled(pos)
            if filled is not None:
                return filled
            try:
                self.c.cancel_order(pos.sl_order_id, variety="STOPLOSS")
            except BrokerError as e:
                log.warning("cancel SL: %s", e)
            o = self._wait(pos.sl_order_id, 2.0)
            if _status(o) in DONE:
                return _avg(o) or pos.stop
        # 2) exit
        return self._exit_aggressive(pos, ltp)

    def _exit_aggressive(self, pos: Position, ltp: float) -> float:
        side = "SELL" if pos.side > 0 else "BUY"
        sym = pos.tradingsymbol
        remaining = pos.qty
        got_value = 0.0
        for attempt in range(4):
            if attempt < 3 and ltp > 0:
                widen = (self.cfg.entry_buffer_ticks + 4 * attempt) * pos.tick + ltp * 0.001 * attempt
                kw = dict(order_type="LIMIT", price=round_tick(ltp - pos.side * widen, pos.tick))
            else:
                kw = dict(order_type="MARKET", price=0.0)
            try:
                oid = self._place(exchange=pos.exchange, symbol=sym, token=pos.token, side=side, qty=remaining, **kw)
            except BrokerError as e:
                log.error("exit order failed: %s", e)
                time.sleep(0.5)
                continue
            o = self._wait(oid, self.cfg.exit_timeout_s)
            if _status(o) not in DONE and _status(o) not in DEAD:
                try:
                    self.c.cancel_order(oid)
                except BrokerError:
                    pass
                o = self._wait(oid, 1.5)
            f = _filled(o)
            got_value += f * (_avg(o) or ltp)
            remaining -= f
            if remaining <= 0:
                return round(got_value / pos.qty, 2)
        log.critical("COULD NOT FULLY EXIT %s, %d shares still open - CHECK THE BROKER TERMINAL", pos.symbol, remaining)
        return round((got_value + remaining * ltp) / pos.qty, 2)

    def open_positions(self, symbols: dict) -> dict[str, int]:
        """symbol -> broker net qty for instruments the ENGINE traded this session (reconcile / flatten).
        Anything you trade by hand - other contracts, pending orders filling, carry-forward positions - is
        invisible here, so the engine can never 'fix' or close it."""
        tok2sym = {v["token"]: k for k, v in symbols.items()}
        out = {}
        for p in self.c.positions():
            tok = str(p.get("symboltoken"))
            if tok not in self.traded:
                continue
            sym = tok2sym.get(tok)
            net = int(float(p.get("netqty") or 0)) - self.foreign.get(tok, 0)
            if sym and net and str(p.get("producttype", "")).upper() in ("INTRADAY", "MIS"):
                out[sym] = net
        return out

    def flatten_all(self, symbols: dict):
        """Cancel every working order the engine placed and square off any open INTRADAY position."""
        try:
            for o in self.c.order_book():
                if o.get("orderid") in self.my_orders and _status(o) not in DONE | DEAD:
                    try:
                        self.c.cancel_order(o["orderid"], variety=o.get("variety", "NORMAL"))
                    except BrokerError as e:
                        log.warning("cancel %s: %s", o["orderid"], e)
            for sym, net in self.open_positions(symbols).items():
                inst = symbols[sym]
                exch = inst.get("exchange", self.cfg.exchange)
                ltp = float(self.c.quote([inst["token"]], exchange=exch).get(inst["token"], {}).get("ltp", 0)) or 0.0
                pos = Position(sym, inst["token"], "flatten", 1 if net > 0 else -1, abs(net), ltp, ltp, ltp, 0, inst["tick"], None,
                               init_risk=1, tradingsymbol=inst["tradingsymbol"], exchange=exch, kind=inst.get("kind", "EQ"))
                self._exit_aggressive(pos, ltp)
        except BrokerError as e:
            log.critical("flatten_all error: %s - CHECK THE BROKER TERMINAL", e)
