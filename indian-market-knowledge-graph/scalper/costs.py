"""Angel One equity-intraday charges (NSE), 2026 schedule.

Every sizing and backtest decision uses these numbers so the engine never takes
a trade whose edge is smaller than its costs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChargeSchedule:
    brokerage_flat: float = 20.0      # per executed order ...
    brokerage_pct: float = 0.001      # ... or 0.1% of order value, whichever is lower
    brokerage_min: float = 5.0        # Angel One minimum per executed equity order
    stt_sell: float = 0.00025         # intraday equity STT, sell side
    exchange_txn: float = 0.000030699 # NSE cash (0.0030699%)
    sebi: float = 0.000001            # ₹10 / crore
    stamp_buy: float = 0.00003        # intraday stamp duty, buy side
    gst: float = 0.18                 # on brokerage + exchange + SEBI


ANGEL_INTRADAY = ChargeSchedule()
# index options (premium turnover): ₹20 flat per order, STT 0.15% on sell premium (from 1 Apr 2026)
ANGEL_OPT_NSE = ChargeSchedule(brokerage_flat=20.0, brokerage_pct=1.0, brokerage_min=20.0,
                               stt_sell=0.0015, exchange_txn=0.000355299,
                               sebi=0.000001, stamp_buy=0.00003, gst=0.18)
ANGEL_OPT_BSE = ChargeSchedule(brokerage_flat=20.0, brokerage_pct=1.0, brokerage_min=20.0,
                               stt_sell=0.0015, exchange_txn=0.000325,
                               sebi=0.000001, stamp_buy=0.00003, gst=0.18)
SCHEDULES = {"EQ": ANGEL_INTRADAY, "NFO": ANGEL_OPT_NSE, "BFO": ANGEL_OPT_BSE}


def round_trip(buy_value: float, sell_value: float, s: ChargeSchedule = ANGEL_INTRADAY) -> float:
    """Total charges for one buy + one sell order (either order first, i.e. long or short)."""
    brk = (max(s.brokerage_min, min(s.brokerage_flat, s.brokerage_pct * buy_value))
           + max(s.brokerage_min, min(s.brokerage_flat, s.brokerage_pct * sell_value)))
    turnover = buy_value + sell_value
    exch = s.exchange_txn * turnover
    sebi = s.sebi * turnover
    return brk + s.stt_sell * sell_value + exch + sebi + s.stamp_buy * buy_value + s.gst * (brk + exch + sebi)


def trade_costs(side: int, entry: float, exit_: float, qty: int, s: ChargeSchedule | str = ANGEL_INTRADAY) -> float:
    if isinstance(s, str):
        s = SCHEDULES[s]
    if side > 0:
        return round_trip(entry * qty, exit_ * qty, s)
    return round_trip(exit_ * qty, entry * qty, s)


def breakeven_move(price: float, qty: int, s: ChargeSchedule = ANGEL_INTRADAY) -> float:
    """Price move per share needed just to pay the charges."""
    return round_trip(price * qty, price * qty, s) / qty if qty else float("inf")
