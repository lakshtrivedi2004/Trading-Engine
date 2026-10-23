"""Market regime: trend, volatility percentile, drawdown, India VIX and co-movement.

Regimes matter because the same signal behaves differently in calm and stressed markets
(Moreira & Muir 2017 on volatility; Moskowitz, Ooi & Pedersen 2012 on trends; Faber 2007 on
the 200-day average). Every number uses data up to the latest close only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .stats import TRADING_DAYS


def pct_rank(series: pd.Series, value: float) -> float:
    """Share of history (in %) below `value`."""
    s = series.dropna()
    return float((s < value).mean() * 100) if len(s) else np.nan


def _ret_over(c: pd.Series, n: int) -> float:
    return c.iloc[-1] / c.iloc[-1 - n] - 1 if len(c) > n else np.nan


def index_regime(df: pd.DataFrame, nifty_close: pd.Series | None = None) -> dict:
    c = df["Close"].dropna()
    r = c.pct_change()
    vol20 = r.rolling(20).std() * np.sqrt(TRADING_DAYS)
    out = {"date": c.index[-1], "close": c.iloc[-1], "ret_1d": r.iloc[-1], "ret_1w": _ret_over(c, 5),
           "ret_1m": _ret_over(c, 21), "ret_3m": _ret_over(c, 63), "ret_1y": _ret_over(c, 252),
           "vol20": vol20.iloc[-1], "vol_pct": pct_rank(vol20, vol20.iloc[-1]),
           "drawdown": c.iloc[-1] / c.cummax().iloc[-1] - 1,
           "vs_dma50": c.iloc[-1] / c.rolling(50).mean().iloc[-1] - 1,
           "vs_dma200": c.iloc[-1] / c.rolling(200).mean().iloc[-1] - 1}
    a50, a200 = out["vs_dma50"] > 0, out["vs_dma200"] > 0
    out["trend"] = "Uptrend" if a50 and a200 else "Downtrend" if not a50 and not a200 else "Mixed"
    v = out["vol_pct"]
    out["vol_regime"] = "Calm" if v < 33 else ("Normal" if v < 80 else "Stressed")
    if nifty_close is not None:
        j = pd.concat([r, nifty_close.pct_change()], axis=1).dropna()
        j.columns = ["a", "b"]
        out["corr_nifty_60d"] = j.tail(60).corr().iloc[0, 1]
        out["corr_nifty_all"] = j.corr().iloc[0, 1]
    return out


def all_regimes(indices: dict) -> pd.DataFrame:
    nifty = indices.get("Nifty 50")
    nclose = nifty["Close"] if nifty is not None else None
    rows = {n: index_regime(df, None if n == "Nifty 50" else nclose) for n, df in indices.items()}
    return pd.DataFrame(rows).T


def vix_status(vix_df: pd.DataFrame | None) -> dict | None:
    if vix_df is None or len(vix_df) < 2:
        return None
    v = vix_df["Close"].dropna()
    return {"level": v.iloc[-1], "change_1d": v.iloc[-1] - v.iloc[-2], "pct_rank": pct_rank(v, v.iloc[-1]),
            "avg_1y": v.tail(252).mean(), "date": v.index[-1]}


def history(df: pd.DataFrame) -> pd.DataFrame:
    """Time series for charts: close, 50/200-day averages, drawdown, 20-day vol and its percentile."""
    c = df["Close"].dropna()
    r = c.pct_change()
    vol20 = r.rolling(20).std() * np.sqrt(TRADING_DAYS)
    # expanding percentile: each day ranked only against history up to that day
    vol_pct = vol20.expanding(min_periods=252).rank(pct=True) * 100
    return pd.DataFrame({"close": c, "dma50": c.rolling(50).mean(), "dma200": c.rolling(200).mean(),
                         "drawdown": c / c.cummax() - 1, "vol20": vol20, "vol_pct": vol_pct})


def correlation_matrix(indices: dict, factors: dict, days: int = 250) -> pd.DataFrame:
    """Daily-return correlations over the last `days` Indian sessions (same-day alignment)."""
    nifty = indices.get("Nifty 50")
    if nifty is None:
        return pd.DataFrame()
    dates = nifty.index
    cols = {n: df["Close"].reindex(dates).pct_change() for n, df in indices.items()}
    for n, df in factors.items():
        lv = df["Close"].dropna()
        cols[n] = pd.merge_asof(pd.DataFrame({"d": dates}), pd.DataFrame({"d": lv.index, "v": lv.to_numpy()}),
                                on="d", direction="backward").set_index("d")["v"].pct_change()
    return pd.DataFrame(cols).tail(days).corr()
