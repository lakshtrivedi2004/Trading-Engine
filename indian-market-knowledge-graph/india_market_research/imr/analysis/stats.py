"""Statistics used across the research modules.

- Newey & West (1987): heteroskedasticity- and autocorrelation-consistent (HAC) standard errors.
  Plain t-statistics overstate significance for clustered/overlapping daily returns.
- Harvey, Liu & Zhu (2016): with many tests run, an in-sample discovery needs |t| >= 3.
- Chronological splits only (never shuffled), with a gap so no observation's target crosses
  from in-sample into out-of-sample.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def newey_west_t(x, lags: int = 5) -> float:
    """HAC t-statistic of the mean of x (Bartlett kernel). NaN with fewer than 30 observations."""
    x = pd.Series(x).dropna().to_numpy(float)
    n = len(x)
    if n < 30:
        return np.nan
    e = x - x.mean()
    var = e @ e / n
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)
        var += 2 * w * (e[lag:] @ e[:-lag]) / n
    se = np.sqrt(var / n) if var > 0 else np.nan
    return x.mean() / se if se else np.nan


def nw_diff_t(y, mask, lags: int = 5) -> tuple[float, float]:
    """Difference in mean of y on 'mask' days versus other days, with a Newey-West t-statistic.

    OLS y = a + b*D with HAC standard error for b (Newey & West 1987). Returns (b, t)."""
    df = pd.concat([pd.Series(y, dtype=float), pd.Series(mask, dtype=float)], axis=1, keys=["y", "d"]).dropna()
    n = len(df)
    k = df["d"].sum()
    if n < 30 or k < 5 or n - k < 5:
        return np.nan, np.nan
    X = np.column_stack([np.ones(n), df["d"].to_numpy()])
    yv = df["y"].to_numpy()
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ yv
    u = yv - X @ beta
    g = X * u[:, None]
    S = g.T @ g
    for lag in range(1, lags + 1):
        w = 1 - lag / (lags + 1)
        G = g[lag:].T @ g[:-lag]
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(V[1, 1]) if V[1, 1] > 0 else np.nan
    return float(beta[1]), float(beta[1] / se) if se else np.nan


def welch_t(a, b) -> float:
    """t-statistic for a difference in means between two independent groups (descriptive use)."""
    a, b = pd.Series(a).dropna(), pd.Series(b).dropna()
    if len(a) < 5 or len(b) < 5:
        return np.nan
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else np.nan


def perf(daily) -> dict:
    """Annualised summary of a daily return series: CAGR, volatility, Sharpe (rf=0), max drawdown."""
    r = pd.Series(daily).dropna()
    if len(r) < 20:
        return dict(cagr=np.nan, vol=np.nan, sharpe=np.nan, maxdd=np.nan, days=len(r))
    wealth = (1 + r).cumprod()
    years = len(r) / TRADING_DAYS
    cagr = wealth.iloc[-1] ** (1 / years) - 1 if wealth.iloc[-1] > 0 else -1.0
    vol = r.std() * np.sqrt(TRADING_DAYS)
    sharpe = r.mean() / r.std() * np.sqrt(TRADING_DAYS) if r.std() > 0 else np.nan
    maxdd = (wealth / wealth.cummax() - 1).min()
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, maxdd=maxdd, days=len(r))


def split_index(index, train_fraction: float, gap: int = 1):
    """Chronological split; `gap` observations between the parts are dropped so a day-t signal whose
    target is day t+1 can never straddle the boundary."""
    index = pd.Index(index)
    cut = int(len(index) * train_fraction)
    return index[:cut], index[cut + gap:]


def verdict(t_in: float, t_out: float, same_sign: bool) -> str:
    """Evidence grading (unchanged from the prototype).

    Strong: in-sample |t| >= 3 (Harvey, Liu & Zhu 2016) AND out-of-sample |t| >= 2, same sign.
    Moderate: in-sample |t| >= 2 AND out-of-sample |t| >= 1.5, same sign.
    Faded: in-sample |t| >= 2 but it did not hold out-of-sample.
    Otherwise: No reliable evidence."""
    if t_in is None or t_out is None or np.isnan(t_in) or np.isnan(t_out):
        return "Not enough data"
    if abs(t_in) >= 3 and same_sign and abs(t_out) >= 2:
        return "Strong: holds out-of-sample"
    if abs(t_in) >= 2 and same_sign and abs(t_out) >= 1.5:
        return "Moderate: holds, but weakly"
    if abs(t_in) >= 2:
        return "Faded: did not hold out-of-sample"
    return "No reliable evidence"


VERDICT_EXPLAINED = {
    "Strong: holds out-of-sample": "Found with a strict in-sample bar (|t| ≥ 3) AND it kept working, in the same "
                                   "direction, on later data it had never seen. Still re-check it over time.",
    "Moderate: holds, but weakly": "Visible in-sample (|t| ≥ 2) and the later data points the same way, but not "
                                   "strongly. Treat as a lead to watch, not a finding.",
    "Faded: did not hold out-of-sample": "Looked significant in the older data, but did not hold on newer data. "
                                         "Typical of chance findings or effects that disappeared.",
    "No reliable evidence": "Not distinguishable from random noise. This is the normal, expected result.",
    "Not enough data": "Too few cases to say anything.",
}
