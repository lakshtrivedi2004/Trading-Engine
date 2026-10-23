"""Statistics helpers used across the research modules."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def newey_west_t(x, lags=5):
    """t-statistic of the mean, robust to autocorrelation and changing volatility.
    Plain t-stats overstate significance for overlapping or clustered returns."""
    x = pd.Series(x).dropna().values
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


def welch_t(a, b):
    """t-statistic for difference in means between two groups."""
    a, b = pd.Series(a).dropna(), pd.Series(b).dropna()
    if len(a) < 5 or len(b) < 5:
        return np.nan
    se = np.sqrt(a.var() / len(a) + b.var() / len(b))
    return (a.mean() - b.mean()) / se if se > 0 else np.nan


def perf(daily):
    """Annualised performance summary of a daily return series."""
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


def split_index(index, train_fraction):
    """Chronological split: never shuffle time series."""
    cut = int(len(index) * train_fraction)
    return index[:cut], index[cut:]


def verdict(t_in, t_out, same_sign):
    """Evidence grading. Uses the stricter t>3 hurdle suggested by
    Harvey, Liu & Zhu (2016) for in-sample discovery, then requires the
    effect to survive on data it was not found in."""
    if np.isnan(t_in) or np.isnan(t_out):
        return "Not enough data"
    if abs(t_in) >= 3 and same_sign and abs(t_out) >= 2:
        return "Strong: holds out-of-sample"
    if abs(t_in) >= 2 and same_sign and abs(t_out) >= 1.5:
        return "Moderate: holds, but weakly"
    if abs(t_in) >= 2:
        return "Faded: did not hold out-of-sample"
    return "No reliable evidence"
