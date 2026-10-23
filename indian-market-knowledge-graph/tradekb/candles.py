"""Candlestick pattern detectors following Varsity M2 rules (node ids = pattern names).

`detect_all(df)` returns a DataFrame of booleans, one column per pattern, plus helper columns.
Bullish patterns require a prior downtrend and bearish ones a prior uptrend [prior_trend].
Trade plans (entry/stop) follow [candlestick_rules]: stop = pattern low (long) / high (short),
and the pattern's range must be between 1% and 10% of the close to be tradable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BULLISH = ["bullish_marubozu", "hammer", "bullish_engulfing", "piercing_pattern", "bullish_harami", "morning_star"]
BEARISH = ["bearish_marubozu", "hanging_man", "shooting_star", "bearish_engulfing", "dark_cloud_cover",
           "bearish_harami", "evening_star"]
NEUTRAL = ["doji", "spinning_top"]
PATTERN_SPAN = {"bullish_engulfing": 2, "bearish_engulfing": 2, "piercing_pattern": 2, "dark_cloud_cover": 2,
                "bullish_harami": 2, "bearish_harami": 2, "morning_star": 3, "evening_star": 3}


def _parts(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    return o, h, l, c, body, rng, upper, lower


def prior_trend(close: pd.Series, n: int = 5) -> pd.Series:
    """[prior_trend] +1 uptrend / -1 downtrend into the bar *before* the pattern (close[t-1] vs close[t-1-n])."""
    prev = close.shift(1)
    ref = close.shift(1 + n)
    return pd.Series(np.sign(prev - ref), index=close.index).fillna(0)


def detect_all(df: pd.DataFrame, trend_lookback: int = 5, marubozu_tol: float = 0.002,
               doji_frac: float = 0.1, small_body_frac: float = 0.3, long_body_frac: float = 0.6) -> pd.DataFrame:
    o, h, l, c, body, rng, upper, lower = _parts(df)
    blue, red = c > o, c < o
    out = pd.DataFrame(index=df.index)

    # single candle
    out["bullish_marubozu"] = blue & ((o - l).abs() <= marubozu_tol * h) & ((h - c).abs() <= marubozu_tol * h)
    out["bearish_marubozu"] = red & ((h - o).abs() <= marubozu_tol * h) & ((c - l).abs() <= marubozu_tol * h)
    out["doji"] = body <= doji_frac * rng
    out["spinning_top"] = (body <= small_body_frac * rng) & ~out["doji"] & \
        ((upper - lower).abs() <= 0.5 * np.maximum(upper, lower))
    umbrella = (lower >= 2 * body) & (upper <= 0.5 * body.clip(lower=1e-12) + 0.1 * rng) & (body > 0)
    star = (upper >= 2 * body) & (lower <= 0.1 * rng) & (body > 0)

    t = prior_trend(c, trend_lookback)
    down, up = t < 0, t > 0
    out["hammer"] = umbrella & down
    out["hanging_man"] = umbrella & up
    out["shooting_star"] = star & up

    # two candle (P1 = previous, P2 = current)
    o1, h1, l1, c1 = o.shift(), h.shift(), l.shift(), c.shift()
    body1 = (c1 - o1).abs()
    rng1 = (h1 - l1).replace(0, np.nan)
    red1, blue1 = c1 < o1, c1 > o1
    t2 = prior_trend(c, trend_lookback).shift()  # trend before P1
    down2, up2 = t2 < 0, t2 > 0
    out["bullish_engulfing"] = down2 & red1 & blue & (o <= c1) & (c > o1)
    out["bearish_engulfing"] = up2 & blue1 & red & (o >= c1) & (c < o1)
    out["piercing_pattern"] = down2 & red1 & blue & (c - c1 >= 0.5 * body1) & (c - c1 < body1) & (o < c1)
    out["dark_cloud_cover"] = up2 & blue1 & red & (c1 - c >= 0.5 * body1) & (c1 - c < body1) & (o > c1)
    long1 = body1 >= long_body_frac * rng1
    out["bullish_harami"] = down2 & red1 & long1 & blue & (o > c1) & (c < o1)
    out["bearish_harami"] = up2 & blue1 & long1 & red & (o < c1) & (c > o1)

    # three candle (P1 = t-2, P2 = t-1, P3 = t)
    o2, c2, h2, l2 = o.shift(2), c.shift(2), h.shift(2), l.shift(2)
    small_mid = (body1 <= small_body_frac * rng1)
    t3 = prior_trend(c, trend_lookback).shift(2)
    out["morning_star"] = (t3 < 0) & (c2 < o2) & small_mid & (np.maximum(o1, c1) < c2) & blue & \
        (o > np.maximum(o1, c1)) & (c > o2)
    out["evening_star"] = (t3 > 0) & (c2 > o2) & small_mid & (np.minimum(o1, c1) > c2) & red & \
        (o < np.minimum(o1, c1)) & (c < o2)
    return out.fillna(False).astype(bool)


def trade_plans(df: pd.DataFrame, patterns: pd.DataFrame | None = None, min_range: float = 0.01,
                max_range: float = 0.10) -> pd.DataFrame:
    """[candlestick_rules] For every detected bullish/bearish pattern: side, entry (pattern close),
    stop (pattern low/high), range%, and whether the range is tradable (1%-10%)."""
    patterns = detect_all(df) if patterns is None else patterns
    rows = []
    for name in BULLISH + BEARISH:
        span = PATTERN_SPAN.get(name, 1)
        for ts in patterns.index[patterns[name]]:
            i = df.index.get_loc(ts)
            win = df.iloc[max(0, i - span + 1): i + 1]
            side = "long" if name in BULLISH else "short"
            entry = float(df["close"].iloc[i])
            stop = float(win["low"].min()) if side == "long" else float(win["high"].max())
            rng = abs(entry - stop) / entry
            rows.append({"time": ts, "pattern": name, "side": side, "entry": entry, "stop": stop,
                         "risk_pct": rng, "tradable": min_range <= rng <= max_range})
    return pd.DataFrame(rows, columns=["time", "pattern", "side", "entry", "stop", "risk_pct", "tradable"])
