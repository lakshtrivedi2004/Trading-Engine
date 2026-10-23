import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tradekb import KG, candles, indicators as ind, options as opt, pairs, risk, strategies as st, valuation as val


def ohlc(closes, spread=0.01):
    c = pd.Series(closes, dtype=float, index=pd.bdate_range("2024-01-01", periods=len(closes)))
    o = c.shift().fillna(c.iloc[0])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + spread), "low": np.minimum(o, c) * (1 - spread),
                         "close": c, "volume": 1000.0})


@pytest.fixture(scope="module")
def walk():
    rng = np.random.default_rng(1)
    return ohlc(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))


# ---------------------------------------------------------------- knowledge graph
def test_kg_loads_and_queries():
    kg = KG()
    assert kg.meta["node_count"] > 300
    assert kg.resolve("RSI") == "rsi" or "rsi" in kg
    assert kg.search("pair trading regression z-score")[0][0].startswith("pair_trading")
    assert "z <=" in kg.context("pair_trading_regression_method")
    assert kg.path("rsi", "black_scholes")
    assert all(e["target"] in kg.nodes and e["source"] in kg.nodes for e in kg.edges)


# ---------------------------------------------------------------- indicators
def test_rsi_bounds_and_trend(walk):
    r = ind.rsi(walk.close).dropna()
    assert r.between(0, 100).all()
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100


def test_macd_bollinger_atr_shapes(walk):
    m = ind.macd(walk.close)
    assert np.allclose((m.macd - m.signal).dropna(), m["hist"].dropna())
    b = ind.bollinger(walk.close).dropna()
    assert (b.upper >= b.middle).all() and (b.lower <= b.middle).all()
    assert (ind.atr(walk).dropna() > 0).all()
    assert ind.adx(walk).adx.dropna().between(0, 100).all()
    assert set(ind.supertrend(walk).direction.unique()) <= {-1, 0, 1}


def test_vwap_single_session():
    idx = pd.date_range("2024-01-01 09:15", periods=3, freq="min")
    df = pd.DataFrame({"high": [11, 12, 13], "low": [9, 10, 11], "close": [10, 11, 12], "volume": [1, 1, 2]}, index=idx)
    assert ind.vwap(df).iloc[-1] == pytest.approx((10 + 11 + 24) / 4)


def test_fibonacci_and_pivots():
    lv = ind.fibonacci_levels(200, 100)
    assert lv[0.618] == pytest.approx(138.2)
    assert ind.pivot_points(110, 90, 100)["P"] == 100


# ---------------------------------------------------------------- candles
def test_bullish_engulfing_detected():
    closes = [130, 128, 126, 124, 122, 120, 118, 116, 114, 112, 110]
    df = ohlc(closes, 0.002)
    df.loc[df.index[-1]] = {"open": 109, "high": 113, "low": 108.5, "close": 112.5, "volume": 1}  # engulfs prior red
    df.iloc[-2, df.columns.get_loc("open")] = 111.5
    df.iloc[-2, df.columns.get_loc("close")] = 110
    p = candles.detect_all(df)
    assert p["bullish_engulfing"].iloc[-1]
    plans = candles.trade_plans(df, p)
    row = plans[plans.pattern == "bullish_engulfing"].iloc[-1]
    assert row.side == "long" and row.stop < row.entry


# ---------------------------------------------------------------- options
def test_black_scholes_reference():
    # Hull example: S=42, K=40, r=10%, vol=20%, T=0.5y -> call 4.76, put 0.81
    c = opt.black_scholes(42, 40, 0.5 * 365, 0.2, 0.10, kind="call")
    p = opt.black_scholes(42, 40, 0.5 * 365, 0.2, 0.10, kind="put")
    assert c.price == pytest.approx(4.76, abs=0.01) and p.price == pytest.approx(0.81, abs=0.01)
    assert 0 < c.delta < 1 and -1 < p.delta < 0 and c.gamma > 0 and c.theta < 0
    assert opt.put_call_parity_gap(c.price, p.price, 42, 40, 0.5 * 365, 0.10) == pytest.approx(0, abs=1e-9)


def test_implied_vol_roundtrip():
    px = opt.black_scholes(18000, 18200, 20, 0.15, 0.065).price
    assert opt.implied_vol(px, 18000, 18200, 20, 0.065) == pytest.approx(0.15, abs=1e-4)


def test_norm_ppf():
    assert opt.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)


