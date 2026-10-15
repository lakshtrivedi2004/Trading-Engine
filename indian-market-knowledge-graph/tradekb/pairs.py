"""Pair trading / statistical arbitrage (Varsity M10).

Method 1 [pair_trading_ratio_method]: ratio density curve.
Method 2 [pair_trading_regression_method]: regression residual z-score with ADF stationarity,
error-ratio orientation and beta-neutral sizing.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .options import norm_cdf


# ---------------------------------------------------------------- method 1

def ratio_density(price_a: pd.Series, price_b: pd.Series, lookback: int = 496) -> pd.DataFrame:
    """ratio = A/B; density = Phi((ratio-mean)/sd) over the lookback window."""
    ratio = price_a / price_b
    mean = ratio.rolling(lookback, min_periods=lookback // 2).mean()
    sd = ratio.rolling(lookback, min_periods=lookback // 2).std()
    z = (ratio - mean) / sd
    density = z.apply(lambda v: norm_cdf(v) if pd.notna(v) else np.nan)
    sig = np.where(density <= 0.025, 1, np.where(density >= 0.975, -1, 0))
    return pd.DataFrame({"ratio": ratio, "mean": mean, "sd": sd, "z": z, "density": density,
                         "signal": sig}, index=ratio.index)  # +1 = buy A sell B


# ---------------------------------------------------------------- regression + ADF

@dataclass
class Regression:
    beta: float
    intercept: float
    residuals: pd.Series
    se_regression: float      # std error of residuals
    se_intercept: float

    @property
    def error_ratio(self) -> float:
        """[error_ratio] SE(intercept)/SE(regression) - lower orientation wins."""
        return self.se_intercept / self.se_regression


def ols(y: pd.Series, x: pd.Series) -> Regression:
    """[linear_regression] y = beta*x + c + e."""
    df = pd.concat([y, x], axis=1, keys=["y", "x"]).dropna()
    X = np.column_stack([np.ones(len(df)), df.x.to_numpy()])
    coef, *_ = np.linalg.lstsq(X, df.y.to_numpy(), rcond=None)
    resid = df.y.to_numpy() - X @ coef
    n = len(df)
    s2 = resid @ resid / (n - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    return Regression(beta=float(coef[1]), intercept=float(coef[0]),
                      residuals=pd.Series(resid, index=df.index), se_regression=math.sqrt(s2),
                      se_intercept=math.sqrt(cov[0, 0]))


# MacKinnon (1994/2010) response-surface coefficients for the ADF tau with constant, 1 variable
_TAU_MAX, _TAU_MIN, _TAU_STAR = 2.74, -18.83, -1.61
_SMALLP = (2.1659, 1.4412, 0.038269)
_LARGEP = (1.7339, 0.93202, -0.12745, -0.010368)


def _mackinnon_p(t: float) -> float:
    if t > _TAU_MAX:
        return 1.0
    if t < _TAU_MIN:
        return 0.0
    c = _SMALLP if t <= _TAU_STAR else _LARGEP
    return norm_cdf(sum(ci * t ** i for i, ci in enumerate(c)))


def adf(series: pd.Series, max_lag: int | None = None) -> dict:
    """[stationarity] Augmented Dickey-Fuller test (constant, AIC lag choice).

    Uses statsmodels if installed, otherwise a numpy implementation with MacKinnon approximate
    p-values. Varsity rule: p <= 0.05 -> stationary residuals -> tradable pair.
    Note: strictly, residuals of an estimated regression need Engle-Granger critical values
    (~-3.34 at 5% vs -2.86); Varsity uses plain ADF p-values - treat borderline p as a reject."""
    s = pd.Series(series).dropna().to_numpy(float)
    try:
        from statsmodels.tsa.stattools import adfuller  # type: ignore
        r = adfuller(s, maxlag=max_lag, autolag="AIC")
        return {"stat": float(r[0]), "pvalue": float(r[1]), "lags": int(r[2]), "engine": "statsmodels"}
    except ImportError:
        pass
    n = len(s)
    if max_lag is None:
        max_lag = int(12 * (n / 100) ** 0.25)
    dy = np.diff(s)
    best = None
    for p in range(max_lag + 1):
        y = dy[max_lag:]
        cols = [np.ones(len(y)), s[max_lag:-1]]
        cols += [dy[max_lag - i: len(dy) - i] for i in range(1, p + 1)]
        X = np.column_stack(cols)
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ coef
        nobs = len(y)
        s2 = res @ res / (nobs - X.shape[1])
        aic = nobs * math.log(res @ res / nobs) + 2 * X.shape[1]
        if best is None or aic < best[0]:
            se = math.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
            best = (aic, coef[1] / se, p)
    _, t, p = best
    return {"stat": float(t), "pvalue": _mackinnon_p(t), "lags": p, "engine": "numpy-mackinnon"}


@dataclass
class PairModel:
    y: str
    x: str
    reg: Regression
    adf_p: float
    z: float
    signal: str
    tradable: bool
    reasons: list[str]

    def quantities(self, lots_y: int, lot_y: int, lot_x: int) -> dict:
        """Beta-neutral sizing: shares_x = beta * shares_y; round to lots (top up in cash if needed)."""
        shares_y = lots_y * lot_y
        shares_x = self.reg.beta * shares_y
        return {"shares_y": shares_y, "shares_x": shares_x, "lots_x": shares_x / lot_x}


def analyse_pair(prices: pd.DataFrame, a: str, b: str, lookback: int = 200, entry_z: float = 2.5,
                 stop_z: float = 3.0, exit_z: float = 1.0, max_intercept_share: float = 0.5) -> PairModel:
    """Full Varsity stat-arb pipeline for one pair on the last `lookback` bars."""
    df = prices[[a, b]].dropna().iloc[-lookback:]
    r1, r2 = ols(df[a], df[b]), ols(df[b], df[a])
    (y, x, reg) = (a, b, r1) if r1.error_ratio <= r2.error_ratio else (b, a, r2)
    p = adf(reg.residuals)["pvalue"]
    z = float(reg.residuals.iloc[-1] / reg.se_regression)
    reasons = []
    if p > 0.05:
        reasons.append(f"residuals not stationary (ADF p={p:.3f})")
    if reg.beta <= 0:
        reasons.append("negative beta")
    share = abs(reg.intercept) / float(df[y].iloc[-1])
    if share > max_intercept_share:
        reasons.append(f"intercept explains {share:.0%} of price_y")
    if z <= -stop_z or z >= stop_z:
        signal = "stop_zone"
    elif z <= -entry_z:
        signal = f"long_pair: buy {y}, sell {reg.beta:.2f}x {x}"
    elif z >= entry_z:
        signal = f"short_pair: sell {y}, buy {reg.beta:.2f}x {x}"
    elif abs(z) <= exit_z:
        signal = "exit_zone"
    else:
        signal = "hold"
    return PairModel(y, x, reg, p, z, signal, not reasons, reasons)


def screen_pairs(prices: pd.DataFrame, lookback: int = 200, **kw) -> pd.DataFrame:
    """[stationarity] bot_use - re-screen all pairs in a sector weekly."""
    rows = []
    for a, b in itertools.combinations(prices.columns, 2):
        m = analyse_pair(prices, a, b, lookback, **kw)
        rows.append({"y": m.y, "x": m.x, "beta": m.reg.beta, "intercept": m.reg.intercept,
                     "error_ratio": m.reg.error_ratio, "adf_p": m.adf_p, "z": m.z, "signal": m.signal,
                     "tradable": m.tradable, "reasons": "; ".join(m.reasons)})
    return pd.DataFrame(rows).sort_values(["tradable", "adf_p"], ascending=[False, True])
