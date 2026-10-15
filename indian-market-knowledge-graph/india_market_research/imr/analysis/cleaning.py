"""Exclusion of clear data errors in FACTOR series (never in the indices themselves).

Rule (spike-and-revert): a one-day move larger than the factor's `max_daily_jump` that is
reversed by at least 70% the next day is treated as a bad print (e.g. Yahoo's USD/INR on
31-Jan-2012: 49.39 -> 52.49 -> 49.50). That day's level is excluded (set missing), so the
calendar alignment uses the previous valid level. Nothing is invented: excluded days are
listed on the dashboard.

Note on timing: deciding that day t was an error uses day t+1. That is acceptable for data
cleaning in after-the-fact explanation and event studies, and these factors are never used
as same-day inputs to trading-rule tests.
"""
from __future__ import annotations

import pandas as pd

REVERT_SHARE = 0.7


def _first_spike(s: pd.Series, kind: str, threshold: float):
    move = s.pct_change() if kind == "ret" else s.diff()
    nxt = move.shift(-1)
    bad = (move.abs() > threshold) & (nxt * move < 0) & (nxt.abs() >= REVERT_SHARE * move.abs())
    hits = s.index[bad.fillna(False).to_numpy()]
    return hits[0] if len(hits) else None


def spike_and_revert(close: pd.Series, kind: str, threshold: float, max_iter: int = 200) -> pd.DatetimeIndex:
    """Remove the earliest bad print, re-check, repeat - so a genuine value sitting between two bad
    prints (52.49, 49.39, 52.49) is not mistaken for a spike itself."""
    s = close.dropna()
    bad = []
    for _ in range(max_iter):
        d = _first_spike(s, kind, threshold)
        if d is None:
            break
        bad.append(d)
        s = s.drop(d)
    return pd.DatetimeIndex(sorted(bad))


def clean_factors(factors: dict[str, pd.DataFrame], settings) -> tuple[dict, pd.DataFrame]:
    """Returns (cleaned factors, table of excluded days: series, date, close, move)."""
    out, rows = {}, []
    for name, df in factors.items():
        spec = settings.factors.get(name)
        if spec is None:
            out[name] = df
            continue
        bad = spike_and_revert(df["Close"], spec.kind, spec.max_daily_jump)
        clean = df.drop(index=bad)
        prev = df["Close"].shift(1)
        for d in bad:
            mv = df.at[d, "Close"] / prev[d] - 1 if spec.kind == "ret" else df.at[d, "Close"] - prev[d]
            rows.append({"series": name, "date": d, "close": df.at[d, "Close"], "move": mv, "kind": spec.kind})
        out[name] = clean
    return out, pd.DataFrame(rows, columns=["series", "date", "close", "move", "kind"])
