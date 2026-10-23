"""Daily move decomposition: "why did the index move today?"

Method: for each day t, factor sensitivities (betas) are estimated by OLS on the previous
`window` days only (t-window .. t-1), then applied to day t's factor moves. The day's own data
never enters its own estimation. Whatever the factors cannot explain is the index-specific
part (domestic news, flows, positioning, or noise).

Research basis: variance-decomposition studies (Roll 1988; Cutler, Poterba & Summers 1989)
and standard linear factor models. Calendar alignment: US markets close after India, so US
data for date d is mapped to the next Indian session (merge_asof, strictly backward).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def factor_change_on_calendar(levels: pd.Series, dates, kind: str, timing: str) -> pd.Series:
    """Change in a factor between consecutive Indian trading dates.

    same_day:     level at or before the Indian date
    prev_session: level strictly BEFORE the Indian date (US closes after India)
    Using levels (not pre-computed returns) handles holiday mismatches: if two US sessions
    happen between Indian dates both count; if none happens the change is zero."""
    s = levels.dropna().sort_index()
    left = pd.DataFrame({"date": pd.DatetimeIndex(dates)}).sort_values("date")
    right = pd.DataFrame({"fdate": s.index, "lvl": s.to_numpy(float)})
    merged = pd.merge_asof(left, right, left_on="date", right_on="fdate", direction="backward",
                           allow_exact_matches=(timing == "same_day"))
    lvl = pd.Series(merged["lvl"].to_numpy(), index=merged["date"].to_numpy())
    return lvl / lvl.shift(1) - 1 if kind == "ret" else lvl.diff()


def build_factor_frame(index_name: str, idx_df: pd.DataFrame, indices: dict, factors: dict, settings,
                       flows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Index return (`_ret`) plus the factor moves configured to explain it.

    If `flows` is given, FII net flow (Rs '000 crore, same day) is added as a factor; flows are
    published after the close, so this is explanation only, never a trading input."""
    dates = idx_df.index
    cols = {"_ret": idx_df["Close"].pct_change()}
    for f in settings.decomposition.get(index_name, []):
        if f in factors:
            spec = settings.factors[f]
            cols[f] = factor_change_on_calendar(factors[f]["Close"], dates, spec.kind, spec.timing)
        elif f in indices:
            cols[f] = factor_change_on_calendar(indices[f]["Close"], dates, "ret", "same_day")
    frame = pd.DataFrame(cols, index=dates)
    if flows is not None and "fii_net" in flows:
        frame["FII net flow"] = flows["fii_net"].reindex(dates) / 1000.0
    return frame


def decompose(frame: pd.DataFrame, window: int = 250):
    """Rolling out-of-sample decomposition. Returns (contributions, betas) or (None, None)."""
    data = frame.dropna()
    fnames = [c for c in data.columns if c != "_ret"]
    if len(data) < window + 20 or not fnames:
        return None, None
    y = data["_ret"].to_numpy()
    X = np.column_stack([np.ones(len(data)), data[fnames].to_numpy()])
    rows, beta_rows, resid_sd = [], [], []
    for t in range(window, len(data)):
        Xw, yw = X[t - window:t], y[t - window:t]
        coef, *_ = np.linalg.lstsq(Xw, yw, rcond=None)
        fit_resid = yw - Xw @ coef
        rows.append(coef[1:] * X[t, 1:])
        beta_rows.append(coef[1:])
        resid_sd.append(fit_resid.std())
    idx = data.index[window:]
    contrib = pd.DataFrame(rows, index=idx, columns=fnames)
    contrib.insert(0, "actual", y[window:])
    contrib["explained"] = contrib[fnames].sum(axis=1)
    contrib["index-specific"] = contrib["actual"] - contrib["explained"]
    contrib["resid_sd"] = resid_sd
    for f in fnames:
        contrib[f"move:{f}"] = data[f].to_numpy()[window:]
    betas = pd.DataFrame(beta_rows, index=idx, columns=fnames)
    return contrib, betas


def explanatory_power(contrib: pd.DataFrame | None, last_days: int | None = None) -> float:
    """Out-of-sample R²: share of daily variance explained by the factors."""
    if contrib is None:
        return np.nan
    c = contrib if last_days is None else contrib.tail(last_days)
    if len(c) < 30:
        return np.nan
    return 1 - c["index-specific"].var() / c["actual"].var()


def factor_names(contrib: pd.DataFrame) -> list[str]:
    return [c[5:] for c in contrib.columns if c.startswith("move:")]
