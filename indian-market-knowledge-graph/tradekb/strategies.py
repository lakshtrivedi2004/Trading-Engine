"""Signal generators for Varsity systems (M2, M4, M10) - each returns a position/flag series."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind


def ma_price_crossover(close: pd.Series, n: int = 50) -> pd.Series:
    """[ma_price_crossover_system] long while close > EMA(n), flat otherwise."""
    e = ind.ema(close, n)
    return (close > e).astype(int).where(e.notna(), 0)


def ma_crossover(close: pd.Series, fast: int = 9, slow: int = 21) -> pd.Series:
    """[ma_crossover_system] long while EMA(fast) > EMA(slow)."""
    f, s = ind.ema(close, fast), ind.ema(close, slow)
    return (f > s).astype(int).where(s.notna(), 0)


def rsi_signals(close: pd.Series, n: int = 14, lo: float = 30, hi: float = 70) -> pd.DataFrame:
    """[rsi] classical zones plus Varsity's exit-of-extreme crosses."""
    r = ind.rsi(close, n)
    return pd.DataFrame({"rsi": r, "oversold": r < lo, "overbought": r > hi,
                         "long_cross": ind.crossover(r, lo), "short_cross": ind.crossunder(r, hi)})


def macd_signals(close: pd.Series) -> pd.DataFrame:
    """[macd] signal-line and centre-line crossovers."""
    m = ind.macd(close)
    return m.assign(signal_bull=ind.crossover(m.macd, m.signal), signal_bear=ind.crossunder(m.macd, m.signal),
                    center_bull=ind.crossover(m.macd, 0.0), center_bear=ind.crossunder(m.macd, 0.0))


def bollinger_reversion(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """[bollinger_bands] touch lower -> long to middle; touch upper -> short to middle.
    Suppressed while bands expand (trend) - Varsity's 'envelope expansion' failure mode."""
    b = ind.bollinger(close, n, k)
    expanding = b.width > b.width.rolling(n).mean() * 1.2
    return b.assign(long=(close <= b.lower) & ~expanding, short=(close >= b.upper) & ~expanding,
                    expanding=expanding)


def adx_di(df: pd.DataFrame, n: int = 14, threshold: float = 25) -> pd.DataFrame:
    """[adx] buy: ADX>=25 and +DI crosses above -DI; sell: mirror."""
    a = ind.adx(df, n)
    return a.assign(buy=(a.adx >= threshold) & ind.crossover(a.plus_di, a.minus_di),
                    sell=(a.adx >= threshold) & ind.crossover(a.minus_di, a.plus_di))


def supertrend_signals(df: pd.DataFrame, n: int = 7, mult: float = 3.0) -> pd.DataFrame:
    """[supertrend] buy when direction flips to +1, sell when it flips to -1."""
    st = ind.supertrend(df, n, mult)
    d = st.direction
    return st.assign(buy=(d == 1) & (d.shift() == -1), sell=(d == -1) & (d.shift() == 1))


def statistical_calendar_spread(near: pd.Series, far: pd.Series, lookback: int = 200, k: float = 1.0) -> pd.DataFrame:
    """[statistical_calendar_spread] spread = far - near; sell spread above mean+k*sd, buy below mean-k*sd, exit at mean."""
    spread = far - near
    m = spread.rolling(lookback).mean()
    sd = spread.rolling(lookback).std()
    return pd.DataFrame({"spread": spread, "mean": m, "upper": m + k * sd, "lower": m - k * sd,
                         "sell_spread": spread > m + k * sd, "buy_spread": spread < m - k * sd})


def futures_fair_value(spot: float, rate: float, days: int, dividend: float = 0.0) -> float:
    """[futures_pricing] spot*(1 + r*days/365) - dividend."""
    return spot * (1 + rate * days / 365) - dividend


def cash_and_carry(spot: float, futures: float, rate: float, days: int, costs: float = 0.0) -> dict:
    """[cash_and_carry] basis vs fair value; positive edge => buy spot, sell futures."""
    fv = futures_fair_value(spot, rate, days)
    return {"fair_value": fv, "basis": futures - spot, "mispricing": futures - fv,
            "edge_after_costs": futures - fv - costs, "trade": "buy_spot_sell_futures" if futures - fv > costs
            else "sell_spot_buy_futures" if fv - futures > costs else "none"}


def momentum_portfolio(closes: pd.DataFrame, top_n: int = 12, lookback: int = 252,
                       regime: pd.Series | None = None, regime_ma: int = 200) -> pd.DataFrame:
    """[momentum_portfolio] month-end ranking by trailing 12m return; equal-weight top N.

    regime: optional index close series - if below its `regime_ma`-day SMA at rebalance, hold cash
    (bot_use regime filter suggested in the KG, not in the source text).
    Returns a DataFrame of weights indexed by rebalance date."""
    score = closes / closes.shift(lookback) - 1
    month_ends = closes.groupby(closes.index.to_period("M")).tail(1).index
    rows = {}
    for d in month_ends:
        s = score.loc[d].dropna()
        w = pd.Series(0.0, index=closes.columns)
        risk_on = True
        if regime is not None:
            ma = regime.rolling(regime_ma).mean()
            risk_on = bool(regime.loc[:d].iloc[-1] > ma.loc[:d].iloc[-1]) if ma.loc[:d].notna().any() else True
        if len(s) >= top_n and risk_on:
            w[s.nlargest(top_n).index] = 1.0 / top_n
        rows[d] = w
    return pd.DataFrame(rows).T


def backtest_positions(close: pd.Series, position: pd.Series, cost_bps: float = 10.0) -> pd.DataFrame:
    """[backtesting] next-bar execution of a 0/1/-1 position series with per-turn cost in bps."""
    pos = position.shift().fillna(0)
    ret = close.pct_change().fillna(0)
    turns = pos.diff().abs().fillna(pos.abs())
    strat = pos * ret - turns * cost_bps / 1e4
    return pd.DataFrame({"position": pos, "ret": strat, "equity": (1 + strat).cumprod()})


def backtest_weights(closes: pd.DataFrame, weights: pd.DataFrame, cost_bps: float = 10.0) -> pd.DataFrame:
    """Daily backtest of rebalance-date weights (held until next rebalance)."""
    w = weights.reindex(closes.index).ffill().shift().fillna(0)
    rets = closes.pct_change().fillna(0)
    turnover = w.diff().abs().sum(axis=1).fillna(0)
    strat = (w * rets).sum(axis=1) - turnover * cost_bps / 1e4
    return pd.DataFrame({"ret": strat, "equity": (1 + strat).cumprod()})


def index_valuation_regime(nifty_pe: float) -> str:
    """[index_valuation] Varsity heuristic: >=22 caution, <=16 attractive."""
    return "caution" if nifty_pe >= 22 else "attractive" if nifty_pe <= 16 else "neutral"


def seasonality(close: pd.Series) -> pd.DataFrame:
    """[seasonality_test] month-of-year average return and hit rate."""
    m = close.resample("ME").last().pct_change().dropna()
    g = m.groupby(m.index.month)
    return pd.DataFrame({"avg_return": g.mean(), "hit_rate": g.apply(lambda s: (s > 0).mean()), "n": g.size()})
