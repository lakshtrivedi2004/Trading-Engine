"""Pattern testing with honest validation.

Standards enforced here:
- No look-ahead: a signal uses data up to the close of day t and is applied to day t+1. Every
  rule passes the truncation guard (lookahead.check_causal) before it is tested.
- Out-of-sample: chronological split with a gap (default), or walk-forward with an expanding
  window (Pardo 2008). Parameters are only ever chosen on past data.
- Costs: deducted per side on every position change (timing rules).
- Newey-West (1987) t-statistics: timing rules on (rule - buy & hold) daily returns; recurring
  effects via OLS on a dummy with HAC standard errors.
- Strict verdicts (stats.verdict) and a multiple-testing registry; near-duplicate indices are
  grouped into families.

Rule sources: trend (Faber 2007), time-series momentum (Moskowitz, Ooi & Pedersen 2012),
volatility timing (Moreira & Muir 2017), short-term reversal/continuation, turn of the month
(Ariel 1987; Lakonishok & Smidt 1988), day of the week (French 1980), F&O expiry effects,
opening gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable

import numpy as np
import pandas as pd

from .lookahead import LookaheadError, check_causal
from .stats import newey_west_t, nw_diff_t, perf, split_index, verdict

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


@dataclass
class Rule:
    id: str
    name: str                     # may contain {param} placeholders
    kind: str                     # "timing" | "effect"
    fn: Callable                  # timing: fn(df, cal, **p) -> 0/1 position decided at close t
    grid: dict = field(default_factory=dict)   # effect: fn(df, cal, **p) -> (target, mask)
    source: str = ""
    explain: str = ""
    needs_open: bool = False
    needs_expiry: bool = False

    def label(self, params: dict) -> str:
        return self.name.format(**params) if params else self.name

    def param_sets(self) -> list[dict]:
        if not self.grid:
            return [{}]
        keys = list(self.grid)
        return [dict(zip(keys, vals)) for vals in product(*(self.grid[k] for k in keys))]


# ---------------------------------------------------------------- rule functions
def _trend(df, cal, n=200):
    c = df["Close"]
    ma = c.rolling(n).mean()
    return (c > ma).astype(float).where(ma.notna())


def _tsmom(df, cal, lookback=126):
    c = df["Close"]
    past = c.shift(lookback)
    return (c / past - 1 > 0).astype(float).where(past.notna())


def _vol_timing(df, cal):
    r = df["Close"].pct_change()
    v = r.rolling(20).std()
    med = v.rolling(252).median()
    return (v < med).astype(float).where(med.notna())


def _after_big(direction):
    def fn(df, cal, k=2.0):
        r = df["Close"].pct_change()
        sigma = r.rolling(60).std()                      # includes day t: known at the close of t
        mask = (r < -k * sigma) if direction == "down" else (r > k * sigma)
        return r.shift(-1), mask.where(sigma.notna())    # target: NEXT day's return
    return fn


def _turn_of_month(df, cal):
    """Last trading day + first 3 trading days of each month, from the trading CALENDAR (known in
    advance from exchange holiday lists) - not from which prices happen to be stored."""
    days = pd.DatetimeIndex(cal).sort_values()
    month = pd.Series(days.to_period("M"), index=days)
    fwd = month.groupby(month).cumcount()
    back = month.groupby(month).cumcount(ascending=False)
    tom = ((fwd < 3) | (back == 0)).reindex(df.index).fillna(False)
    return df["Close"].pct_change(), tom.astype(float)


def _weekday(i):
    def fn(df, cal):
        wd = pd.Series(df.index.dayofweek, index=df.index)
        return df["Close"].pct_change(), (wd == i).astype(float)
    return fn


def _expiry_day(df, cal, expiry=None):
    mask = pd.Series(df.index.isin(pd.DatetimeIndex(expiry if expiry is not None else [])), index=df.index)
    return df["Close"].pct_change(), mask.astype(float)


def _gap(direction):
    def fn(df, cal, size=0.005):
        gap = df["Open"] / df["Close"].shift(1) - 1           # known at the open of day t
        intraday = df["Close"] / df["Open"] - 1               # target: open-to-close of day t
        mask = (gap > size) if direction == "up" else (gap < -size)
        return intraday, mask.where(gap.notna()).astype(float)
    return fn


def rule_library() -> list[Rule]:
    rules = [
        Rule("trend", "Trend: hold only above {n}-day average", "timing", _trend, {"n": [100, 150, 200]},
             "Faber (2007)", "Stay invested only while the index is above its moving average."),
        Rule("tsmom", "Momentum: hold only if {lookback}-day return > 0", "timing", _tsmom,
             {"lookback": [63, 126, 252]}, "Moskowitz, Ooi & Pedersen (2012)",
             "Stay invested only while the past return is positive."),
        Rule("voltiming", "Volatility timing: hold only when volatility below its 1-year median", "timing",
             _vol_timing, {}, "Moreira & Muir (2017)", "Stay invested only in calmer-than-usual periods."),
        Rule("after_big_down", "Day after a big down day (>{k}x normal)", "effect", _after_big("down"),
             {"k": [1.5, 2.0, 2.5]}, "Short-term reversal literature (e.g. Lehmann 1990)",
             "Does the index bounce (or keep falling) the day after an unusually large fall?"),
        Rule("after_big_up", "Day after a big up day (>{k}x normal)", "effect", _after_big("up"),
             {"k": [1.5, 2.0, 2.5]}, "Short-term reversal literature (e.g. Lehmann 1990)",
             "Does the index give back (or extend) an unusually large rise the next day?"),
        Rule("tom", "Turn of the month (last day + first 3 days)", "effect", _turn_of_month, {},
             "Ariel (1987); Lakonishok & Smidt (1988)", "Are returns higher around the turn of the month?"),
    ]
    rules += [Rule(f"dow_{d.lower()}", f"{d} returns", "effect", _weekday(i), {}, "French (1980)",
                   f"Are {d} returns different from other days?") for i, d in enumerate(WEEKDAYS)]
    rules += [
        Rule("expiry_day", "Monthly F&O expiry day returns", "effect", _expiry_day, {},
             "Expiry-day effects (e.g. Stoll & Whaley 1987)", "Do returns differ on monthly expiry days?",
             needs_expiry=True),
        Rule("gap_up", "After a gap-up open of {size:.1%}+: open-to-close move", "effect", _gap("up"),
             {"size": [0.005]}, "Opening-gap behaviour", "After a gap-up open, does the index keep rising "
             "or fade during the day?", needs_open=True),
        Rule("gap_down", "After a gap-down open of {size:.1%}+: open-to-close move", "effect", _gap("down"),
             {"size": [0.005]}, "Opening-gap behaviour", "After a gap-down open, does the index recover "
             "or keep falling during the day?", needs_open=True),
    ]
    return rules


# ---------------------------------------------------------------- evaluation pieces
def timing_returns(df: pd.DataFrame, pos: pd.Series, cost: float) -> pd.DataFrame:
    """Position decided at close t is held on t+1; cost per side on every change."""
    r = df["Close"].pct_change()
    held = pos.shift(1)
    trades = held.diff().abs()
    strat = held * r - trades.fillna(0) * cost
    valid = held.notna() & r.notna()
    return pd.DataFrame({"strat": strat, "hold": r, "excess": strat - r, "held": held,
                         "trades": trades.fillna(0)})[valid]


def eval_timing(tr: pd.DataFrame, period, lags: int) -> dict:
    t = tr.reindex(period).dropna(subset=["excess"])
    s, b = perf(t["strat"]), perf(t["hold"])
    return {"t": newey_west_t(t["excess"], lags), "effect": t["excess"].mean() * 252, "n": len(t),
            "sharpe": s["sharpe"], "bh_sharpe": b["sharpe"], "cagr": s["cagr"], "bh_cagr": b["cagr"],
            "maxdd": s["maxdd"], "bh_maxdd": b["maxdd"], "invested": t["held"].mean(),
            "trades_per_year": t["trades"].sum() / max(len(t) / 252, 1e-9)}


def eval_effect(target: pd.Series, mask: pd.Series, period, lags: int) -> dict:
    tg = target.reindex(period)
    m = mask.reindex(period)
    ok = tg.notna() & m.notna()
    diff, t = nw_diff_t(tg[ok], m[ok].astype(bool), lags)
    return {"t": t, "effect": diff, "n": int(m[ok].astype(bool).sum())}


def _evaluate(rule, df, cal, params, period, cost, lags, tr_cache=None):
    if rule.kind == "timing":
        pos = rule.fn(df, cal, **params)
        return eval_timing(timing_returns(df, pos, cost), period, lags)
    target, mask = rule.fn(df, cal, **params)
    return eval_effect(target, mask, period, lags)


def _pick(rule, df, cal, period, cost, lags, extra) -> tuple[dict, dict, int]:
    """Choose the parameter set with the largest |t| on `period` only (past data)."""
    best, best_res, tried = None, None, 0
    for p in rule.param_sets():
        p = {**p, **extra}
        res = _evaluate(rule, df, cal, p, period, cost, lags)
        tried += 1
        score = abs(res["t"]) if not np.isnan(res["t"]) else -1
        if best_res is None or score > (abs(best_res["t"]) if not np.isnan(best_res["t"]) else -1):
            best, best_res = p, res
    return best, best_res, tried


# ---------------------------------------------------------------- runners
@dataclass
class PatternConfig:
    train_fraction: float = 0.7
    gap: int = 1
    cost: float = 0.0003
    lags: int = 5
    walk_forward: bool = False
    wf_block: int = 252
    min_rows: int = 800

    @classmethod
    def from_settings(cls, s, **override):
        r = s.research
        base = dict(train_fraction=r["train_fraction"], gap=r["split_gap_days"], cost=r["cost_per_side"],
                    lags=r["newey_west_lags"], walk_forward=r["walk_forward"], wf_block=r["walk_forward_test_days"],
                    min_rows=r["min_rows_for_patterns"])
        return cls(**{**base, **override})


def evaluate_rule(rule: Rule, df: pd.DataFrame, cal, cfg: PatternConfig, extra: dict | None = None) -> dict:
    """Test one rule on one index. Returns a result row (also used by the registry)."""
    extra = extra or {}
    for p in rule.param_sets():
        check_causal(rule.fn, df, cal=cal, **{**p, **extra})
    usable = df.index[260:]                         # warm-up for 252-day lookbacks
    train, test = split_index(usable, cfg.train_fraction, cfg.gap)
    params, res_in, tried = _pick(rule, df, cal, train, cfg.cost, cfg.lags, extra)
    row = {"rule_id": rule.id, "kind": rule.kind, "method": "split", "params": params,
           "grid_size": tried, "train_end": train[-1], "test_start": test[0] if len(test) else None}
    if not cfg.walk_forward:
        res_out = _evaluate(rule, df, cal, params, test, cfg.cost, cfg.lags)
    else:
        row["method"] = "walk-forward"
        res_out, chosen = _walk_forward(rule, df, cal, usable, train, cfg, extra)
        row["wf_params"] = chosen
    row["rule"] = rule.label({k: v for k, v in params.items() if k in rule.grid})
    for tag, res in (("in", res_in), ("out", res_out)):
        for k, v in res.items():
            row[f"{k}_{tag}"] = v
    same = np.sign(row["effect_in"]) == np.sign(row["effect_out"])
    row["verdict"] = verdict(row["t_in"], row["t_out"], bool(same))
    if rule.kind == "timing":
        better_sharpe = row["sharpe_out"] > row["bh_sharpe_out"]
        smaller_dd = row["maxdd_out"] > row["bh_maxdd_out"]
        row["risk_comparison"] = ("Higher Sharpe, smaller losses" if better_sharpe and smaller_dd else
                                  "Smaller losses, lower Sharpe" if smaller_dd else
                                  "Higher Sharpe, deeper losses" if better_sharpe else "No improvement vs holding")
    return row


def _walk_forward(rule, df, cal, usable, train, cfg, extra):
    """Expanding window: each test block uses parameters chosen on all data before it."""
    start = len(train) + cfg.gap
    pieces, chosen = [], []
    while start < len(usable):
        block = usable[start:start + cfg.wf_block]
        past = usable[:start - cfg.gap]
        params, _, _ = _pick(rule, df, cal, past, cfg.cost, cfg.lags, extra)
        chosen.append((block[0], {k: v for k, v in params.items() if k in rule.grid}))
        if rule.kind == "timing":
            tr = timing_returns(df, rule.fn(df, cal, **params), cfg.cost).reindex(block).dropna(subset=["excess"])
            pieces.append(tr)
        else:
            target, mask = rule.fn(df, cal, **params)
            pieces.append(pd.DataFrame({"target": target.reindex(block), "mask": mask.reindex(block)}))
        start += cfg.wf_block
    if not pieces:
        return {"t": np.nan, "effect": np.nan, "n": 0}, chosen
    stitched = pd.concat(pieces)
    if rule.kind == "timing":
        return eval_timing(stitched, stitched.index, cfg.lags), chosen
    return eval_effect(stitched["target"], stitched["mask"], stitched.index, cfg.lags), chosen


def run_pattern_tests(indices: dict, calendars: dict, cfg: PatternConfig, open_ok: dict | None = None,
                      expiry: dict | None = None, families: dict | None = None,
                      rules: list[Rule] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Run every applicable rule on every index with enough history.

    Returns (results, notes). Rules that fail the look-ahead guard are refused and noted."""
    rows, notes = [], []
    for name, df in indices.items():
        if len(df) < cfg.min_rows:
            notes.append(f"{name}: only {len(df)} days of history (need {cfg.min_rows}); not pattern-tested.")
            continue
        for rule in rules or rule_library():
            extra = {}
            if rule.needs_open and not (open_ok or {}).get(name, False):
                continue
            if rule.needs_expiry:
                e = (expiry or {}).get(name)
                if e is None or len(e) < 24:
                    continue
                extra["expiry"] = pd.DatetimeIndex(e["date"])
            try:
                row = evaluate_rule(rule, df, calendars.get(name, df.index), cfg, extra)
            except LookaheadError as exc:
                notes.append(f"{name} / {rule.id}: REFUSED - {exc}")
                continue
            row["index"] = name
            row["family"] = (families or {}).get(name, name)
            if rule.needs_expiry:
                conf = (expiry[name]["confirmed"].str.lower() == "yes").all()
                row["warning"] = "" if conf else "Expiry rules unconfirmed"
            rows.append(row)
    return pd.DataFrame(rows), notes


def family_summary(results: pd.DataFrame) -> pd.DataFrame:
    """Collapse near-duplicate indices: one line per (rule, family). Agreement inside a family is
    NOT independent confirmation; the family counts once."""
    if results.empty:
        return results
    order = ["Strong: holds out-of-sample", "Moderate: holds, but weakly", "Faded: did not hold out-of-sample",
             "No reliable evidence", "Not enough data"]
    rows = []
    for (rid, fam), g in results.groupby(["rule_id", "family"], sort=False):
        vs = g["verdict"].tolist()
        best = min(vs, key=order.index)
        rows.append({"rule_id": rid, "family": fam, "rule": g["rule"].iloc[0], "indices": ", ".join(g["index"]),
                     "best_verdict": best, "all_agree": len(set(vs)) == 1,
                     "t_out_range": f"{g['t_out'].min():+.1f} to {g['t_out'].max():+.1f}"})
    return pd.DataFrame(rows)
