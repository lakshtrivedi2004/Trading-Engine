"""
Synthetic data for --demo mode: lets you see the full report without internet.
The numbers are SIMULATED and mean nothing about real markets.
"""
import numpy as np
import pandas as pd


def _ohlc(close, gap_share, rng):
    """Build Open/High/Low from closes; part of each day's move happens at the open."""
    prev = close.shift(1).bfill()
    ret = close / prev - 1
    opn = prev * (1 + ret * gap_share + rng.normal(0, 0.001, len(close)))
    high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.003, len(close))))
    low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.003, len(close))))
    return pd.DataFrame({"Open": opn, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, len(close))})


def make_demo_data(start="2012-01-01", seed=7):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, pd.Timestamp.today().normalize())
    n = len(days)

    # volatility regimes (calm / normal / stressed) that persist for weeks
    regime = np.ones(n)
    level = 1.0
    for i in range(n):
        if rng.random() < 0.02:
            level = rng.choice([0.7, 1.0, 1.0, 1.8])
        regime[i] = level

    us = rng.normal(0.0004, 0.010, n) * regime
    us_prev = np.r_[0, us[:-1]]
    nikkei = 0.35 * us_prev + rng.normal(0, 0.010, n) * regime
    hsi = 0.30 * us_prev + 0.3 * nikkei + rng.normal(0, 0.011, n) * regime
    inr = -0.08 * us_prev + rng.normal(0.0001, 0.003, n)
    brent = rng.normal(0.0002, 0.018, n) * regime
    brent_prev = np.r_[0, brent[:-1]]
    tnx_diff = rng.normal(0, 0.05, n)
    tnx_prev = np.r_[0, tnx_diff[:-1]]

    # momentum-ish drift so the pattern tests have something to find
    nifty = (0.0004 + 0.30 * us_prev + 0.15 * nikkei + 0.12 * hsi - 0.6 * inr
             - 0.03 * brent_prev - 0.004 * tnx_prev
             + rng.normal(0, 0.006, n) * regime)
    sensex = nifty + rng.normal(0, 0.0015, n)
    banknifty = 1.25 * nifty - 0.006 * tnx_prev - 0.2 * inr + rng.normal(0, 0.006, n) * regime
    bankex = banknifty + rng.normal(0, 0.002, n)

    def level_series(r, base):
        return pd.Series(base * np.cumprod(1 + r), index=days)

    indices = {
        "Nifty 50": _ohlc(level_series(nifty, 5000), 0.5, rng),
        "Sensex": _ohlc(level_series(sensex, 16500), 0.5, rng),
        "Bank Nifty": _ohlc(level_series(banknifty, 9500), 0.5, rng),
        "Bankex": _ohlc(level_series(bankex, 11000), 0.5, rng),
    }

    vix = np.empty(n)
    vix[0] = 18
    for i in range(1, n):
        vix[i] = max(9, vix[i - 1] + 0.05 * (17 - vix[i - 1]) - 120 * nifty[i] + rng.normal(0, 0.4))
    tnx = 2.0 + np.cumsum(tnx_diff)
    tnx = tnx - min(0, tnx.min()) + 0.5

    def as_df(levels):
        return pd.DataFrame({"Close": levels}, index=days)

    factors = {
        "S&P 500": as_df(level_series(us, 1300).values),
        "US 10Y yield": as_df(tnx),
        "Brent crude": as_df(level_series(brent, 80).values),
        "USD/INR": as_df(level_series(inr, 50).values),
        "Nikkei 225": as_df(level_series(nikkei, 9000).values),
        "Hang Seng": as_df(level_series(hsi, 20000).values),
        "India VIX": as_df(vix),
    }
    notes = ["DEMO MODE: all numbers in this report are simulated and say nothing about real markets."]
    return indices, factors, notes
