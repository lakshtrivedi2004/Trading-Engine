"""Filesystem locations. Everything is relative to the project folder (or $IMR_HOME)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.toml"

    @property
    def events_file(self) -> Path:
        return self.config_dir / "events.csv"

    @property
    def expiry_rules_file(self) -> Path:
        return self.config_dir / "expiry_rules.csv"

    @property
    def holidays_file(self) -> Path:
        return self.config_dir / "holidays.csv"

    @property
    def flows_manual_file(self) -> Path:
        return self.config_dir / "fii_dii_manual.csv"

    @property
    def manual_prices_dir(self) -> Path:
        return self.config_dir / "manual_prices"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def db_file(self, demo: bool = False) -> Path:
        return self.data_dir / ("demo.db" if demo else "market.db")

    def ensure(self) -> "Paths":
        for d in (self.data_dir, self.logs_dir, self.reports_dir, self.manual_prices_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def get_paths(root: str | os.PathLike | None = None) -> Paths:
    """Project paths; override with the IMR_HOME environment variable (used by tests)."""
    base = Path(root or os.environ.get("IMR_HOME") or PROJECT_ROOT)
    return Paths(base.resolve())
