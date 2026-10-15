"""Milestone 1: storage, incremental downloads, fallbacks, NSE flows, calendars, data health. Offline."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from imr.data import db, yahoo
from imr.data.calendars import expiry_dates, expected_sessions, load_expiry_rules, trading_days
from imr.data.demo import build_demo_db
from imr.data.nse_flows import NSEClient, NSEUnavailable, parse_flows, parse_holidays, update_flows
from imr.data.quality import check_series, open_data_usable, run_checks
from imr.log import UserError
from imr.settings import load_settings

from .conftest import FakeFetcher, make_ohlc

AFTER_CLOSE = datetime(2030, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))   # far future: no bar is provisional


# ---------------------------------------------------------------- storage + incremental update
def test_incremental_update_adds_only_missing_days(conn):
    full = make_ohlc(pd.bdate_range("2020-01-01", periods=300))
    first = FakeFetcher({"^X": full.iloc[:200]})
    r1 = yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01", first, now=AFTER_CLOSE)
    assert r1["added"] == 200 and r1["status"] == "updated"

    second = FakeFetcher({"^X": full})
    r2 = yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01", second, now=AFTER_CLOSE)
    assert r2["added"] == 100
    # the second request started near the last stored day, not at the beginning of history
    assert pd.Timestamp(second.calls[0][1]) > pd.Timestamp("2020-09-01")
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE series='X'").fetchone()[0] == 300


def test_every_row_has_source_and_downloaded_at(conn):
    yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01",
                        FakeFetcher({"^X": make_ohlc(pd.bdate_range("2021-01-01", periods=60))}), now=AFTER_CLOSE)
    bad = conn.execute("SELECT COUNT(*) FROM prices WHERE source IS NULL OR source='' OR downloaded_at IS NULL "
                       "OR ticker IS NULL").fetchone()[0]
    assert bad == 0
    df = db.read_prices(conn, "X", with_meta=True)
    assert set(df["source"]) == {"yahoo"} and set(df["ticker"]) == {"^X"}


def test_revisions_are_detected_and_reported(conn):
    df = make_ohlc(pd.bdate_range("2021-01-01", periods=80))
    yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01", FakeFetcher({"^X": df}), now=AFTER_CLOSE)
    revised = df.copy()
    revised.iloc[-2, revised.columns.get_loc("Close")] *= 1.02
    res = yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01", FakeFetcher({"^X": revised}),
                              now=AFTER_CLOSE)
    assert res["revised"] == 1 and res["revisions"][0][0] == revised.index[-2].strftime("%Y-%m-%d")


def test_ticker_fallback_and_network_errors_never_crash(conn):
    df = make_ohlc(pd.bdate_range("2021-01-01", periods=80))
    f = FakeFetcher({"^B": df}, errors={"^A": ConnectionError("offline")})
    res = yahoo.update_series(conn, "X", "index", ["^A", "^B"], "2020-01-01", f, now=AFTER_CLOSE)
    assert res["ticker"] == "^B" and res["added"] == 80
    dead = FakeFetcher({}, errors={"^A": TimeoutError("t"), "^B": ConnectionError("c")})
    res = yahoo.update_series(conn, "Y", "index", ["^A", "^B"], "2020-01-01", dead, now=AFTER_CLOSE)
    assert res["status"] == "failed" and "TimeoutError" in res["error"]


def test_too_short_history_is_rejected_for_a_new_series(conn):
    res = yahoo.update_series(conn, "Bankex", "index", ["BSE-BANK.BO"], "2010-01-01",
                              FakeFetcher({"BSE-BANK.BO": make_ohlc(pd.bdate_range("2026-10-01", periods=1))}),
                              now=AFTER_CLOSE)
    assert res["status"] == "failed" and db.last_date(conn, "Bankex") is None


def test_ticker_switch_reloads_full_history(conn):
    a = make_ohlc(pd.bdate_range("2021-01-01", periods=100), start=100)
    yahoo.update_series(conn, "X", "index", ["^A", "^B"], "2020-01-01", FakeFetcher({"^A": a}), now=AFTER_CLOSE)
    b = make_ohlc(pd.bdate_range("2021-01-01", periods=120), start=500, seed=3)
    res = yahoo.update_series(conn, "X", "index", ["^A", "^B"], "2020-01-01",
                              FakeFetcher({"^B": b}, errors={"^A": ConnectionError()}), now=AFTER_CLOSE)
    stored = db.read_prices(conn, "X", with_meta=True)
    assert res["ticker"] == "^B" and set(stored["ticker"]) == {"^B"} and len(stored) == 120


def test_provisional_bar_is_dropped_during_the_session():
    df = make_ohlc(pd.bdate_range("2026-09-28", "2026-10-01"))
    during = datetime(2026, 10, 1, 6, 0, tzinfo=ZoneInfo("UTC"))      # 11:30 IST, market open
    after = datetime(2026, 10, 1, 13, 0, tzinfo=ZoneInfo("UTC"))      # 18:30 IST
    assert yahoo.clean_frame(df, during).index[-1] == pd.Timestamp("2026-09-30")
    assert yahoo.clean_frame(df, after).index[-1] == pd.Timestamp("2026-10-01")


def test_manual_prices_fill_gaps_but_never_overwrite_downloads(conn, project):
    df = make_ohlc(pd.bdate_range("2022-01-03", periods=60))
    yahoo.update_series(conn, "X", "index", ["^X"], "2020-01-01", FakeFetcher({"^X": df}), now=AFTER_CLOSE)
    path = project.manual_prices_dir / "X.csv"
    first_day = df.index[0].strftime("%Y-%m-%d")
    path.write_text(f"date,close\n2021-12-31,95\n{first_day},1\n")
    res = yahoo.import_manual_prices(conn, "X", "index", path)
    stored = db.read_prices(conn, "X", with_meta=True)
    assert res["added"] == 1
    assert stored.loc["2021-12-31", "source"] == "manual_csv"
    assert stored.loc[first_day, "source"] == "yahoo" and stored.loc[first_day, "Close"] != 1


# ---------------------------------------------------------------- NSE flows
PAYLOAD = [{"buyValue": "25420.04", "category": "DII", "date": "01-Oct-2026", "netValue": "10041.84",
            "sellValue": "15378.2"},
           {"buyValue": "12260.26", "category": "FII/FPI", "date": "01-Oct-2026", "netValue": "-9484.22",
            "sellValue": "21744.48"}]


class Resp:
    def __init__(self, code, payload=None):
        self.status_code, self._p = code, payload

    def json(self):
        return self._p


def test_parse_nse_flows():
    df = parse_flows(PAYLOAD)
    assert df.loc["2026-10-01", "fii_net"] == pytest.approx(-9484.22)
    assert df.loc["2026-10-01", "dii_buy"] == pytest.approx(25420.04)
    with pytest.raises(NSEUnavailable):
        parse_flows({"error": "changed format"})


@pytest.mark.parametrize("failure", ["403", "timeout"])
def test_nse_block_falls_back_to_manual_csv(conn, project, failure):
    def get(url, timeout):
        if failure == "timeout":
            raise TimeoutError("read timed out")
        return Resp(403)
    project.flows_manual_file.write_text("date,fii_net,dii_net\n2026-09-29,-100,200\n2026-09-30,50,-20\n")
    client = NSEClient(min_gap=0, retries=2, get=get, sleep=lambda s: None)
    st = update_flows(conn, project.flows_manual_file, client)
    assert st["nse"] == "failed" and "manual" in st["message"] and "What to do next" in st["message"]
    fl = db.read_flows(conn)
    assert len(fl) == 2 and set(fl["source"]) == {"manual_csv"}


def test_nse_rows_take_precedence_over_manual(conn, project):
    project.flows_manual_file.write_text("date,fii_net,dii_net\n2026-10-01,1,1\n")
    client = NSEClient(min_gap=0, get=lambda url, timeout: Resp(200, PAYLOAD), sleep=lambda s: None)
    update_flows(conn, project.flows_manual_file, client)
    fl = db.read_flows(conn)
    assert fl.loc["2026-10-01", "source"] == "nse_api" and fl.loc["2026-10-01", "fii_net"] == pytest.approx(-9484.22)


def test_nse_client_rate_limits_and_retries():
    calls, sleeps = [], []

    def get(url, timeout):
        calls.append(url)
        return Resp(500) if len([c for c in calls if "api" in c]) < 2 else Resp(200, PAYLOAD)
    client = NSEClient(min_gap=0, retries=3, get=get, sleep=sleeps.append)
    assert client.json("https://www.nseindia.com/api/fiidiiTradeReact") == PAYLOAD
    assert calls[0].endswith("/reports/fii-dii")          # cookie warm-up first
    assert sleeps                                         # backed off between attempts


def test_parse_holidays_drops_weekends():
    payload = {"CM": [{"tradingDate": "26-Jan-2026", "description": "Republic Day"},
                      {"tradingDate": "15-Feb-2026", "description": "Sunday holiday"}]}
    h = parse_holidays(payload)
    assert list(h["date"]) == [pd.Timestamp("2026-01-26")]


# ---------------------------------------------------------------- calendars / expiry rules
def _rules(tmp_path, body):
    p = tmp_path / "rules.csv"
    p.write_text("exchange,contract,valid_from,valid_to,weekday,week_of_month,holiday_shift,confirmed,note\n" + body)
    return load_expiry_rules(p)


def test_expiry_last_weekday_with_holiday_shift_and_rule_change(tmp_path):
    rules = _rules(tmp_path, "NSE,NIFTY,2024-01-01,2024-02-29,Thursday,last,previous,no,\n"
                             "NSE,NIFTY,2024-03-01,,Tuesday,last,previous,yes,\n")
    tdays = pd.bdate_range("2024-01-01", "2024-04-30")
    tdays = tdays[tdays != pd.Timestamp("2024-01-25")]          # pretend the last Thursday of Jan is a holiday
    e = expiry_dates(rules, "NSE", "NIFTY", tdays)
    assert list(e["date"]) == [pd.Timestamp("2024-01-24"), pd.Timestamp("2024-02-29"),
                               pd.Timestamp("2024-03-26"), pd.Timestamp("2024-04-30")]
    assert list(e["confirmed"]) == ["no", "no", "yes", "yes"]


def test_shipped_expiry_rules_load_and_are_marked_unconfirmed(project):
    rules = load_expiry_rules(project.expiry_rules_file)
    assert len(rules) > 0 and set(rules["confirmed"]) == {"no"}


def test_invalid_expiry_rule_gives_a_helpful_error(tmp_path):
    with pytest.raises(UserError) as e:
        _rules(tmp_path, "NSE,NIFTY,2024-01-01,,Thursdy,last,previous,no,\n")
    assert "What to do next" in str(e.value)


def test_expected_sessions_skip_holidays_and_weekends():
    s = expected_sessions("2026-01-23", "2026-01-30", pd.DatetimeIndex(["2026-01-26"]))
    assert list(s.strftime("%Y-%m-%d")) == ["2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30"]
    td = trading_days(pd.DatetimeIndex(["2026-01-22", "2026-01-23"]), pd.DatetimeIndex(["2026-01-26"]), "2026-01-27")
    assert list(td.strftime("%d")) == ["22", "23", "27"]


# ---------------------------------------------------------------- data health
def _cfg(settings):
    return settings.data_quality


def test_stale_series_is_flagged_and_holidays_are_not_counted(settings):
    df = make_ohlc(pd.bdate_range("2025-01-01", "2026-01-23"), tz=None)
    hol = pd.DatetimeIndex(["2026-01-26"])
    fresh = check_series("N", df, indian=True, kind="ret", jump=0.15, cfg=_cfg(settings), holidays=hol,
                         ref_end=pd.Timestamp("2026-01-27"))
    assert next(r for r in fresh if r["check"] == "stale")["status"] == "ok"
    stale = check_series("N", df, indian=True, kind="ret", jump=0.15, cfg=_cfg(settings), holidays=hol,
                         ref_end=pd.Timestamp("2026-02-06"))
    assert next(r for r in stale if r["check"] == "stale")["status"] == "warn"


def test_jumps_flat_runs_and_missing_days_are_flagged(settings):
    days = pd.bdate_range("2024-01-01", periods=400)
    df = make_ohlc(days, tz=None)
    df.iloc[200, df.columns.get_loc("Close")] *= 1.3                    # spike
    df.iloc[300:308, df.columns.get_loc("Close")] = df["Close"].iloc[299]  # frozen feed
    df = df.drop(days[[50, 60, 70, 80, 90, 100]])                        # gaps
    res = {r["check"]: r for r in check_series("N", df, indian=True, kind="ret", jump=0.15, cfg=_cfg(settings),
                                                holidays=pd.DatetimeIndex([]), ref_end=days[-1], peer_days=days)}
    assert res["jumps"]["status"] == "warn"
    assert res["flat_run"]["status"] == "warn"
    assert res["missing_days"]["status"] == "warn" and "6 day(s)" in res["missing_days"]["message"]


def test_open_data_usable():
    df = make_ohlc(pd.bdate_range("2024-01-01", periods=300), tz=None)
    assert open_data_usable(df)
    bad = df.copy()
    bad["Open"] = bad["Close"].shift(1)
    assert not open_data_usable(bad)


def test_unusable_series_fails_with_next_step(settings):
    res = check_series("Bankex", None, indian=True, kind="ret", jump=0.15, cfg=_cfg(settings),
                       holidays=pd.DatetimeIndex([]), ref_end=pd.Timestamp("2026-10-01"))
    assert res[0]["status"] == "fail" and "What to do next" in res[0]["detail"]


def test_run_checks_writes_health_table(conn, settings):
    yahoo.update_series(conn, "Nifty 50", "index", ["^NSEI"], "2020-01-01",
                        FakeFetcher({"^NSEI": make_ohlc(pd.bdate_range("2023-01-02", periods=300))}), now=AFTER_CLOSE)
    run_checks(conn, settings)
    h = db.read_latest_health(conn)
    assert len(h) > 5 and {"ok", "warn", "fail"} >= set(h["status"])
    assert (h["series"] == "Bankex").any()            # missing series are reported, not ignored


# ---------------------------------------------------------------- demo isolation / settings
def test_demo_database_is_labelled_and_isolated(project, settings):
    with db.connect(project.db_file(demo=True)) as c:
        build_demo_db(c, settings)
        assert db.data_mode(c) == "demo"
        assert {r[0] for r in c.execute("SELECT DISTINCT source FROM prices")} == {"SIMULATED"}
    with db.connect(project.db_file()) as c:
        db.set_meta(c, "data_mode", "real")
        with pytest.raises(RuntimeError):
            build_demo_db(c, settings)


def test_broken_settings_give_plain_errors(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text('[indices."Nifty 50"\ntickers = ["^NSEI"]\n')
    with pytest.raises(UserError) as e:
        load_settings(p)
    assert "What to do next" in str(e.value)
