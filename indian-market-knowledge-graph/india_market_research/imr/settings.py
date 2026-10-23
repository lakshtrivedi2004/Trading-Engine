"""Typed, validated settings loaded from config/settings.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .log import UserError


@dataclass(frozen=True)
class IndexSpec:
    name: str
    tickers: list[str]
    exchange: str
    family: str
    source: str = "yahoo"         # "yahoo" | "nse"
    nse_name: str = ""


@dataclass(frozen=True)
class FactorSpec:
    name: str
    tickers: list[str]
    kind: str            # "ret" | "diff"
    timing: str          # "same_day" | "prev_session"
    max_daily_jump: float


@dataclass(frozen=True)
class Settings:
    start_date: str
    indices: dict[str, IndexSpec]
    factors: dict[str, FactorSpec]
    decomposition: dict[str, list[str]]
    rolling_window: int
    use_flows_factor: bool
    min_flow_history: int
    research: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    data_quality: dict = field(default_factory=dict)
    nse: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    scanner: dict = field(default_factory=dict)
    telegram: dict = field(default_factory=dict)

    def family_of(self, index_name: str) -> str:
        spec = self.indices.get(index_name)
        return spec.family if spec else index_name


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise UserError(f"settings.toml: '{key}' is missing in [{where}].",
                        f"Add '{key} = ...' under [{where}] in config/settings.toml (see the README).")
    return d[key]


def load_settings(path: Path) -> Settings:
    if not path.exists():
        raise UserError(f"Settings file not found: {path}",
                        "Restore config/settings.toml from the project folder (it ships with the code).")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UserError(f"config/settings.toml has a formatting error: {exc}",
                        "Open the file, fix the line mentioned (check quotes and brackets), save, and re-run.")

    indices = {}
    for name, d in _require(raw, "indices", "root").items():
        tickers = _require(d, "tickers", f'indices."{name}"')
        source = d.get("source", "yahoo")
        if source not in ("yahoo", "nse"):
            raise UserError(f"Index '{name}': source must be 'yahoo' or 'nse', got '{source}'.",
                            f'Fix source under [indices."{name}"] in config/settings.toml.')
        if source == "nse" and not d.get("nse_name"):
            raise UserError(f"Index '{name}' uses source 'nse' but has no nse_name.",
                            f'Add nse_name = "<NSE index name>" under [indices."{name}"].')
        indices[name] = IndexSpec(name, list(tickers), d.get("exchange", "NSE"), d.get("family", name), source,
                                  d.get("nse_name", ""))

    factors = {}
    for name, d in _require(raw, "factors", "root").items():
        kind = d.get("kind", "ret")
        timing = d.get("timing", "same_day")
        if kind not in ("ret", "diff"):
            raise UserError(f"Factor '{name}': kind must be 'ret' or 'diff', got '{kind}'.",
                            f'Fix kind under [factors."{name}"] in config/settings.toml.')
        if timing not in ("same_day", "prev_session"):
            raise UserError(f"Factor '{name}': timing must be 'same_day' or 'prev_session', got '{timing}'.",
                            f'Fix timing under [factors."{name}"] in config/settings.toml.')
        factors[name] = FactorSpec(name, list(_require(d, "tickers", f'factors."{name}"')), kind, timing,
                                   float(d.get("max_daily_jump", 0.2)))

    dec = dict(raw.get("decomposition", {}))
    rolling = int(dec.pop("rolling_window", 250))
    use_flows = bool(dec.pop("use_flows_factor", True))
    min_flow = int(dec.pop("min_flow_history", 250))
    for idx, flist in dec.items():
        for f in flist:
            if f not in factors and f not in indices:
                raise UserError(f"Decomposition for '{idx}' uses unknown factor '{f}'.",
                                "Use a name defined under [factors] or [indices] in config/settings.toml.")

    research = {"train_fraction": 0.7, "split_gap_days": 1, "cost_per_side": 0.0003, "newey_west_lags": 5,
                "walk_forward": False, "walk_forward_test_days": 252, "stability_window_days": 504,
                "min_rows_for_patterns": 800, **raw.get("research", {})}
    if not 0.3 <= research["train_fraction"] <= 0.9:
        raise UserError("research.train_fraction must be between 0.3 and 0.9.",
                        "Set e.g. train_fraction = 0.70 in config/settings.toml.")
    if research["cost_per_side"] < 0:
        raise UserError("research.cost_per_side cannot be negative.", "Set it to 0 or more (0.0003 = 0.03%).")

    return Settings(
        start_date=raw.get("general", {}).get("start_date", "2010-01-01"),
        indices=indices, factors=factors, decomposition=dec, rolling_window=rolling,
        use_flows_factor=use_flows, min_flow_history=min_flow, research=research,
        events={"window_before": 5, "window_after": 10, "estimation_days": 250, "dedupe_gap_days": 5,
                "big_us_move": 0.02, "vix_jump": 0.15, "big_move_sigma": 2.0, **raw.get("events", {})},
        data_quality={"max_index_jump": 0.15, "stale_sessions": 2, "flat_run_days": 5,
                      "revision_tolerance": 0.005, "min_rows_usable": 250, **raw.get("data_quality", {})},
        nse={"enabled": True, "min_seconds_between_requests": 3.0, "retries": 3, "timeout_seconds": 15,
             **raw.get("nse", {})},
        report={"recent_days": 10, **raw.get("report", {})},
        scanner={"enabled": True, "history_days": 90, "groups": ["A", "B"], "min_price": 20.0,
                 "min_avg_turnover_cr": 5.0, "min_history_days": 15, "top_n": 15, "unusual_z": 3.0,
                 "seconds_between_downloads": 1.5, **raw.get("scanner", {})},
        telegram={"daily_summary": True, "unusual_alerts": True, "data_problem_alerts": True,
                  "weekly_pattern_check": True, "weekly_day": "Friday", "top_movers_in_summary": 5,
                  **raw.get("telegram", {})},
    )
