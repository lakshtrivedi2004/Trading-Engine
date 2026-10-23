"""
Event studies: how do the indices behave around specific kinds of events?

Method (Fama, Fisher, Jensen & Roll 1969; MacKinlay 1997): line up every
occurrence of an event on "day 0", measure returns in excess of the normal
average day, and average across occurrences so that random noise cancels out.
"""
import numpy as np
import pandas as pd

import config
from decompose import factor_change_on_calendar


def _positions(trading_index, dates):
    """Day 0 = first trading day on or after each event date."""
    pos = trading_index.searchsorted(pd.DatetimeIndex(dates))
    return sorted(set(p for p in pos if p < len(trading_index)))


def _dedupe(positions, min_gap):
    """Drop events that fall inside the window of an earlier event
    (e.g. many shock days in March 2020) so one episode isn't counted many times."""
    kept = []
    for p in positions:
        if not kept or p - kept[-1] > min_gap:
            kept.append(p)
    return kept


def event_study(returns, positions, window=config.EVENT_WINDOW):
    lo, hi = window
    r = returns.values
    normal = np.nanmean(r)
    normal_abs = np.nanmean(np.abs(r))
    rows = []
    for p in positions:
        if p + lo < 1 or p + hi >= len(r):
            continue
        rows.append(r[p + lo:p + hi + 1] - normal)
    if len(rows) < 3:
        return None
    m = np.array(rows)
    offsets = list(range(lo, hi + 1))
    z = offsets.index(0)
    day0 = m[:, z]
    post5 = m[:, z + 1:z + 6].sum(axis=1)
    pre5 = m[:, max(0, z - 5):z].sum(axis=1)

    def t(x):
        return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std(ddof=1) > 0 else np.nan

    return {
        "n": len(rows),
        "day0_mean": day0.mean(),
        "day0_t": t(day0),
        "day0_up_share": ((day0 + normal) > 0).mean(),
        "move_size_ratio": np.mean(np.abs(day0 + normal)) / normal_abs,
        "pre5_mean": pre5.mean(),
        "post5_mean": post5.mean(),
        "post5_t": t(post5),
        "path": pd.Series(np.cumsum(m.mean(axis=0)), index=offsets),
    }


def data_driven_events(name, df, factors):
    """Events defined from the data itself, so no date list is needed."""
    idx = df.index
    ret = df["Close"].pct_change()
    ev = {}
    if "S&P 500" in factors:
        us = factor_change_on_calendar(factors["S&P 500"]["Close"], idx, "ret", "prev_session")
        ev[f"US fell {config.BIG_US_MOVE:.0%}+ overnight"] = idx[us.values < -config.BIG_US_MOVE]
        ev[f"US rose {config.BIG_US_MOVE:.0%}+ overnight"] = idx[us.values > config.BIG_US_MOVE]
    if "India VIX" in factors:
        vix = factor_change_on_calendar(factors["India VIX"]["Close"], idx, "ret", "same_day")
        ev[f"India VIX jumped {config.VIX_JUMP:.0%}+"] = idx[vix.values > config.VIX_JUMP]
    # sigma uses only PAST volatility, so the event is defined without hindsight
    sigma = ret.rolling(60).std().shift(1)
    k = config.BIG_MOVE_SIGMA
    ev[f"{name} big down day (>{k:g}x normal)"] = idx[(ret < -k * sigma).values]
    ev[f"{name} big up day (>{k:g}x normal)"] = idx[(ret > k * sigma).values]
    return ev


def run_event_studies(indices, factors, calendar_events):
    """Returns {index_name: {event_label: result}}."""
    out = {}
    lo, hi = config.EVENT_WINDOW
    for name, df in indices.items():
        ret = df["Close"].pct_change()
        res = {}
        groups = data_driven_events(name, df, factors)
        if calendar_events is not None and len(calendar_events):
            for etype, g in calendar_events.groupby("type"):
                groups[str(etype)] = g["date"].values
        for label, dates in groups.items():
            pos = _dedupe(_positions(df.index, dates), min_gap=5)
            r = event_study(ret, pos, (lo, hi))
            if r:
                res[label] = r
        out[name] = res
    return out


def event_text(label, r):
    direction = "up" if r["day0_mean"] > 0 else "down"
    sig = "statistically clear" if abs(r["day0_t"]) >= 2 else "not statistically clear"
    after = r["post5_mean"]
    follow = ("continued in the same direction" if np.sign(after) == np.sign(r["day0_mean"])
              else "partly reversed")
    follow_sig = "" if abs(r["post5_t"]) >= 2 else " (weak, could be chance)"
    return (f"{label} ({r['n']} cases): on the day, the index moved {direction} "
            f"{abs(r['day0_mean']) * 100:.2f}% beyond normal ({sig}); typical day size was "
            f"{r['move_size_ratio']:.1f}x a normal day. Over the next 5 days it {follow} by "
            f"{abs(after) * 100:.2f}%{follow_sig}.")
