"""
Market regime: trend, volatility, drawdown, and co-movement.
Regimes matter because the same signal behaves differently in calm and
stressed markets (e.g. Moreira & Muir 2017 on volatility; Moskowitz, Ooi &
Pedersen 2012 on trends).
"""
import numpy as np
import pandas as pd

from stats import TRADING_DAYS


def _pct_rank(series, value):
    s = series.dropna()
    return (s < value).mean() * 100 if len(s) else np.nan


def index_regime(df, nifty_close=None):
    c = df["Close"].dropna()
    r = c.pct_change()
    vol20 = r.rolling(20).std() * np.sqrt(TRADING_DAYS)
    out = {
        "date": c.index[-1],
        "close": c.iloc[-1],
        "ret_1d": r.iloc[-1],
        "ret_1w": c.iloc[-1] / c.iloc[-6] - 1 if len(c) > 6 else np.nan,
        "ret_1m": c.iloc[-1] / c.iloc[-22] - 1 if len(c) > 22 else np.nan,
        "ret_3m": c.iloc[-1] / c.iloc[-64] - 1 if len(c) > 64 else np.nan,
        "ret_1y": c.iloc[-1] / c.iloc[-253] - 1 if len(c) > 253 else np.nan,
        "vol20": vol20.iloc[-1],
        "vol_pct": _pct_rank(vol20, vol20.iloc[-1]),
        "drawdown": c.iloc[-1] / c.cummax().iloc[-1] - 1,
        "vs_dma50": c.iloc[-1] / c.rolling(50).mean().iloc[-1] - 1,
        "vs_dma200": c.iloc[-1] / c.rolling(200).mean().iloc[-1] - 1,
    }
    above50, above200 = out["vs_dma50"] > 0, out["vs_dma200"] > 0
    if above50 and above200:
        out["trend"] = "Uptrend"
    elif not above50 and not above200:
        out["trend"] = "Downtrend"
    else:
        out["trend"] = "Mixed"
    v = out["vol_pct"]
    out["vol_regime"] = "Calm" if v < 33 else ("Normal" if v < 80 else "Stressed")
    if nifty_close is not None:
        joined = pd.concat([r, nifty_close.pct_change()], axis=1).dropna()
        joined.columns = ["a", "b"]
        out["corr_nifty_60d"] = joined.tail(60).corr().iloc[0, 1]
        out["corr_nifty_all"] = joined.corr().iloc[0, 1]
    return out


def vix_status(vix_df):
    if vix_df is None:
        return None
    v = vix_df["Close"].dropna()
    return {
        "level": v.iloc[-1],
        "change_1d": v.iloc[-1] - v.iloc[-2],
        "pct_rank": _pct_rank(v, v.iloc[-1]),
        "avg_1y": v.tail(252).mean(),
    }


def all_regimes(indices, factors):
    nifty = indices.get("Nifty 50")
    nclose = nifty["Close"] if nifty is not None else None
    rows = {}
    for name, df in indices.items():
        rows[name] = index_regime(df, None if name == "Nifty 50" else nclose)
    return pd.DataFrame(rows).T, vix_status(factors.get("India VIX"))


def regime_text(reg, vix):
    lines = []
    for name, r in reg.iterrows():
        lines.append(
            f"{name}: {r['trend'].lower()}, volatility {r['vol_regime'].lower()} "
            f"({r['vol20'] * 100:.1f}% annualised, higher than {r['vol_pct']:.0f}% of history), "
            f"{abs(r['drawdown']) * 100:.1f}% below its peak."
        )
    if vix:
        lines.append(f"India VIX is {vix['level']:.1f} ({vix['change_1d']:+.2f} today), higher than "
                     f"{vix['pct_rank']:.0f}% of its history; 1-year average {vix['avg_1y']:.1f}.")
    return lines
