"""Look-ahead guard: a signal for day t must be computable from data up to the close of day t.

Truncation test: recompute the signal on data cut off at day t and compare it with the value the
full-history computation produced for day t. Any difference means the rule peeked at the future.
Every rule is checked before it is tested; failing rules are refused, never reported.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class LookaheadError(Exception):
    pass


def _values(obj) -> pd.Series:
    """Signal part of a rule's output: a position Series (timing) or the mask of (target, mask)."""
    s = obj[1] if isinstance(obj, tuple) else obj
    return pd.Series(s).astype(float)


def check_causal(fn, df: pd.DataFrame, n_points: int = 8, min_history: int = 300, **kwargs) -> None:
    """Raise LookaheadError if fn(df.iloc[:t+1]) disagrees with fn(df) at day t for sampled t."""
    full = _values(fn(df, **kwargs))
    n = len(df)
    if n <= min_history + 2:
        return
    points = np.unique(np.linspace(min_history, n - 2, n_points).astype(int))
    for t in points:
        part = _values(fn(df.iloc[:t + 1], **kwargs))
        a, b = full.iloc[t], part.iloc[-1]
        if (np.isnan(a) != np.isnan(b)) or (not np.isnan(a) and not np.isclose(a, b)):
            raise LookaheadError(f"signal on {df.index[t]:%Y-%m-%d} changes when later data is removed "
                                 f"({a} with full history vs {b} without) - the rule uses future data")
