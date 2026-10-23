"""
Pattern testing with honest validation.

Every pattern is tested on the first part of history (in-sample) and then
checked on later data it has never seen (out-of-sample). Signals use only
information available at the close of day t and are applied to day t+1,
so there is no look-ahead bias. Trading costs are deducted.

Patterns tested come from published research rather than guesswork:
  * Trend / time-series momentum: Moskowitz, Ooi & Pedersen (2012); Faber (2007)
  * Volatility timing: Moreira & Muir (2017)
  * Short-term reversal / continuation after extreme days
  * Turn-of-the-month effect: Ariel (1987); Lakonishok & Smidt (1988)
  * Day-of-the-week effects: French (1980)
  * Opening-gap behaviour
"""
import numpy as np
import pandas as pd

import config
from data import open_data_usable
from stats import perf, split_index, verdict, welch_t

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


# ---------------------------------------------------------------- timing rules
def timing_signals(close):
    r = close.pct_change()
    vol20 = r.rolling(20).std()
    return {
        "Trend: hold only above 200-day average": (close > close.rolling(200).mean()),
        "Momentum: hold only if 6-month return > 0": (close / close.shift(126) - 1 > 0),
        "Volatility timing: hold only when volatility below 1-year median":
            (vol20 < vol20.rolling(252).median()),
    }


def test_timing(name, df):
    close = df["Close"].dropna()
    r = close.pct_change()
    train, test = split_index(close.index[260:], config.TRAIN_FRACTION)  # skip warm-up
    rows = []
    for label, sig in timing_signals(close).items():
        pos = sig.astype(float).shift(1)            # decided at close t, held on t+1
        trades = pos.diff().abs().fillna(0)
        strat = pos * r - trades * config.COST_PER_SIDE
        res = {"index": name, "rule": label}
        for tag, period in (("in", train), ("out", test)):
            s, b = perf(strat.loc[period]), perf(r.loc[period])
            res[f"sharpe_{tag}"], res[f"bh_sharpe_{tag}"] = s["sharpe"], b["sharpe"]
            res[f"cagr_{tag}"], res[f"bh_cagr_{tag}"] = s["cagr"], b["cagr"]
            res[f"maxdd_{tag}"], res[f"bh_maxdd_{tag}"] = s["maxdd"], b["maxdd"]
        res["time_invested"] = pos.loc[test].mean()
        res["trades_per_year"] = trades.loc[test].sum() / (len(test) / 252)
        better_sharpe = res["sharpe_out"] > res["bh_sharpe_out"]
        smaller_dd = res["maxdd_out"] > res["bh_maxdd_out"]
        if better_sharpe and smaller_dd:
            res["verdict"] = "Better: higher Sharpe, smaller losses"
        elif smaller_dd:
            res["verdict"] = "Mixed: smaller losses, lower Sharpe"
        elif better_sharpe:
            res["verdict"] = "Mixed: higher Sharpe, deeper losses"
        else:
            res["verdict"] = "No improvement"
        rows.append(res)
    return rows


# ------------------------------------------------------- conditional effects
def conditional_tests(name, df):
    close = df["Close"].dropna()
    r = close.pct_change()
    nxt = r.shift(-1)                               # tomorrow's return
    sigma = r.rolling(60).std()
    k = config.BIG_MOVE_SIGMA
    tests = {
        f"Day after a big down day (>{k:g}x normal)": (nxt, r < -k * sigma),
        f"Day after a big up day (>{k:g}x normal)": (nxt, r > k * sigma),
    }
    # Turn of month: last trading day + first 3 trading days of each month
    month = pd.Series(close.index.to_period("M"), index=close.index)
    rank_fwd = month.groupby(month).cumcount()
    rank_back = month.groupby(month).cumcount(ascending=False)
    tests["Turn of the month (last day + first 3 days)"] = (r, (rank_fwd < 3) | (rank_back == 0))
    wd = pd.Series(close.index.dayofweek, index=close.index)
    for i, day in enumerate(WEEKDAYS):
        tests[f"{day} returns"] = (r, wd == i)
    if open_data_usable(df):
        gap = df["Open"] / df["Close"].shift(1) - 1
        intraday = df["Close"] / df["Open"] - 1
        tests["After a gap-up open of 0.5%+: open-to-close move"] = (intraday, gap > 0.005)
        tests["After a gap-down open of 0.5%+: open-to-close move"] = (intraday, gap < -0.005)

    train, test = split_index(close.index[60:], config.TRAIN_FRACTION)
    rows = []
    for label, (target, mask) in tests.items():
        mask = mask.fillna(False).astype(bool)
        res = {"index": name, "pattern": label}
        stats_ = {}
        for tag, period in (("in", train), ("out", test)):
            t_ = target.reindex(period)
            m_ = mask.reindex(period).fillna(False).astype(bool)
            hit, rest = t_[m_].dropna(), t_[~m_].dropna()
            res[f"n_{tag}"] = len(hit)
            res[f"diff_{tag}"] = hit.mean() - rest.mean() if len(hit) else np.nan
            res[f"t_{tag}"] = welch_t(hit, rest)
            stats_[tag] = res[f"diff_{tag}"]
        same = np.sign(stats_["in"]) == np.sign(stats_["out"])
        res["verdict"] = verdict(res["t_in"], res["t_out"], same)
        rows.append(res)
    return rows


def run_pattern_tests(indices):
    timing, cond = [], []
    for name, df in indices.items():
        if len(df) < 800:
            continue
        timing += test_timing(name, df)
        cond += conditional_tests(name, df)
    return pd.DataFrame(timing), pd.DataFrame(cond)
