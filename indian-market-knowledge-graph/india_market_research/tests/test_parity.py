"""The refactored engine reproduces the prototype's numbers wherever the method is unchanged.

(Event baselines and pattern tests changed deliberately - see PLAN.md fixes 1-5 - so they are not
compared here.)"""
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from imr.analysis import decompose, regime, stats

LEGACY = Path(__file__).resolve().parent.parent / "legacy"
MODS = ["config", "stats", "data", "decompose", "regime", "demo_data"]


@pytest.fixture(scope="module")
def legacy():
    saved = {m: sys.modules.pop(m) for m in MODS if m in sys.modules}
    sys.path.insert(0, str(LEGACY))
    try:
        yield {m: importlib.import_module(m) for m in MODS}
    finally:
        sys.path.remove(str(LEGACY))
        for m in MODS:
            sys.modules.pop(m, None)
        sys.modules.update(saved)


def test_decomposition_matches_prototype(legacy, settings):
    indices, factors, _ = legacy["demo_data"].make_demo_data(start="2014-01-01")
    for name in ("Nifty 50", "Bank Nifty"):
        old_frame = legacy["decompose"].build_factor_frame(name, indices[name], indices, factors)
        new_frame = decompose.build_factor_frame(name, indices[name], indices, factors, settings)
        pd.testing.assert_frame_equal(old_frame, new_frame)
        old_c, old_b = legacy["decompose"].decompose(old_frame)
        new_c, new_b = decompose.decompose(new_frame, settings.rolling_window)
        pd.testing.assert_frame_equal(old_c, new_c)
        pd.testing.assert_frame_equal(old_b, new_b)
        assert decompose.explanatory_power(new_c) == pytest.approx(legacy["decompose"].explanatory_power(old_c))


def test_regime_matches_prototype(legacy):
    indices, _, _ = legacy["demo_data"].make_demo_data(start="2014-01-01")
    for name, df in indices.items():
        old = legacy["regime"].index_regime(df, indices["Nifty 50"]["Close"])
        new = regime.index_regime(df, indices["Nifty 50"]["Close"])
        for k, v in old.items():
            if isinstance(v, float):
                assert new[k] == pytest.approx(v, nan_ok=True), k
            else:
                assert new[k] == v, k


def test_stats_match_prototype(legacy):
    rng = np.random.default_rng(0)
    x = rng.normal(0.0005, 0.01, 1000)
    assert stats.newey_west_t(x) == pytest.approx(legacy["stats"].newey_west_t(x))
    for args in [(3.1, 2.2, True), (2.5, 1.6, True), (2.5, 0.1, True), (1.0, 3.0, True), (np.nan, 1, True)]:
        assert stats.verdict(*args) == legacy["stats"].verdict(*args)
    assert stats.perf(x) == pytest.approx(legacy["stats"].perf(x))


def test_open_data_usable_matches_prototype(legacy):
    from imr.data.quality import open_data_usable
    indices, _, _ = legacy["demo_data"].make_demo_data(start="2014-01-01")
    for df in indices.values():
        assert open_data_usable(df) == legacy["data"].open_data_usable(df)