def test_max_pain_and_pcr():
    s, k = opt.max_pain([100, 110, 120], call_oi=[10, 50, 100], put_oi=[100, 50, 10])
    assert s == 110
    assert opt.pcr([130], [100])["view"] == "neutral"
    assert opt.select_strategy("moderately_bullish", "low_iv") == ["bull_call_spread"]


def test_straddle_payoff():
    legs = [dict(strike=100, premium=5, kind="call", side=1), dict(strike=100, premium=5, kind="put", side=1)]
    assert opt.strategy_payoff([100, 120], legs).tolist() == [-10, 10]


# ---------------------------------------------------------------- risk
def test_sizing_and_kelly():
    assert risk.position_size_risk(500000, 0.015, 100, 95) == 1500
    assert risk.kelly(0.6, 1.5) == pytest.approx(0.6 - 0.4 / 1.5)
    assert risk.reward_risk(100, 95, 110) == 2
    assert risk.recovery_needed(0.5) == pytest.approx(1.0)
    assert risk.hedge_lots(1_000_000, 1.2, 20000, 50) == pytest.approx(1.2)


def test_xirr_and_cagr():
    assert risk.cagr(100, 200, 5) == pytest.approx(2 ** 0.2 - 1)
    r = risk.xirr([(date(2020, 1, 1), -100), (date(2021, 1, 1), 110)])
    assert r == pytest.approx(0.1, abs=1e-3)


def test_var_and_drawdown(walk):
    r = walk.close.pct_change().dropna()
    v = risk.var_historical(r)
    assert v["cvar"] >= v["var"] > 0
    assert risk.max_drawdown(walk.close)["max_dd"] <= 0
    assert risk.trade_stats([10, -5, 20, -5])["win_rate"] == 0.5


# ---------------------------------------------------------------- pairs
def test_adf_detects_stationarity():
    rng = np.random.default_rng(7)
    noise = pd.Series(rng.normal(size=500))
    rw = noise.cumsum()
    assert pairs.adf(noise)["pvalue"] < 0.01
    assert pairs.adf(rw)["pvalue"] > 0.05


def test_pair_pipeline_cointegrated():
    rng = np.random.default_rng(3)
    x = 100 + np.cumsum(rng.normal(0, 1, 300))
    y = 1.5 * x + 20 + rng.normal(0, 1, 300)
    y[-1] += -4  # push residual to a long-pair extreme
    df = pd.DataFrame({"Y": y, "X": x})
    m = pairs.analyse_pair(df, "Y", "X", lookback=200)
    assert m.adf_p <= 0.05 and m.tradable
    assert m.reg.beta == pytest.approx(1.5, rel=0.05) if m.y == "Y" else m.reg.beta == pytest.approx(1 / 1.5, rel=0.05)
    assert m.z < -2


# ---------------------------------------------------------------- strategies / valuation
def test_futures_fair_value_and_carry():
    assert st.futures_fair_value(10000, 0.08, 30) == pytest.approx(10000 * (1 + 0.08 * 30 / 365))
    assert st.cash_and_carry(10000, 10200, 0.08, 30)["trade"] == "buy_spot_sell_futures"


def test_momentum_portfolio_weights():
    idx = pd.bdate_range("2022-01-03", periods=400)
    closes = pd.DataFrame({f"S{i}": 100 * (1 + 0.0005 * i) ** np.arange(400) for i in range(20)}, index=idx)
    w = st.momentum_portfolio(closes, top_n=5)
    last = w.iloc[-1]
    assert last.sum() == pytest.approx(1.0) and last[["S19", "S18"]].gt(0).all()
    bt = st.backtest_weights(closes, w)
    assert bt.equity.iloc[-1] > 1


def test_dcf_and_wacc():
    assert val.capm(0.074586, 1.3, 0.085) == pytest.approx(0.0881, abs=1e-4)  # Varsity M13 example
    assert val.wacc(300, 200, 0.08, 0.12, 0.30) == pytest.approx(0.0816)       # Varsity M13 example
    r = val.dcf([100, 110, 120, 130, 140], 0.10, 0.04, net_debt=200, shares=10)
    assert r.fair_price == pytest.approx((r.enterprise_value - 200) / 10)
    assert 0 < r.tv_share < 1
    with pytest.raises(ValueError):
        val.terminal_value(100, 0.1, 0.1)
    rt = val.ratios({"pat": 10, "revenue": 100, "total_assets": 200, "equity": 50})
    assert rt["roe_dupont"] == pytest.approx(rt["roe"])
