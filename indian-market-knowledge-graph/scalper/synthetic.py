"""Synthetic 1-minute NSE-like market (regime-switching random walk) for offline smoke tests.
It has no real edge in it - use it to check the plumbing, never to judge a strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _day_index(day: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(day + pd.Timedelta(hours=9, minutes=15), periods=375, freq="1min")


def make_series(days: list[pd.Timestamp], start: float, rng: np.random.Generator, vol: float = 0.0009) -> pd.DataFrame:
    frames, px = [], start
    for day in days:
        idx = _day_index(day)
        n = len(idx)
        drift = np.zeros(n)
        k = 0
        while k < n:                      # alternating trend / range regimes
            L = int(rng.integers(30, 120))
            drift[k:k + L] = rng.choice([0, 0, 1, -1]) * vol * 0.35
            k += L
        u_shape = 1 + 1.5 * np.exp(-np.arange(n) / 25) + 0.6 * np.exp(-(n - np.arange(n)) / 30)
        rets = drift + rng.standard_normal(n) * vol * u_shape
        close = px * np.exp(np.cumsum(rets))
        open_ = np.r_[px * (1 + rng.standard_normal() * 0.002), close[:-1]]
        spread = np.abs(rng.standard_normal(n)) * vol * close * 0.8
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        volume = (rng.lognormal(10, 0.5, n) * u_shape * (1 + 4 * np.abs(rets) / vol / 3)).round()
        frames.append(pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx))
        px = close[-1]
    df = pd.concat(frames)
    for c in ("open", "high", "low", "close"):
        df[c] = (df[c] / 0.05).round() * 0.05
    return df


def make_market(days: int = 24, symbols=("SBIN", "ICICIBANK"), seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = list(pd.bdate_range("2026-08-03", periods=days))
    bars = {s: make_series(dates, float(rng.uniform(400, 3000)), rng) for s in symbols}
    index = make_series(dates, 25000.0, rng, vol=0.0005)
    index["volume"] = 0.0
    return bars, index
