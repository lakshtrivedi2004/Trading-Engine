"""Engine: loads everything from the database and runs the analyses.

Used by the dashboard, the HTML report and the tests. Nothing here downloads data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .analysis import cleaning, decompose, events, flows, patterns, regime
from .analysis.stability import rolling_stability
from .data import db
from .data.calendars import expiry_dates, trading_days
from .data.quality import open_data_usable


@dataclass
class Context:
    mode: str                                   # "real" | "demo"
    db_path: Path
    indices: dict
    factors: dict                               # cleaned (spike-and-revert days excluded)
    excluded: pd.DataFrame                      # days excluded from factors
    flows: pd.DataFrame | None
    events: pd.DataFrame
    expiry_rules: pd.DataFrame
    holidays: pd.DataFrame
    health: pd.DataFrame
    calendars: dict = field(default_factory=dict)
    expiry: dict = field(default_factory=dict)
    open_ok: dict = field(default_factory=dict)
    tests_run: int = 0

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def asof(self) -> pd.Timestamp | None:
        return max((d.index[-1] for d in self.indices.values()), default=None)


def load_context(db_path: Path, settings) -> Context:
    with db.connect(db_path) as conn:
        mode = db.data_mode(conn)
        indices, raw_factors = db.load_market(conn, settings)
        min_rows = settings.data_quality["min_rows_usable"]
        indices = {n: d for n, d in indices.items() if len(d) >= min_rows}
        factors, excluded = cleaning.clean_factors(raw_factors, settings)
        ctx = Context(mode=mode, db_path=db_path, indices=indices, factors=factors, excluded=excluded,
                      flows=db.read_flows(conn), events=db.read_events(conn), expiry_rules=db.read_expiry_rules(conn),
                      holidays=db.read_holidays(conn), health=db.read_latest_health(conn),
                      tests_run=db.count_tests(conn))
    hol = pd.DatetimeIndex(ctx.holidays["date"]) if len(ctx.holidays) else pd.DatetimeIndex([])
    rules = ctx.expiry_rules.copy()
    if len(rules):
        rules["valid_from"] = pd.to_datetime(rules["valid_from"])
        rules["valid_to"] = pd.to_datetime(rules["valid_to"])
    for name, df in ctx.indices.items():
        horizon = df.index[-1] + pd.offsets.MonthEnd(2)
        cal = trading_days(df.index, hol, horizon)       # observed history + expected future sessions
        ctx.calendars[name] = cal
        ctx.open_ok[name] = open_data_usable(df)
        ex, contract = events.EXPIRY_CONTRACT.get(name, (None, None))
        if ex and len(rules):
            e = expiry_dates(rules, ex, contract, cal, end=df.index[-1])
            if len(e):
                ctx.expiry[name] = e
    return ctx


def factor_kinds(settings) -> dict:
    return {n: s.kind for n, s in settings.factors.items()}


def run_decompositions(ctx: Context, settings) -> dict:
    """{index: (contrib, betas)}; plus '<index> + flows' variants when enough flow history exists."""
    out = {}
    for name, df in ctx.indices.items():
        frame = decompose.build_factor_frame(name, df, ctx.indices, ctx.factors, settings)
        out[name] = decompose.decompose(frame, settings.rolling_window)
    if settings.use_flows_factor and ctx.flows is not None:
        overlap = ctx.flows["fii_net"].dropna()
        for name in ("Nifty 50", "Sensex"):
            df = ctx.indices.get(name)
            if df is None or overlap.index.isin(df.index).sum() < settings.min_flow_history + settings.rolling_window // 5:
                continue
            frame = decompose.build_factor_frame(name, df, ctx.indices, ctx.factors, settings, flows=ctx.flows)
            res = decompose.decompose(frame, min(settings.rolling_window, settings.min_flow_history))
            if res[0] is not None:
                out[f"{name} + flows"] = res
    return out


def run_regime(ctx: Context) -> tuple[pd.DataFrame, dict | None]:
    return regime.all_regimes(ctx.indices), regime.vix_status(ctx.factors.get("India VIX"))


def run_events(ctx: Context, settings) -> dict:
    return events.run_event_studies(ctx.indices, ctx.factors, ctx.events, settings.events, ctx.expiry)


def run_flows(ctx: Context) -> dict | None:
    return flows.analyse_flows(ctx.flows, ctx.indices.get("Nifty 50"))


def run_patterns(ctx: Context, settings, register: bool = True, **overrides) -> tuple[pd.DataFrame, list[str], int]:
    """Run the pattern tests; register every test (each parameter set tried) in the database.
    Returns (results, notes, tests_run_all_time)."""
    cfg = patterns.PatternConfig.from_settings(settings, **overrides)
    fams = {n: settings.family_of(n) for n in ctx.indices}
    res, notes = patterns.run_pattern_tests(ctx.indices, ctx.calendars, cfg, ctx.open_ok, ctx.expiry, fams)
    total = ctx.tests_run
    if register and not res.empty:
        with db.connect(ctx.db_path) as conn:
            for r in res.itertuples():
                for _ in range(int(r.grid_size)):
                    total = db.register_test(conn, r.rule_id, r.index, r.family,
                                             {"params": r.params, "train_fraction": cfg.train_fraction,
                                              "walk_forward": cfg.walk_forward}, r.method, cfg.cost,
                                             r.t_in, r.t_out, r.verdict)
        ctx.tests_run = total
    return res, notes, total


def run_stability(ctx: Context, settings, results: pd.DataFrame, index_name: str, rule_id: str,
                  cost: float | None = None) -> pd.DataFrame:
    row = results[(results["index"] == index_name) & (results["rule_id"] == rule_id)]
    if row.empty:
        return pd.DataFrame()
    rule = next(r for r in patterns.rule_library() if r.id == rule_id)
    params = dict(row.iloc[0]["params"])
    df = ctx.indices[index_name]
    return rolling_stability(rule, df, ctx.calendars[index_name], params, settings.research["stability_window_days"],
                             settings.research["cost_per_side"] if cost is None else cost,
                             settings.research["newey_west_lags"])


def run_scan(ctx: Context, settings) -> dict | None:
    """BSE stock scanner on the stored bhavcopies (None if no stock data yet)."""
    from .analysis.scanner import scan
    from .data import bse_stocks

    with db.connect(ctx.db_path) as conn:
        df = bse_stocks.read_stocks(conn, settings.scanner["history_days"])
    if df.empty:
        return None
    return scan(df, settings.scanner)


def run_forecasts(ctx: Context, settings) -> dict:
    """{index: forecast dict} - honest walk-forward evaluation plus the next-session forecast."""
    from .analysis.forecast import forecast_index

    return {n: forecast_index(df, ctx.factors, cost=settings.research["cost_per_side"]) for n, df in ctx.indices.items()}


def run_stock_forecasts(ctx: Context, settings, scan_result: dict | None) -> dict | None:
    from .analysis.forecast import stock_size_forecasts
    from .data import bse_stocks

    if not scan_result or scan_result.get("asof") is None:
        return None
    with db.connect(ctx.db_path) as conn:
        df = bse_stocks.read_stocks(conn, settings.scanner["history_days"])
    return stock_size_forecasts(df, list(scan_result["all"]["symbol"]))
