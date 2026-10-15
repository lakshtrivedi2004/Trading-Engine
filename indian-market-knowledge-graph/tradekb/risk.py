"""Risk, sizing and performance metrics (Varsity M2, M4, M9, M11)."""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- trade-level

def reward_risk(entry: float, stop: float, target: float) -> float:
    """[reward_risk_ratio] works for long (stop < entry) and short (stop > entry)."""
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk else float("inf")


def position_size_risk(equity: float, risk_pct: float, entry: float, stop: float, lot_size: int = 1) -> int:
    """[position_sizing] % risk model: units (or lots) = equity*risk% / (|entry-stop| * lot_size)."""
    per_unit = abs(entry - stop) * lot_size
    return int(equity * risk_pct / per_unit) if per_unit > 0 else 0


def position_size_volatility(equity: float, vol_pct: float, atr_value: float, lot_size: int = 1) -> int:
    """[position_sizing] % volatility model: units = equity*vol% / ATR."""
    return int(equity * vol_pct / (atr_value * lot_size)) if atr_value > 0 else 0


def kelly(win_rate: float, win_loss_ratio: float, max_risk_pct: float | None = None) -> float:
    """[kelly_criterion] K = W - (1-W)/R; optionally scale to max_risk_pct (Varsity caps usage)."""
    k = win_rate - (1 - win_rate) / win_loss_ratio
    k = max(k, 0.0)
    return k * max_risk_pct if max_risk_pct is not None else k


def expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """[expectancy] per-trade expected P&L (avg_loss positive)."""
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def volatility_stop(entry: float, daily_vol: float, holding_days: int, side: str = "long") -> float:
    """[volatility_stop_loss] entry*(1 -/+ daily_vol*sqrt(days))."""
    m = daily_vol * math.sqrt(holding_days)
    return entry * (1 - m) if side == "long" else entry * (1 + m)


def ta_checklist(has_pattern: bool, stop: float, nearest_sr: float | None, volume_ok: bool,
                 rrr: float, indicators_confirm: bool, min_rrr: float = 1.5, sr_tol: float = 0.04) -> dict:
    """[ta_checklist] 4 must-haves + indicator confirmation -> size multiplier.

    Returns {'score', 'take', 'size_mult'}; size 1.0 if indicators confirm, else ~0.6 (Varsity 500 vs 300)."""
    sr_ok = nearest_sr is not None and abs(stop - nearest_sr) / stop <= sr_tol
    checks = {"pattern": has_pattern, "sr_near_stop": sr_ok, "volume": volume_ok, "rrr": rrr >= min_rrr}
    score = sum(checks.values())
    take = has_pattern and checks["rrr"] and score >= 3
    return {"checks": checks, "score": score, "take": take,
            "size_mult": (1.0 if indicators_confirm else 0.6) if take else 0.0}


# ---------------------------------------------------------------- portfolio

def beta(stock_returns: pd.Series, index_returns: pd.Series) -> float:
    """[beta] cov(rs, rm)/var(rm) on aligned daily returns."""
    df = pd.concat([stock_returns, index_returns], axis=1).dropna()
    return float(np.cov(df.iloc[:, 0], df.iloc[:, 1], ddof=1)[0, 1] / np.var(df.iloc[:, 1], ddof=1))


def hedge_lots(portfolio_value: float, portfolio_beta: float, futures_price: float, lot_size: int) -> float:
    """[portfolio_hedge] index futures lots to short = beta*value / (futures*lot)."""
    return portfolio_beta * portfolio_value / (futures_price * lot_size)


def portfolio_stats(returns: pd.DataFrame, weights, periods: int = 252) -> dict:
    """[expected_return]/[portfolio_variance] annualised E[R], sd and 1/2/3 SD ranges."""
    w = np.asarray(weights, float)
    mu = float(returns.mean().to_numpy() @ w) * periods
    sd = float(math.sqrt(w @ returns.cov().to_numpy() @ w) * math.sqrt(periods))
    return {"exp_return": mu, "sd": sd, "range_1sd": (mu - sd, mu + sd), "range_2sd": (mu - 2 * sd, mu + 2 * sd),
            "range_3sd": (mu - 3 * sd, mu + 3 * sd)}


