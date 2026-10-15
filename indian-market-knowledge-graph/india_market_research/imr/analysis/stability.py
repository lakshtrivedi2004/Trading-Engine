"""Pattern stability: is an effect fading?

For each rule (with the parameters chosen in-sample), the effect and its Newey-West t-statistic
are recomputed on a rolling window (default ~2 years), stepped monthly. Windows that end after
the in-sample period are genuinely out-of-sample. A line drifting toward zero is the typical
signature of an anomaly that has weakened since it became known (McLean & Pontiff 2016).
"""
from __future__ import annotations

import pandas as pd

from .patterns import Rule, eval_effect, eval_timing, timing_returns


def rolling_stability(rule: Rule, df: pd.DataFrame, cal, params: dict, window: int = 504, cost: float = 0.0003,
                      lags: int = 5, step: int = 21) -> pd.DataFrame:
    if rule.kind == "timing":
        tr = timing_returns(df, rule.fn(df, cal, **params), cost)
        idx = tr.index
    else:
        target, mask = rule.fn(df, cal, **params)
        idx = df.index[target.notna() & mask.notna()]
    rows = []
    for end in range(window, len(idx), step):
        period = idx[end - window:end]
        res = eval_timing(tr, period, lags) if rule.kind == "timing" else eval_effect(target, mask, period, lags)
        rows.append({"date": period[-1], "effect": res["effect"], "t": res["t"], "n": res["n"]})
    return pd.DataFrame(rows)
