import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from imr.data import db
from imr.paths import get_paths
from imr.settings import load_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An isolated project folder with a copy of the shipped config files."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    monkeypatch.setenv("IMR_HOME", str(tmp_path))
    paths = get_paths(tmp_path).ensure()
    return paths


@pytest.fixture
def settings(project):
    return load_settings(project.settings_file)


@pytest.fixture
def conn(project):
    with db.connect(project.db_file()) as c:
        yield c


def make_ohlc(dates, start=100.0, seed=0, tz="Asia/Kolkata"):
    """Deterministic OHLCV with a timezone-aware index, like yfinance returns."""
    rng = np.random.default_rng(seed)
    dates = pd.DatetimeIndex(dates)
    close = start * np.cumprod(1 + rng.normal(0, 0.01, len(dates)))
    opn = close * (1 + rng.normal(0, 0.003, len(dates)))
    df = pd.DataFrame({"Open": opn, "High": np.maximum(opn, close) * 1.004, "Low": np.minimum(opn, close) * 0.996,
                       "Close": close, "Volume": 1000.0, "Adj Close": close}, index=dates)
    if tz:
        df.index = df.index.tz_localize(tz)
    return df


class FakeFetcher:
    """Serves frames per ticker, filtered by start date; records calls; can raise per ticker."""

    def __init__(self, frames: dict, errors: dict | None = None):
        self.frames, self.errors, self.calls = frames, errors or {}, []

    def __call__(self, ticker, start):
        self.calls.append((ticker, start))
        if ticker in self.errors:
            raise self.errors[ticker]
        df = self.frames.get(ticker)
        if df is None:
            return None
        local = df.index.tz_localize(None) if df.index.tz is not None else df.index
        out = df[local >= pd.Timestamp(start)]
        return out if len(out) else None


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test runs offline: any attempt to open a network connection fails the test."""
    import socket

    def guard(*args, **kwargs):
        raise AssertionError("A test tried to use the network; all tests must run offline.")
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
