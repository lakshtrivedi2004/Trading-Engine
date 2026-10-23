"""Regime classifier + five scalping setups.

Each setup is objective and returns entry/stop/target, so sizing and the
cost/expectancy gate are pure arithmetic.

    regime TREND  -> ORB, VWAP pullback, momentum breakout
    regime RANGE  -> VWAP z-score reversion, liquidity sweep
    regime MIXED  -> ORB, liquidity sweep (strictest filters)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

TREND_UP, TREND_DN, RANGE, MIXED = "TREND_UP", "TREND_DN", "RANGE", "MIXED"


@dataclass
class Signal:
    symbol: str
    strategy: str
    side: int          # +1 long, -1 short
    entry: float
    stop: float
    target: float
    atr: float
    reason: str
    ts: pd.Timestamp | None = None

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.entry)


def regime(r: pd.Series) -> str:
    er, adx = r["er"], r["adx"]
    if any(map(lambda x: x != x, (er, adx))):  # NaN
        return MIXED
    if er >= 0.30 and adx >= 20:
        if r["close"] > r["vwap"] and r["ema9"] > r["ema20"]:
            return TREND_UP
        if r["close"] < r["vwap"] and r["ema9"] < r["ema20"]:
            return TREND_DN
    if er <= 0.20 and adx < 20:
        return RANGE
    return MIXED


def _rv(r: pd.Series, thr: float) -> bool:
    """Relative-volume filter; indices carry no volume, so the filter is skipped for them."""
    rv = r["relvol"]
    if rv != rv:
        return r["volume"] == 0
    return rv >= thr


def _ok(*vals) -> bool:
    return all(v == v and v is not None and not math.isinf(v) for v in vals)


# ---------------------------------------------------------------- setups
# signature: (df_with_features, i, ctx) -> Signal | None ; ctx: symbol, bias, reg, fired(set)

def orb(d: pd.DataFrame, i: int, ctx: dict) -> Signal | None:
    """15-min opening range breakout + relative volume + VWAP side. Once per side per day, before 11:30."""
    r, p = d.iloc[i], d.iloc[i - 1]
    if not (15 <= r["bar_no"] <= 135) or not _ok(r["or_high"], r["or_low"], r["atr"]):
        return None
    width = (r["or_high"] - r["or_low"]) / r["close"]
    if not 0.001 <= width <= 0.025 or not _rv(r, 1.3) or r["body_frac"] < 0.5:
        return None
    atr = r["atr"]
    if r["close"] > r["or_high"] >= p["close"] and r["close"] > r["vwap"] and ctx["bias"] >= 0 and "orb+1" not in ctx["fired"]:
        stop = min(r["or_high"] - 0.5 * atr, r["close"] - 0.8 * atr)
        e = r["close"]
        return Signal(ctx["symbol"], "orb", 1, e, stop, e + 2.0 * (e - stop), atr, f"ORB up relvol={r['relvol']:.1f}")
    if r["close"] < r["or_low"] <= p["close"] and r["close"] < r["vwap"] and ctx["bias"] <= 0 and "orb-1" not in ctx["fired"]:
        stop = max(r["or_low"] + 0.5 * atr, r["close"] + 0.8 * atr)
        e = r["close"]
        return Signal(ctx["symbol"], "orb", -1, e, stop, e - 2.0 * (stop - e), atr, f"ORB down relvol={r['relvol']:.1f}")
    return None


def vwap_pullback(d: pd.DataFrame, i: int, ctx: dict) -> Signal | None:
    """Trend + pullback into VWAP/EMA20 + confirmation bar breaking the prior bar."""
    if ctx["reg"] not in (TREND_UP, TREND_DN) or i < 6:
        return None
    r, p = d.iloc[i], d.iloc[i - 1]
    w = d.iloc[i - 5:i]
    atr = r["atr"]
    if not _ok(atr, r["vwap"], r["ema20"]):
        return None
    if ctx["reg"] == TREND_UP and ctx["bias"] >= 0:
        zone = max(r["vwap"], r["ema20"]) + 0.3 * atr
        touched = w["low"].min() <= zone
        held = (w["close"] > r["vwap"] - 0.3 * atr).all()
        if touched and held and r["close"] > p["high"] and r["close"] > r["open"]:
            e, stop = r["close"], w["low"].min() - 0.2 * atr
            if e - stop < 0.4 * atr:
                stop = e - 0.4 * atr
            return Signal(ctx["symbol"], "vwap_pullback", 1, e, stop, e + 1.8 * (e - stop), atr, "trend pullback to VWAP")
    if ctx["reg"] == TREND_DN and ctx["bias"] <= 0:
        zone = min(r["vwap"], r["ema20"]) - 0.3 * atr
        touched = w["high"].max() >= zone
        held = (w["close"] < r["vwap"] + 0.3 * atr).all()
        if touched and held and r["close"] < p["low"] and r["close"] < r["open"]:
            e, stop = r["close"], w["high"].max() + 0.2 * atr
            if stop - e < 0.4 * atr:
                stop = e + 0.4 * atr
            return Signal(ctx["symbol"], "vwap_pullback", -1, e, stop, e - 1.8 * (stop - e), atr, "trend pullback to VWAP")
    return None


def momentum_breakout(d: pd.DataFrame, i: int, ctx: dict) -> Signal | None:
    """Compression (low BB-width percentile) -> volume expansion -> break of 20-bar extreme."""
    r, p = d.iloc[i], d.iloc[i - 1]
    if not _ok(p["bbw_pct"], r["hh20"], r["ll20"], r["atr"]) or p["bbw_pct"] > 0.25:
        return None
    if not _rv(r, 1.5) or r["body_frac"] < 0.55:
        return None
    atr = r["atr"]
    if r["close"] > r["hh20"] and r["close"] > r["vwap"] and ctx["bias"] >= 0 and ctx["reg"] != TREND_DN:
        e = r["close"]
        stop = max(r["ll5"], e - 1.5 * atr)
        stop = min(stop, e - 0.5 * atr)
        return Signal(ctx["symbol"], "momentum", 1, e, stop, e + 2.0 * (e - stop), atr, f"squeeze break relvol={r['relvol']:.1f}")
    if r["close"] < r["ll20"] and r["close"] < r["vwap"] and ctx["bias"] <= 0 and ctx["reg"] != TREND_UP:
        e = r["close"]
        stop = min(r["hh5"], e + 1.5 * atr)
        stop = max(stop, e + 0.5 * atr)
        return Signal(ctx["symbol"], "momentum", -1, e, stop, e - 2.0 * (stop - e), atr, f"squeeze break relvol={r['relvol']:.1f}")
    return None


def liquidity_sweep(d: pd.DataFrame, i: int, ctx: dict) -> Signal | None:
    """Wick through a 20-bar / prior-day extreme that closes back inside -> fade toward VWAP."""
    if ctx["reg"] in (TREND_UP, TREND_DN):
        return None
    r = d.iloc[i]
    atr = r["atr"]
    if not _ok(r["hh20"], r["ll20"], atr) or not _rv(r, 1.2):
        return None
    hi_lvl = max(r["hh20"], r["pdh"]) if _ok(r["pdh"]) and r["pdh"] - r["hh20"] < 0.5 * atr else r["hh20"]
    lo_lvl = min(r["ll20"], r["pdl"]) if _ok(r["pdl"]) and r["ll20"] - r["pdl"] < 0.5 * atr else r["ll20"]
    if r["high"] > hi_lvl and r["close"] < hi_lvl and r["upper_wick"] >= 0.5:
        e, stop = r["close"], r["high"] + 0.15 * atr
        risk = stop - e
        tgt = r["vwap"] if e - r["vwap"] >= 1.5 * risk else e - 1.5 * risk
        return Signal(ctx["symbol"], "sweep", -1, e, stop, tgt, atr, f"swept high {hi_lvl:.2f}")
    if r["low"] < lo_lvl and r["close"] > lo_lvl and r["lower_wick"] >= 0.5:
        e, stop = r["close"], r["low"] - 0.15 * atr
        risk = e - stop
        tgt = r["vwap"] if r["vwap"] - e >= 1.5 * risk else e + 1.5 * risk
        return Signal(ctx["symbol"], "sweep", 1, e, stop, tgt, atr, f"swept low {lo_lvl:.2f}")
    return None


def vwap_reversion(d: pd.DataFrame, i: int, ctx: dict) -> Signal | None:
    """RANGE only: |z| >= 2 from VWAP + reversal bar -> target VWAP."""
    if ctx["reg"] != RANGE:
        return None
    r = d.iloc[i]
    atr = r["atr"]
    if not _ok(r["z"], atr, r["vwap"]):
        return None
    if r["z"] <= -2.0 and r["close"] > r["open"]:
        e, stop = r["close"], r["low"] - 0.3 * atr
        return Signal(ctx["symbol"], "vwap_revert", 1, e, stop, r["vwap"], atr, f"z={r['z']:.2f}")
    if r["z"] >= 2.0 and r["close"] < r["open"]:
        e, stop = r["close"], r["high"] + 0.3 * atr
        return Signal(ctx["symbol"], "vwap_revert", -1, e, stop, r["vwap"], atr, f"z={r['z']:.2f}")
    return None


STRATEGIES = {
    "orb": orb,
    "vwap_pullback": vwap_pullback,
    "momentum": momentum_breakout,
    "sweep": liquidity_sweep,
    "vwap_revert": vwap_reversion,
}


STRUCTURAL_TARGET = {"vwap_revert", "sweep"}   # target is a price level (VWAP), not an R multiple


def widen_stop(s: Signal, min_stop_atr: float, min_stop_pct: float) -> Signal:
    """Enforce a minimum stop distance; R-multiple targets scale with it, level targets stay put."""
    min_d = max(min_stop_atr * s.atr, min_stop_pct * s.entry)
    if s.risk >= min_d:
        return s
    rr = s.reward / s.risk
    s.stop = s.entry - s.side * min_d
    if s.strategy not in STRUCTURAL_TARGET:
        s.target = s.entry + s.side * rr * min_d
    return s


def scan(d: pd.DataFrame, i: int, symbol: str, bias: int, fired: set, enabled=None,
         min_atr_pct: float = 0.0006, max_atr_pct: float = 0.006,
         min_stop_atr: float = 1.2, min_stop_pct: float = 0.0025) -> list[Signal]:
    """All valid signals on bar i (needs >= 30 bars of history)."""
    if i < 30:
        return []
    r = d.iloc[i]
    if not _ok(r["atr"]) or not (min_atr_pct <= r["atr"] / r["close"] <= max_atr_pct):
        return []
    ctx = {"symbol": symbol, "bias": bias, "reg": regime(r), "fired": fired}
    out = []
    for name, fn in STRATEGIES.items():
        if enabled is not None and name not in enabled:
            continue
        s = fn(d, i, ctx)
        if s and s.risk > 0 and s.reward > 0:
            s = widen_stop(s, min_stop_atr, min_stop_pct)
        if s and s.reward > 0:
            s.ts = d.index[i]
            s.reason = f"{ctx['reg']} | {s.reason}"
            out.append(s)
    return out
