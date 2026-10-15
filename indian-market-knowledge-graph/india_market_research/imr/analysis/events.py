"""Event studies: how do the indices behave around specific kinds of events?

Method (Fama, Fisher, Jensen & Roll 1969; MacKinlay 1997): line up every occurrence on day 0,
measure abnormal returns = return minus the "normal" return, and average across occurrences so
noise cancels. The normal return is the mean over an ESTIMATION WINDOW of `estimation_days`
sessions that ends before the event window starts, so no post-event data is used.
Overlapping occurrences (within `dedupe_gap` sessions) are counted once.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .decompose import factor_change_on_calendar


def positions(trading_index: pd.DatetimeIndex, dates) -> list[int]:
    """Day 0 = first trading day on or after each event date."""
    pos = trading_index.searchsorted(pd.DatetimeIndex(dates))
    return sorted({int(p) for p in pos if p < len(trading_index)})


def dedupe(pos: list[int], min_gap: int) -> list[int]:
    kept: list[int] = []
    for p in pos:
        if not kept or p - kept[-1] > min_gap:
            kept.append(p)
    return kept


def _t(x: np.ndarray) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / np.sqrt(len(x)))) if len(x) > 1 and sd > 0 else np.nan


def event_study(returns: pd.Series, pos: list[int], window=(-5, 10), estimation_days: int = 250) -> dict | None:
    """Average abnormal-return path around events. Needs >= 3 usable occurrences."""
    lo, hi = window
    r = returns.to_numpy(float)
    rows, raw0, dates = [], [], []
    for p in pos:
        est_end = p + lo                       # estimation window ends where the event window starts
        est_start = est_end - estimation_days
        if est_start < 1 or p + hi >= len(r):
            continue
        est = r[est_start:est_end]
        normal = np.nanmean(est)
        normal_abs = np.nanmean(np.abs(est))
        seg = r[p + lo:p + hi + 1]
        if np.isnan(seg).any() or np.isnan(normal):
            continue
        rows.append(seg - normal)
        raw0.append((r[p], normal_abs))
        dates.append(returns.index[p])
    if len(rows) < 3:
        return None
    m = np.array(rows)
    offsets = list(range(lo, hi + 1))
    z = offsets.index(0)
    day0 = m[:, z]
    post5 = m[:, z + 1:z + 6].sum(axis=1)
    pre5 = m[:, max(0, z - 5):z].sum(axis=1)
    raw = np.array(raw0)
    return {"n": len(rows), "day0_mean": day0.mean(), "day0_t": _t(day0),
            "day0_up_share": float((raw[:, 0] > 0).mean()),
            "move_size_ratio": float(np.mean(np.abs(raw[:, 0])) / np.mean(raw[:, 1])) if np.mean(raw[:, 1]) > 0
            else np.nan,
            "pre5_mean": pre5.mean(), "pre5_t": _t(pre5), "post5_mean": post5.mean(), "post5_t": _t(post5),
            "path": pd.Series(np.cumsum(m.mean(axis=0)), index=offsets),
            "path_se": pd.Series(m.std(axis=0, ddof=1) / np.sqrt(len(m)), index=offsets),
            "dates": pd.DatetimeIndex(dates)}


def data_driven_events(name: str, df: pd.DataFrame, factors: dict, cfg: dict) -> dict:
    """Events defined from the data itself. Every definition uses only information up to day 0."""
    idx = df.index
    ret = df["Close"].pct_change()
    ev = {}
    if "S&P 500" in factors:
        us = factor_change_on_calendar(factors["S&P 500"]["Close"], idx, "ret", "prev_session")
        m = cfg["big_us_move"]
        ev[f"US fell {m:.0%}+ overnight"] = idx[us.to_numpy() < -m]
        ev[f"US rose {m:.0%}+ overnight"] = idx[us.to_numpy() > m]
    if "India VIX" in factors:
        vix = factor_change_on_calendar(factors["India VIX"]["Close"], idx, "ret", "same_day")
        ev[f"India VIX jumped {cfg['vix_jump']:.0%}+"] = idx[vix.to_numpy() > cfg["vix_jump"]]
    sigma = ret.rolling(60).std().shift(1)          # past volatility only
    k = cfg["big_move_sigma"]
    ev[f"Big down day (>{k:g}x normal)"] = idx[(ret < -k * sigma).to_numpy()]
    ev[f"Big up day (>{k:g}x normal)"] = idx[(ret > k * sigma).to_numpy()]
    return ev


EXPIRY_CONTRACT = {"Nifty 50": ("NSE", "NIFTY"), "Bank Nifty": ("NSE", "BANKNIFTY"),
                   "Sensex": ("BSE", "SENSEX"), "Bankex": ("BSE", "BANKEX"),
                   "FinNifty": ("NSE", "FINNIFTY")}


def run_event_studies(indices: dict, factors: dict, calendar_events: pd.DataFrame | None, cfg: dict,
                      expiry: dict[str, pd.DataFrame] | None = None) -> dict:
    """{index: {label: result}}. Each result also carries 'category' and 'warning'."""
    out = {}
    window = (-cfg["window_before"], cfg["window_after"])
    for name, df in indices.items():
        ret = df["Close"].pct_change()
        own = "Day 0 is large by definition (the event is the index's own move); only the days after are informative."
        groups: dict[str, tuple[pd.DatetimeIndex, str, str]] = {
            lbl: (d, "Market shocks", own if lbl.startswith("Big ") else "")
            for lbl, d in data_driven_events(name, df, factors, cfg).items()}
        if calendar_events is not None and len(calendar_events):
            for etype, g in calendar_events.groupby("type"):
                warn = "Few cases: descriptive history, not a reliable pattern." if len(g) < 20 else ""
                groups[str(etype)] = (pd.DatetimeIndex(g["date"]), "Calendar", warn)
        if expiry and name in expiry and len(expiry[name]):
            e = expiry[name]
            warn = "" if (e["confirmed"].str.lower() == "yes").all() else \
                "Expiry rules not yet confirmed: verify config/expiry_rules.csv."
            groups["Monthly F&O expiry"] = (pd.DatetimeIndex(e["date"]), "Expiry", warn)
        res = {}
        for label, (dates, cat, warn) in groups.items():
            pos = dedupe(positions(df.index, dates), cfg["dedupe_gap_days"])
            r = event_study(ret, pos, window, cfg["estimation_days"])
            if r:
                res[label] = {**r, "category": cat, "warning": warn}
        out[name] = res
    return out
