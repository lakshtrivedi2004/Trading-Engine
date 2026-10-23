"""Causal 1-minute features. Every column at row i uses only bars <= i, so the
same frame drives the backtest and the live engine without look-ahead."""
from __future__ import annotations

import numpy as np
import pandas as pd

OR_BARS = 15  # 09:15-09:29 opening range


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: index = tz-naive IST timestamps, columns open/high/low/close/volume."""
    d = df.copy()
    o, h, l, c, v = (d[k] for k in ("open", "high", "low", "close", "volume"))
    day = pd.Series(d.index.date, index=d.index)
    d["bar_no"] = day.groupby(day).cumcount()

    prev_c = c.shift()
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    d["atr"] = _wilder(tr, 14)

    # session VWAP (falls back to typical-price mean when volume is missing, e.g. the index)
    tp = (h + l + c) / 3
    vol = v.where(v > 0, 0.0)
    cum_pv = (tp * vol).groupby(day).cumsum()
    cum_v = vol.groupby(day).cumsum()
    d["vwap"] = (cum_pv / cum_v.replace(0, np.nan)).fillna(tp.groupby(day).expanding().mean().droplevel(0))

    d["ema9"] = c.ewm(span=9, adjust=False).mean()
    d["ema20"] = c.ewm(span=20, adjust=False).mean()

    # ADX(14)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)
    atr_w = _wilder(tr, 14)
    pdi = 100 * _wilder(pdm, 14) / atr_w
    ndi = 100 * _wilder(ndm, 14) / atr_w
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    d["adx"] = _wilder(dx.fillna(0), 14)

    # Kaufman efficiency ratio: 1 = straight line, 0 = noise
    d["er"] = (c - c.shift(20)).abs() / c.diff().abs().rolling(20).sum().replace(0, np.nan)

    # relative volume vs previous 20 bars
    d["relvol"] = v / v.shift().rolling(20).mean().replace(0, np.nan)

    # VWAP deviation z-score
    dev = c - d["vwap"]
    d["z"] = dev / dev.rolling(30).std().replace(0, np.nan)

    # prior structure (shifted: excludes the current bar)
    d["hh20"] = h.shift().rolling(20).max()
    d["ll20"] = l.shift().rolling(20).min()
    d["hh5"] = h.rolling(5).max()
    d["ll5"] = l.rolling(5).min()

    # Bollinger bandwidth percentile over the last 120 bars (compression detector)
    mid = c.rolling(20).mean()
    bbw = 4 * c.rolling(20).std() / mid
    d["bbw_pct"] = bbw.rolling(120, min_periods=40).rank(pct=True)

    # opening range, only known once the 15th bar has closed
    first = d["bar_no"] < OR_BARS
    or_h = h.where(first).groupby(day).transform("max")
    or_l = l.where(first).groupby(day).transform("min")
    known = d["bar_no"] >= OR_BARS - 1
    d["or_high"] = or_h.where(known)
    d["or_low"] = or_l.where(known)

    # previous day high / low
    daily = d.groupby(day).agg(dh=("high", "max"), dl=("low", "min"))
    prev = daily.shift()
    d["pdh"] = day.map(prev["dh"])
    d["pdl"] = day.map(prev["dl"])

    rng = (h - l).replace(0, np.nan)
    d["body_frac"] = (c - o).abs() / rng
    d["upper_wick"] = (h - np.maximum(o, c)) / rng
    d["lower_wick"] = (np.minimum(o, c) - l) / rng
    return d


def index_bias(idx: pd.DataFrame) -> pd.Series:
    """+1 / -1 / 0 market direction from NIFTY 1m bars (futures/spot confirmation layer)."""
    c = idx["close"]
    e9, e20 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
    slope = e20.diff(5)
    up = (c > e20) & (e9 > e20) & (slope > 0)
    dn = (c < e20) & (e9 < e20) & (slope < 0)
    return pd.Series(np.where(up, 1, np.where(dn, -1, 0)), index=idx.index)
