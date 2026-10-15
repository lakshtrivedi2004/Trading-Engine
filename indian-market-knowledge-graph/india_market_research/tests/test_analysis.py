"""Milestone 2: synthetic data with KNOWN answers. All offline."""
import numpy as np
import pandas as pd
import pytest

from imr.analysis import cleaning, decompose, events, patterns, stats
from imr.analysis.lookahead import LookaheadError, check_causal
from imr.analysis.patterns import PatternConfig, Rule, rule_library, run_pattern_tests


def _prices(n=2500, seed=0, start="2010-01-01", vol=0.01, drift=0.0):
    """Pure random walk with an independent overnight gap and intraday move (no planted effects)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n)
    gap = rng.normal(0, vol * 0.5, n)
    intraday = rng.normal(drift, vol * 0.85, n)
    close = 1000 * np.cumprod((1 + gap) * (1 + intraday))
    opn = np.r_[1000.0, close[:-1]] * (1 + gap)
    return pd.DataFrame({"Open": opn, "High": np.maximum(opn, close) * 1.003,
                         "Low": np.minimum(opn, close) * 0.997, "Close": close}, index=days)


# ---------------------------------------------------------------- decomposition
def test_decomposition_recovers_planted_betas(settings):
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2012-01-02", periods=1500)
    f1, f2 = rng.normal(0, 0.01, len(days)), rng.normal(0, 0.004, len(days))
    y = 0.0002 + 0.5 * f1 - 0.8 * f2 + rng.normal(0, 0.003, len(days))
    frame = pd.DataFrame({"_ret": y, "A": f1, "B": f2}, index=days)
    contrib, betas = decompose.decompose(frame, window=250)
    assert betas["A"].median() == pytest.approx(0.5, abs=0.03)
    assert betas["B"].median() == pytest.approx(-0.8, abs=0.08)
    assert decompose.explanatory_power(contrib) == pytest.approx(0.79, abs=0.05)   # theoretical R² ≈ 0.796
    # the decomposition adds up exactly
    assert np.allclose(contrib["explained"] + contrib["index-specific"], contrib["actual"])


def test_decomposition_never_uses_the_days_own_data():
    rng = np.random.default_rng(2)
    days = pd.bdate_range("2015-01-01", periods=400)
    frame = pd.DataFrame({"_ret": rng.normal(0, 0.01, 400), "A": rng.normal(0, 0.01, 400)}, index=days)
    c1, b1 = decompose.decompose(frame, window=250)
    shocked = frame.copy()
    shocked.iloc[-1, 0] = 0.5                       # absurd move on the last day
    c2, b2 = decompose.decompose(shocked, window=250)
    assert b1.iloc[-1]["A"] == b2.iloc[-1]["A"]     # its own beta is unaffected by that day


# ---------------------------------------------------------------- calendar alignment / holidays
def test_us_data_maps_to_next_indian_session():
    india = pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-03"])
    us = pd.Series([100.0, 110.0, 121.0], index=india)          # US closes AFTER India
    chg = decompose.factor_change_on_calendar(us, india, "ret", "prev_session")
    # India 2 Jan sees the US move of 1 Jan (100 -> ?): needs the level strictly before each date
    assert np.isnan(chg.iloc[0]) and np.isnan(chg.iloc[1])        # no US level before 1 Jan exists
    assert chg.iloc[2] == pytest.approx(0.10)                      # 3 Jan India <- 2 Jan US (100 -> 110)


def test_us_holiday_gives_zero_change():
    india = pd.bdate_range("2024-07-01", "2024-07-05")             # Mon-Fri
    us = pd.Series([100.0, 101.0, 102.0, 104.0], index=pd.DatetimeIndex(
        ["2024-07-01", "2024-07-02", "2024-07-03", "2024-07-05"]))  # US closed Thu 4 Jul
    chg = decompose.factor_change_on_calendar(us, india, "ret", "prev_session")
    assert chg.loc["2024-07-04"] == pytest.approx(102 / 101 - 1)   # Wed's US session
    assert chg.loc["2024-07-05"] == 0.0                            # US holiday: nothing new overnight


def test_indian_holiday_sums_two_us_sessions_and_shifts_nothing():
    india = pd.DatetimeIndex(["2024-03-04", "2024-03-05", "2024-03-07", "2024-03-08"])   # India shut Wed 6th
    us = pd.Series([100.0, 102.0, 103.02, 104.0, 105.0], index=pd.bdate_range("2024-03-04", periods=5))
    chg = decompose.factor_change_on_calendar(us, india, "ret", "prev_session")
    assert chg.loc["2024-03-05"] == pytest.approx(0.0) or np.isnan(chg.loc["2024-03-05"])
    assert chg.loc["2024-03-07"] == pytest.approx(103.02 / 100.0 - 1)  # Tue + Wed US sessions together
    assert chg.loc["2024-03-08"] == pytest.approx(104.0 / 103.02 - 1)  # Thu's US session only
    same = decompose.factor_change_on_calendar(us, india, "ret", "same_day")
    assert same.loc["2024-03-07"] == pytest.approx(104.0 / 102.0 - 1)  # same-day: Tue close -> Thu close


# ---------------------------------------------------------------- look-ahead guard
def _leaky(df, cal):
    return (df["Close"].shift(-1) > df["Close"]).astype(float)        # peeks at tomorrow


def test_planted_lookahead_leak_is_caught_and_refused():
    df = _prices(1200)
    with pytest.raises(LookaheadError):
        check_causal(_leaky, df, cal=df.index)
    leak_rule = Rule("leak", "Leaky rule", "timing", _leaky)
    res, notes = run_pattern_tests({"X": df}, {"X": df.index}, PatternConfig(min_rows=800), rules=[leak_rule])
    assert res.empty and any("REFUSED" in n for n in notes)


def test_every_shipped_rule_passes_the_lookahead_guard():
    df = _prices(1200)
    expiry = pd.DatetimeIndex(df.index[::21])
    for rule in rule_library():
        for p in rule.param_sets():
            extra = {"expiry": expiry} if rule.needs_expiry else {}
            check_causal(rule.fn, df, cal=df.index, **{**p, **extra})


def test_turn_of_month_uses_the_calendar_not_the_data_edge():
    df = _prices(700, start="2020-01-01")
    cal = pd.bdate_range("2020-01-01", "2023-12-31")               # known trading calendar
    mid_month = df.index[df.index.day == 15][-1]
    cut = df.loc[:mid_month]
    _, mask = patterns._turn_of_month(cut, cal)
    assert mask.iloc[-1] == 0.0          # mid-month day is not "last day of month" just because data ends


# ---------------------------------------------------------------- costs / NW / verdicts
def test_costs_are_deducted_on_every_position_change():
    df = _prices(600)
    flip = pd.Series(np.arange(len(df)) % 2, index=df.index, dtype=float)
    always = pd.Series(1.0, index=df.index)
    a = patterns.timing_returns(df, always, cost=0.001)
    f = patterns.timing_returns(df, flip, cost=0.001)
    assert a["trades"].iloc[1:].sum() == 0
    assert (f["trades"].iloc[1:] == 1).all()
    expected = f["held"] * f["hold"] - 0.001
    assert np.allclose(f["strat"].iloc[1:], expected.iloc[1:])


def test_newey_west_matches_plain_t_without_autocorrelation_and_is_smaller_with_it():
    rng = np.random.default_rng(3)
    x = rng.normal(0.001, 0.01, 5000)
    plain = x.mean() / (x.std() / np.sqrt(len(x)))
    assert stats.newey_west_t(x, lags=5) == pytest.approx(plain, rel=0.1)
    e = rng.normal(0, 0.01, 5000)
    ar = np.empty_like(e)
    ar[0] = e[0]
    for i in range(1, len(e)):
        ar[i] = 0.6 * ar[i - 1] + e[i]
    ar += 0.0005
    plain_ar = ar.mean() / (ar.std() / np.sqrt(len(ar)))
    assert abs(stats.newey_west_t(ar, lags=10)) < abs(plain_ar)


def test_verdict_rules_unchanged():
    v = stats.verdict
    assert v(3.0, 2.0, True) == "Strong: holds out-of-sample"
    assert v(2.9, 2.5, True) == "Moderate: holds, but weakly"
    assert v(3.5, 2.5, False) == "Faded: did not hold out-of-sample"
    assert v(2.0, 1.4, True) == "Faded: did not hold out-of-sample"
    assert v(1.9, 5.0, True) == "No reliable evidence"
    assert v(np.nan, 1.0, True) == "Not enough data"


def test_split_has_a_gap_and_is_chronological():
    idx = pd.RangeIndex(100)
    a, b = stats.split_index(idx, 0.7, gap=1)
    assert a[-1] == 69 and b[0] == 71


# ---------------------------------------------------------------- pattern tests on pure noise
def test_random_data_mostly_gives_no_reliable_evidence():
    verdicts = []
    for seed in range(6):
        df = _prices(3000, seed=seed + 10)
        res, notes = run_pattern_tests({"X": df}, {"X": df.index}, PatternConfig(min_rows=800),
                                       open_ok={"X": True})
        verdicts += res["verdict"].tolist()
    share_none = np.mean([v in ("No reliable evidence", "Not enough data") for v in verdicts])
    strong = sum(v.startswith("Strong") for v in verdicts)
    assert share_none >= 0.8, share_none
    assert strong <= 1, strong


def test_a_real_planted_effect_is_found():
    df = _prices(3000, seed=5)
    r = df["Close"].pct_change().fillna(0).to_numpy(copy=True)
    r[df.index.dayofweek == 0] += 0.004                   # Mondays +0.4% on average
    df["Close"] = 1000 * np.cumprod(1 + r)
    monday = [x for x in rule_library() if x.id == "dow_monday"]
    res, _ = run_pattern_tests({"X": df}, {"X": df.index}, PatternConfig(min_rows=800), rules=monday)
    assert res.iloc[0]["verdict"] == "Strong: holds out-of-sample"


def test_walk_forward_never_sees_its_own_test_block(monkeypatch):
    df = _prices(2500, seed=4)
    seen = []
    orig = patterns._pick

    def spy(rule, d, cal, period, *a, **k):
        seen.append(period[-1])
        return orig(rule, d, cal, period, *a, **k)
    monkeypatch.setattr(patterns, "_pick", spy)
    trend = [x for x in rule_library() if x.id == "trend"]
    cfg = PatternConfig(walk_forward=True, wf_block=252, min_rows=800)
    res, _ = run_pattern_tests({"X": df}, {"X": df.index}, cfg, rules=trend)
    blocks = [d for d, _ in res.iloc[0]["wf_params"]]
    assert len(blocks) >= 2
    for b in blocks:
        assert any(p < b for p in seen)               # chosen on data ending before the block
    assert all(not (b <= p < b + pd.Timedelta(days=1)) for b in blocks for p in seen)


# ---------------------------------------------------------------- event studies
def test_event_study_recovers_planted_effect_and_finds_nothing_on_random_dates():
    rng = np.random.default_rng(6)
    days = pd.bdate_range("2010-01-01", periods=3000)
    r = pd.Series(rng.normal(0.0003, 0.01, len(days)), index=days)
    pos = list(range(400, 2900, 40))
    planted = r.copy()
    planted.iloc[pos] += 0.01
    res = events.event_study(planted, pos, (-5, 10), 250)
    assert res["day0_mean"] == pytest.approx(0.01, abs=0.003) and res["day0_t"] > 4
    rand = events.event_study(r, list(range(420, 2900, 40)), (-5, 10), 250)
    assert abs(rand["day0_t"]) < 3


def test_event_baseline_uses_only_pre_event_data():
    days = pd.bdate_range("2015-01-01", periods=800)
    r = pd.Series(0.0, index=days)
    r.iloc[600:] = 0.05                    # huge returns AFTER the events must not change the baseline
    res = events.event_study(r, [300, 340, 380], (-5, 10), 250)
    assert res["day0_mean"] == pytest.approx(0.0)


# ---------------------------------------------------------------- data cleaning
def test_spike_and_revert_excludes_only_the_bad_prints():
    s = pd.Series([49.5, 49.6, 52.49, 49.39, 52.49, 49.5, 49.0], index=pd.bdate_range("2012-01-24", periods=7))
    bad = cleaning.spike_and_revert(s, "ret", 0.03)
    assert list(bad.strftime("%m-%d")) == ["01-26", "01-30"]