def min_variance_weights(returns: pd.DataFrame, n_samples: int = 20000, seed: int = 0) -> dict:
    """[portfolio_optimization] Monte-Carlo efficient frontier: min-variance and max-Sharpe(rf=0) long-only weights."""
    rng = np.random.default_rng(seed)
    mu, cov = returns.mean().to_numpy() * 252, returns.cov().to_numpy() * 252
    w = rng.dirichlet(np.ones(len(mu)), n_samples)
    er = w @ mu
    sd = np.sqrt(np.einsum("ij,jk,ik->i", w, cov, w))
    i_min, i_sh = int(np.argmin(sd)), int(np.argmax(er / sd))
    cols = list(returns.columns)
    return {"min_variance": dict(zip(cols, w[i_min])), "min_var_stats": (er[i_min], sd[i_min]),
            "max_sharpe": dict(zip(cols, w[i_sh])), "max_sharpe_stats": (er[i_sh], sd[i_sh])}


def var_historical(returns: pd.Series, level: float = 0.95) -> dict:
    """[value_at_risk] historical VaR and CVaR (expected shortfall), as positive loss fractions."""
    r = returns.dropna().to_numpy()
    q = float(np.quantile(r, 1 - level))
    return {"var": -q, "cvar": -float(r[r <= q].mean())}


# ---------------------------------------------------------------- performance

def absolute_return(start: float, end: float) -> float:
    return end / start - 1


def cagr(start: float, end: float, years: float) -> float:
    """[cagr]"""
    return (end / start) ** (1 / years) - 1


def xirr(cashflows: list[tuple[date, float]], guess: float = 0.1) -> float:
    """[return_measurement] XIRR; investments negative, redemptions positive."""
    t0 = min(d for d, _ in cashflows)
    ts = np.array([(d - t0).days / 365.0 for d, _ in cashflows])
    cf = np.array([v for _, v in cashflows], float)

    def f(r):
        return float((cf / (1 + r) ** ts).sum())
    lo, hi = -0.9999, 10.0
    if f(lo) * f(hi) > 0:
        raise ValueError("XIRR not bracketed")
    for _ in range(300):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def rolling_returns(nav: pd.Series, years: int = 3, periods: int = 252) -> pd.Series:
    """[return_measurement] rolling n-year CAGR."""
    n = years * periods
    return (nav / nav.shift(n)) ** (1 / years) - 1


def max_drawdown(equity: pd.Series) -> dict:
    """[equity_curve] max drawdown and its peak/trough dates."""
    peak = equity.cummax()
    dd = equity / peak - 1
    trough = dd.idxmin()
    return {"max_dd": float(dd.min()), "trough": trough, "peak": equity.loc[:trough].idxmax()}


def recovery_needed(drawdown: float) -> float:
    """[recovery_trauma] gain needed to recover a loss: 1/(1-d) - 1 (50% loss needs 100%)."""
    return 1 / (1 - drawdown) - 1


def sharpe(returns: pd.Series, rf_annual: float = 0.0, periods: int = 252) -> float:
    """[performance_metrics]"""
    ex = returns - rf_annual / periods
    return float(ex.mean() / ex.std() * math.sqrt(periods))


def sortino(returns: pd.Series, rf_annual: float = 0.0, periods: int = 252) -> float:
    """[performance_metrics] uses downside deviation of excess returns."""
    ex = returns - rf_annual / periods
    dd = math.sqrt(float((np.minimum(ex, 0) ** 2).mean()))
    return float(ex.mean() / dd * math.sqrt(periods)) if dd else float("inf")


def capture_ratios(fund: pd.Series, bench: pd.Series) -> dict:
    """[performance_metrics] upside / downside capture (%)."""
    df = pd.concat([fund, bench], axis=1, keys=["f", "b"]).dropna()
    up, dn = df[df.b > 0], df[df.b < 0]
    return {"upside_capture": 100 * up.f.mean() / up.b.mean(), "downside_capture": 100 * dn.f.mean() / dn.b.mean()}


def alpha(fund_ret: float, bench_ret: float, beta_value: float, rf: float) -> float:
    """[performance_metrics] Jensen's alpha."""
    return (fund_ret - rf) - beta_value * (bench_ret - rf)


def trade_stats(pnls) -> dict:
    """[trading_journal] win rate, payoff ratio, expectancy, kelly from a list of trade P&Ls."""
    p = np.asarray(pnls, float)
    wins, losses = p[p > 0], p[p <= 0]
    wr = len(wins) / len(p) if len(p) else 0.0
    aw = wins.mean() if len(wins) else 0.0
    al = -losses.mean() if len(losses) else 0.0
    return {"trades": len(p), "win_rate": wr, "avg_win": aw, "avg_loss": al,
            "payoff_ratio": aw / al if al else float("inf"),
            "expectancy": expectancy(wr, aw, al), "kelly": kelly(wr, aw / al) if al and aw else 0.0}
