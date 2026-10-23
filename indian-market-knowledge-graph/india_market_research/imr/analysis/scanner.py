"""Volatility scanner for BSE stocks (from stored bhavcopies).

A watchlist, not a recommendation: it ranks what moved and what is volatile, with the facts a trader
needs to judge tradability (liquidity, circuit limits, trade-to-trade groups). It never says buy/sell.

Measures (per stock, as of the latest stored day):
- 1-day move: close / BSE previous close - 1 (BSE adjusts the previous close for splits/bonuses).
- Unusual move (z): today's move divided by the stock's own daily volatility over the PREVIOUS 20
  sessions (today excluded, so a big day cannot inflate its own baseline).
- Volatility: 20-day standard deviation of daily moves, annualised (x sqrt 252).
- ATR%: 14-day average true range as % of price (Wilder 1978), using BSE's previous close.
- Liquidity: average daily turnover over 20 sessions (Rs crore); volume vs its 20-day average.
- Circuit flag: locked (high = low) or closing at the high/low right at a standard band (2/5/10/20%):
  such stocks may not be tradable at the shown price.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BANDS = (0.02, 0.05, 0.10, 0.20)
GROUP_NOTES = {"T": "trade-to-trade (no intraday)", "XT": "trade-to-trade (no intraday)", "Z": "Z group (compliance issues)",
               "M": "SME", "MT": "SME trade-to-trade"}


def compute_metrics(df: pd.DataFrame, vol_window: int = 20, atr_window: int = 14) -> pd.DataFrame:
    """One row per symbol with all scanner metrics as of each symbol's latest day."""
    if df.empty:
        return pd.DataFrame()
    d = df.sort_values(["symbol", "date"]).copy()
    d["ret"] = d["close"] / d["prev_close"] - 1
    hi, lo, pc = d["high"], d["low"], d["prev_close"]
    d["tr_pct"] = np.maximum(hi - lo, np.maximum((hi - pc).abs(), (lo - pc).abs())) / pc
    g = d.groupby("symbol", sort=False)
    prior = g["ret"].shift(1)
    d["vol_prior"] = prior.groupby(d["symbol"]).rolling(vol_window, min_periods=10).std().reset_index(level=0, drop=True)
    d["vol20"] = g["ret"].rolling(vol_window, min_periods=10).std().reset_index(level=0, drop=True)
    d["atr_pct"] = g["tr_pct"].rolling(atr_window, min_periods=7).mean().reset_index(level=0, drop=True)
    d["avg_turnover"] = g["turnover"].shift(1).groupby(d["symbol"]).rolling(vol_window, min_periods=5).mean() \
        .reset_index(level=0, drop=True)
    d["avg_volume"] = g["volume"].shift(1).groupby(d["symbol"]).rolling(vol_window, min_periods=5).mean() \
        .reset_index(level=0, drop=True)
    d["ret_5d"] = g["close"].transform(lambda c: c / c.shift(5) - 1)   # raw closes: may include splits, shown as context
    d["days"] = g.cumcount() + 1
    last = d.groupby("symbol", sort=False).tail(1).copy()
    last["z"] = last["ret"] / last["vol_prior"]
    last["vol_ann"] = last["vol20"] * np.sqrt(252)
    last["turnover_cr"] = last["turnover"] / 1e7
    last["avg_turnover_cr"] = last["avg_turnover"] / 1e7
    last["volume_x"] = last["volume"] / last["avg_volume"]
    last["circuit"] = [_circuit(r) for r in last.itertuples()]
    last["note"] = [GROUP_NOTES.get(gp, "") for gp in last["grp"]]
    return last.reset_index(drop=True)


def _circuit(r) -> str:
    if r.high == r.low and r.volume and r.volume > 0:
        return "locked (high = low)"
    for b in BANDS:
        if r.ret >= b - 0.0025 and r.ret <= b + 0.0005 and np.isclose(r.close, r.high, rtol=1e-4):
            return f"at upper circuit (~{b:.0%})"
        if r.ret <= -b + 0.0025 and r.ret >= -b - 0.0005 and np.isclose(r.close, r.low, rtol=1e-4):
            return f"at lower circuit (~{b:.0%})"
    return ""


def scan(df: pd.DataFrame, cfg: dict) -> dict:
    """Apply the universe filters and build the ranked lists."""
    m = compute_metrics(df)
    if m.empty:
        return {"asof": None, "universe": 0, "eligible": 0, "lists": {}, "excluded": {}}
    asof = m["date"].max()
    m = m[m["date"] == asof]                                     # only stocks that traded on the latest day
    universe = len(m)
    reasons = {
        f"group not in {', '.join(cfg['groups'])}": ~m["grp"].isin(cfg["groups"]),
        f"price below Rs {cfg['min_price']:g}": m["close"] < cfg["min_price"],
        f"avg turnover below Rs {cfg['min_avg_turnover_cr']:g} cr/day": ~(m["avg_turnover_cr"] >= cfg["min_avg_turnover_cr"]),
        f"less than {cfg['min_history_days']} days of history": m["days"] < cfg["min_history_days"],
    }
    keep = ~pd.concat(reasons.values(), axis=1).any(axis=1)
    excluded = {k: int(v.sum()) for k, v in reasons.items()}   # a stock can fail several filters
    e = m[keep].copy()
    n = cfg["top_n"]
    lists = {
        "gainers": e.nlargest(n, "ret"),
        "losers": e.nsmallest(n, "ret"),
        "unusual": e[e["z"].abs() >= cfg["unusual_z"]].reindex(e["z"].abs().sort_values(ascending=False).index)
                   .dropna(subset=["z"]).head(n),
        "volatile": e.nlargest(n, "vol_ann"),
    }
    return {"asof": asof, "universe": universe, "eligible": len(e), "lists": lists, "excluded": excluded,
            "all": e}


SCAN_NOTE = ("Watchlist, not advice: these stocks moved the most or are the most volatile. Big movers are often the "
             "hardest to trade (circuit limits, gaps, wide spreads, news risk), and research on short-term reversal "
             "shows yesterday's biggest movers do not reliably keep going. Check the news and liquidity before acting.")
