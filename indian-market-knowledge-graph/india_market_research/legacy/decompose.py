"""
Daily move decomposition ("why did the index move today?").

Method: for each day, factor sensitivities (betas) are estimated by regression
on the PREVIOUS year of data only, then applied to that day's factor moves.
So each day's explanation uses no information from that day itself in the
estimation. Whatever the factors can't explain is the index-specific part
(domestic news, flows, positioning, or noise).

Research basis: variance-decomposition studies such as Roll (1988) and
Cutler, Poterba & Summers (1989), and standard factor models.
"""
import numpy as np
import pandas as pd

import config


def factor_change_on_calendar(levels, dates, kind, timing):
    """Change in a factor between consecutive Indian trading dates.

    same_day:     level at or before the Indian date
    prev_session: level strictly BEFORE the Indian date (US closes after India)
    Using levels (not pre-computed returns) handles holiday mismatches
    correctly: if two US sessions happen between Indian dates, both count;
    if none happens, the change is zero.
    """
    s = levels.dropna().sort_index()
    left = pd.DataFrame({"date": pd.DatetimeIndex(dates)}).sort_values("date")
    right = pd.DataFrame({"fdate": s.index, "lvl": s.values.astype(float)})
    merged = pd.merge_asof(left, right, left_on="date", right_on="fdate",
                           direction="backward",
                           allow_exact_matches=(timing == "same_day"))
    lvl = pd.Series(merged["lvl"].values, index=merged["date"].values)
    return lvl / lvl.shift(1) - 1 if kind == "ret" else lvl.diff()


def build_factor_frame(index_name, idx_df, indices, factors):
    """Returns DataFrame: index return + the factor moves used to explain it."""
    dates = idx_df.index
    cols = {"_ret": idx_df["Close"].pct_change()}
    for f in config.DECOMPOSITION_FACTORS.get(index_name, []):
        if f in factors:
            spec = config.FACTORS[f]
            cols[f] = factor_change_on_calendar(factors[f]["Close"], dates,
                                                spec["kind"], spec["timing"])
        elif f in indices:  # another index used as the "market" factor
            cols[f] = factor_change_on_calendar(indices[f]["Close"], dates, "ret", "same_day")
    return pd.DataFrame(cols, index=dates)


def decompose(frame, window=config.ROLLING_WINDOW):
    """Rolling out-of-sample decomposition. Returns (contributions, betas)."""
    data = frame.dropna()
    fnames = [c for c in data.columns if c != "_ret"]
    if len(data) < window + 20 or not fnames:
        return None, None
    y = data["_ret"].values
    X = np.column_stack([np.ones(len(data)), data[fnames].values])
    rows, beta_rows, resid_sd = [], [], []
    for t in range(window, len(data)):
        Xw, yw = X[t - window:t], y[t - window:t]
        coef, *_ = np.linalg.lstsq(Xw, yw, rcond=None)
        fit_resid = yw - Xw @ coef
        contrib = coef[1:] * X[t, 1:]
        rows.append(contrib)
        beta_rows.append(coef[1:])
        resid_sd.append(fit_resid.std())
    idx = data.index[window:]
    contrib = pd.DataFrame(rows, index=idx, columns=fnames)
    contrib.insert(0, "actual", y[window:])
    contrib["explained"] = contrib[fnames].sum(axis=1)
    contrib["index-specific"] = contrib["actual"] - contrib["explained"]
    contrib["resid_sd"] = resid_sd
    for f in fnames:  # keep the raw factor moves for plain-language text
        contrib[f"move:{f}"] = data[f].values[window:]
    betas = pd.DataFrame(beta_rows, index=idx, columns=fnames)
    return contrib, betas


def explanatory_power(contrib, last_days=None):
    """Out-of-sample R^2: share of daily variance explained by the factors."""
    c = contrib if last_days is None else contrib.tail(last_days)
    if c is None or len(c) < 30:
        return np.nan
    return 1 - c["index-specific"].var() / c["actual"].var()


def _fmt_move(f, v):
    kind = config.FACTORS.get(f, {"kind": "ret"})["kind"]
    if kind == "diff":
        return f"{v:+.2f} pts"
    return f"{v * 100:+.2f}%"


def explain_day(index_name, contrib, date=None):
    """Plain-language explanation of one day's move."""
    if contrib is None or contrib.empty:
        return f"{index_name}: not enough data to decompose."
    row = contrib.iloc[-1] if date is None else contrib.loc[date]
    fnames = [c[5:] for c in contrib.columns if c.startswith("move:")]
    parts = sorted(((f, row[f], row[f"move:{f}"]) for f in fnames),
                   key=lambda x: -abs(x[1]))
    actual = row["actual"]
    text = [f"{index_name} {'rose' if actual >= 0 else 'fell'} {abs(actual) * 100:.2f}%."]
    drivers = [p for p in parts if abs(p[1]) >= 0.0005][:3]
    if drivers:
        bits = [f"{f} ({_fmt_move(f, mv)}) contributed {c * 100:+.2f}%" for f, c, mv in drivers]
        text.append("Main measurable drivers: " + "; ".join(bits) + ".")
    else:
        text.append("None of the tracked factors moved enough to matter.")
    spec = row["index-specific"]
    sd = row["resid_sd"]
    text.append(f"Index-specific part (not explained by the factors): {spec * 100:+.2f}%.")
    if sd > 0 and abs(spec) > 2 * sd:
        text.append("That index-specific move is unusually large (over 2x its normal size), "
                    "which points to domestic news, heavy flows, or positioning. Worth checking.")
    return " ".join(text)
