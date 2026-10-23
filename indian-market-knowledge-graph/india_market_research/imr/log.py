"""Logging to logs/imr.log (rotating) and short messages on the console."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path, verbose: bool = False) -> logging.Logger:
    global _CONFIGURED
    log = logging.getLogger("imr")
    if _CONFIGURED:
        return log
    logs_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(logs_dir / "imr.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(message)s"))
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(fh)
    log.addHandler(ch)
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    _CONFIGURED = True
    return log


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"imr.{name}")


class UserError(Exception):
    """An error the user can fix. Always carries a 'what to do next' hint."""

    def __init__(self, problem: str, next_step: str):
        super().__init__(f"{problem}\nWhat to do next: {next_step}")
        self.problem = problem
        self.next_step = next_step
