"""FinNifty (NSE), Bankex CSV import, BSE stock scanner, Forecast lab, Telegram bot, scheduling. All offline."""
import os
import stat
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from imr.analysis import forecast, scanner
from imr.data import bse_stocks, db, nse_prices, yahoo
from imr.data.nse_flows import NSEUnavailable
from imr.notify import dispatch, messages
from imr.notify.bot import Bot
from imr.notify.telegram import Secret, TelegramClient, TelegramError, load_secret, save_secret, split_message
from imr import schedule

EVENING = datetime(2026, 10, 2, 14, 0, tzinfo=ZoneInfo("UTC"))      # 19:30 IST Friday


# ---------------------------------------------------------------- NSE index history (FinNifty)
class FakeNSE:
    def __init__(self, fail_after=None):
        self.calls, self.fail_after = [], fail_after

    def json(self, url):
        self.calls.append(url)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise NSEUnavailable("HTTP 503")
        q = dict(p.split("=") for p in url.split("?")[1].split("&"))
        a, b = pd.to_datetime(q["from"], dayfirst=True), pd.to_datetime(q["to"], dayfirst=True)
        days = pd.bdate_range(a, b)
        return {"data": [{"EOD_TIMESTAMP": d.strftime("%d-%b-%Y").upper(), "EOD_OPEN_INDEX_VAL": 100,
                          "EOD_HIGH_INDEX_VAL": 101, "EOD_LOW_INDEX_VAL": 99, "EOD_CLOSE_INDEX_VAL": 100 + i % 3,
                          "HIT_TRADED_QTY": 5} for i, d in enumerate(reversed(days))]}


def test_nse_history_is_chunked_below_the_70_row_limit_and_stored(conn):
    client = FakeNSE()
    res = nse_prices.update_nse_series(conn, "FinNifty", "NIFTY FINANCIAL SERVICES", "2025-01-01", client, now=EVENING)
    stored = db.read_prices(conn, "FinNifty", with_meta=True)
    assert res["status"] == "updated" and len(stored) == len(pd.bdate_range("2025-01-01", "2026-10-02"))
    assert set(stored["source"]) == {"nse_api"}
    assert all(len(pd.bdate_range(*[pd.to_datetime(x.split("=")[1], dayfirst=True) for x in u.split("&")[1:3]])) < 70
               for u in client.calls)


def test_nse_history_stops_at_first_failure_so_no_hole_is_left(conn):
    res = nse_prices.update_nse_series(conn, "FinNifty", "X", "2025-01-01", FakeNSE(fail_after=2), now=EVENING)
    stored = db.read_prices(conn, "FinNifty")
    assert res["status"] == "partial" and "503" in res["error"]
    full = pd.bdate_range(stored.index[0], stored.index[-1])
    assert len(stored) == len(full)                    # contiguous: resumes from here next time


# ---------------------------------------------------------------- Bankex CSV from bseindia.com
@pytest.mark.parametrize("body", [
    "Date,Open,High,Low,Close\n01-October-2026,\"60,100.5\",\"60,500\",\"59,900\",\"60,321.25\"\n30-September-2026,1,2,1,59000\n",
    "Date,Open,High,Low,Close\n01/10/2026,1,2,1,60321.25\n30/09/2026,1,2,1,59000\n",
    "date,close\n2026-10-01,60321.25\n2026-09-30,59000\n",
])
def test_bse_style_csv_is_read(project, conn, body):
    p = project.manual_prices_dir / "Bankex_download1.csv"
    p.write_text(body)
    assert yahoo.manual_files(project.manual_prices_dir, "Bankex") == [p]
    res = yahoo.import_manual_prices(conn, "Bankex", "index", p)
    s = db.read_prices(conn, "Bankex", with_meta=True)
    assert res["added"] == 2 and s.loc["2026-10-01", "Close"] == pytest.approx(60321.25)
    assert s.loc["2026-09-30", "Close"] == 59000 and set(s["source"]) == {"manual_csv"}


# ---------------------------------------------------------------- BSE bhavcopy
HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,"
          "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
          "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n")


def _bhav(day, rows):
    lines = [f"{day:%Y-%m-%d},{day:%Y-%m-%d},CM,BSE,STK,1,ISIN{s},{s},{g},,,,,{s} LTD,{c},{h},{l},{c},{c},{pc},,,,,{v},{v * c},10,F1,1,,,,,"
             for s, g, c, h, l, pc, v in rows]
    return (HEADER + "\n".join(lines) + "\n").encode()


class Resp:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content


def test_bhavcopy_parse_and_holiday_detection():
    d = pd.Timestamp("2026-10-01")
    df = bse_stocks.parse_bhavcopy(_bhav(d, [("ABC", "A", 110, 112, 100, 100, 1000)]), d)
    assert df.iloc[0]["symbol"] == "ABC" and df.iloc[0]["prev_close"] == 100
    with pytest.raises(bse_stocks.NotAFile):
        bse_stocks.parse_bhavcopy(b"<!DOCTYPE html><html>holiday</html>", d)


def test_update_stocks_downloads_missing_days_and_records_holidays(conn):
    def get(url):
        day = pd.Timestamp(url.split("_0_0_0_")[1][:8])
        if day == pd.Timestamp("2026-09-30"):
            return Resp(200, b"<!DOCTYPE html>")           # a holiday: BSE serves its web page
        return Resp(200, _bhav(day, [("ABC", "A", 101, 102, 99, 100, 1000)]))
    res = bse_stocks.update_stocks(conn, history_days=5, get=get, sleep=lambda s: None, now=EVENING)
    assert res["added_days"] > 0 and res["skipped_days"] == 1
    again = bse_stocks.update_stocks(conn, history_days=5, get=get, sleep=lambda s: None, now=EVENING)
    assert again["added_days"] == 0                       # nothing downloaded twice


# ---------------------------------------------------------------- scanner
def _stock_panel(n_days=40, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-08-01", periods=n_days)
    rows = []
    for i, (sym, grp, vol) in enumerate([("CALM", "A", 0.005), ("WILD", "A", 0.04), ("JUMP", "B", 0.01),
                                         ("PENNY", "A", 0.02), ("TINY", "A", 0.01), ("TTRADE", "T", 0.01)]):
        price = 5.0 if sym == "PENNY" else 500.0
        r = rng.normal(0, vol, n_days)
        if sym == "JUMP":
            r[-1] = 0.12                                  # planted unusual move today
        close = price * np.cumprod(1 + r)
        prev = np.r_[price, close[:-1]]
        volu = 10.0 if sym == "TINY" else 200000.0
        rows.append(pd.DataFrame({"date": dates, "symbol": sym, "name": sym, "grp": grp, "open": prev,
                                  "high": np.maximum(prev, close) * 1.002, "low": np.minimum(prev, close) * 0.998,
                                  "close": close, "prev_close": prev, "volume": volu, "turnover": volu * close,
                                  "trades": 100}))
    return pd.concat(rows, ignore_index=True)


CFG = {"groups": ["A", "B"], "min_price": 20, "min_avg_turnover_cr": 1, "min_history_days": 15, "top_n": 10,
       "unusual_z": 3.0}


def test_scanner_ranks_moves_volatility_and_filters_illiquid():
    res = scanner.scan(_stock_panel(), CFG)
    eligible = set(res["all"]["symbol"])
    assert eligible == {"CALM", "WILD", "JUMP"}           # penny, illiquid and trade-to-trade stocks excluded
    assert res["lists"]["gainers"].iloc[0]["symbol"] == "JUMP"
    assert res["lists"]["unusual"].iloc[0]["symbol"] == "JUMP" and res["lists"]["unusual"].iloc[0]["z"] > 5
    assert res["lists"]["volatile"].iloc[0]["symbol"] == "WILD"


def test_unusual_z_excludes_today_from_its_own_baseline():
    m = scanner.compute_metrics(_stock_panel())
    j = m[m["symbol"] == "JUMP"].iloc[0]
    assert j["vol_prior"] < 0.02                           # the 12% day did not inflate the baseline


def test_circuit_flag():
    row = pd.Series({"high": 110.0, "low": 100.0, "close": 110.0, "ret": 0.0999, "volume": 5})
    assert "upper circuit" in scanner._circuit(type("R", (), row.to_dict()))
    locked = type("R", (), {"high": 95.0, "low": 95.0, "close": 95.0, "ret": -0.05, "volume": 10})
    assert "locked" in scanner._circuit(locked)


# ---------------------------------------------------------------- forecast lab
def _ohlc_from_returns(r, start="2008-01-01"):
    days = pd.bdate_range(start, periods=len(r))
    close = 1000 * np.cumprod(1 + r)
    prev = np.r_[1000.0, close[:-1]]
    return pd.DataFrame({"Open": prev, "High": np.maximum(prev, close) * (1 + np.abs(r) * 0.3),
                         "Low": np.minimum(prev, close) * (1 - np.abs(r) * 0.3), "Close": close}, index=days)


def test_direction_on_random_walk_has_no_edge():
    rng = np.random.default_rng(1)
    f = forecast.forecast_index(_ohlc_from_returns(rng.normal(0.0003, 0.01, 2600)))
    assert not f["direction"]["verdict"].startswith("Beats")


def test_direction_finds_planted_predictability():
    rng = np.random.default_rng(2)
    e = rng.normal(0, 0.01, 2600)
    r = np.empty_like(e)
    r[0] = e[0]
    for i in range(1, len(e)):
        r[i] = 0.35 * r[i - 1] + e[i]                    # strong momentum: tomorrow follows today
    f = forecast.forecast_index(_ohlc_from_returns(r))
    assert f["direction"]["verdict"] == "Beats the baseline out-of-sample"
    assert f["direction"]["hit_rate"] > 0.55


def test_move_size_model_beats_naive_on_clustered_volatility():
    rng = np.random.default_rng(3)
    n = 2600
    sig = np.empty(n)
    r = np.empty(n)
    sig[0] = 0.01
    for i in range(n):                                    # GARCH(1,1)-style volatility clustering
        if i:
            sig[i] = np.sqrt(0.0000033 + 0.20 * r[i - 1] ** 2 + 0.75 * sig[i - 1] ** 2)
        r[i] = sig[i] * rng.standard_normal()
    f = forecast.forecast_index(_ohlc_from_returns(r))
    assert f["size"]["verdict"] == "Beats the baseline out-of-sample"
    assert f["size"]["mae"] < f["size"]["baseline_mae"] and f["size"]["tomorrow"] > 0


def test_walk_forward_never_trains_on_future_targets():
    seen = []
    days = pd.bdate_range("2010-01-01", periods=1500)
    feats = pd.DataFrame({"x": np.arange(1500.0)}, index=days)
    tgt = pd.Series(np.arange(1500.0), index=days)

    def fit(X, y):
        seen.append(X[:, 0].max())
        return {}

    def predict(m, X):
        seen.append(("pred", X[:, 0].min()))
        return np.zeros(len(X))
    forecast.walk_forward(feats, tgt, ["x"], fit, predict, start=1000, step=50)
    trained = [s for s in seen if not isinstance(s, tuple)]
    predicted = [s[1] for s in seen if isinstance(s, tuple)]
    assert all(t < p for t, p in zip(trained, predicted))  # every model was fit only on rows before its block


# ---------------------------------------------------------------- telegram
class FakePost:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        if self.fail:
            return type("R", (), {"status_code": 401, "json": lambda s: {"ok": False, "description": "Unauthorized"}})()
        if method == "sendMessage":
            self.sent.append((json["chat_id"], json["text"]))
        result = {"getMe": {"username": "testbot"}, "getUpdates": []}.get(method, True)
        return type("R", (), {"status_code": 200, "json": lambda s: {"ok": True, "result": result}})()


def test_get_updates_long_poll_and_bot_poll_once(demo_db, settings):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url.rsplit("/", 1)[1], json, timeout))
        result = [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "/help"}}] \
            if url.endswith("getUpdates") else True
        return type("R", (), {"status_code": 200, "json": lambda s: {"ok": True, "result": result}})()
    client = TelegramClient("t", post=post)
    ups = client.updates(None, wait=20)                    # used by 'imr telegram setup' while waiting for /start
    assert ups[0]["update_id"] == 7
    method, params, http_timeout = calls[0]
    assert params["timeout"] == 20 and http_timeout == 35   # Telegram's long-poll wait vs our network wait
    bot = Bot(demo_db, settings, Secret("t", [42]), client)
    assert bot.poll_once(wait=20) == 1 and bot.offset == 8
    assert calls[-1][0] == "sendMessage" and "/today" in calls[-1][1]["text"]


def test_split_long_messages():
    parts = split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("x") == 10000


def test_secret_is_private_and_roundtrips(project):
    p = save_secret(project, Secret("123456:ABCDEFGHIJ", [42], "testbot"))
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    s = load_secret(project)
    assert s.token.startswith("123456") and s.chat_ids == [42] and "ABCDEFGHIJ" not in s.masked


def test_bad_token_gives_clear_error():
    with pytest.raises(TelegramError, match="token was rejected"):
        TelegramClient("bad", post=FakePost(fail=True)).get_me()


@pytest.fixture
def demo_db(project, settings):
    from imr.data.demo import build_demo_db
    from imr.data.update import load_config_files
    with db.connect(project.db_file(demo=True)) as c:
        build_demo_db(c, settings)
        load_config_files(c, project)
    return project


def test_bot_only_answers_authorised_chats(demo_db, settings):
    post = FakePost()
    bot = Bot(demo_db, settings, Secret("t", [42]), TelegramClient("t", post=post))
    bot.handle({"update_id": 1, "message": {"chat": {"id": 999}, "text": "/today"}})
    bot.handle({"update_id": 2, "message": {"chat": {"id": 999}, "text": "/movers"}})
    assert post.sent == [(999, "This bot is private.")]    # once, and no data revealed
    bot.handle({"update_id": 3, "message": {"chat": {"id": 42}, "text": "/help"}})
    bot.handle({"update_id": 4, "message": {"chat": {"id": 42}, "text": "/movers"}})
    texts = [t for c, t in post.sent if c == 42]
    assert "/today" in texts[0] and "movers" in texts[1].lower()


@pytest.mark.parametrize("cmd", ["/today", "/regime", "/health", "/forecast", "/status", "/patterns", "/nonsense"])
def test_bot_commands_answer(demo_db, settings, cmd):
    bot = Bot(demo_db, settings, Secret("t", [42]), TelegramClient("t", post=FakePost()))
    reply = bot.answer(cmd)
    assert isinstance(reply, str) and len(reply) > 10


def test_dispatch_sends_once_and_records_everything(demo_db, settings):
    post = FakePost()
    save_secret(demo_db, Secret("t", [42], "testbot"))
    res = dispatch.after_update(demo_db, settings, demo=True, client=TelegramClient("t", post=post), now=EVENING)
    kinds = dict(res)
    assert kinds["summary"] == "sent" and "weekly" in kinds          # EVENING is a Friday
    again = dispatch.after_update(demo_db, settings, demo=True, client=TelegramClient("t", post=post), now=EVENING)
    assert dict(again)["summary"] == "duplicate"
    with db.connect(demo_db.db_file(demo=True)) as c:
        notes = dispatch.read_notifications(c)
    assert (notes["status"] == "sent").any() and notes["text"].str.contains("SIMULATED").all()
    summary = next(t for _, t in post.sent if "Why they moved" in t)
    assert "not buy/sell advice" in summary and "Next session" in summary


def test_updates_are_recorded_even_without_telegram(demo_db, settings):
    res = dispatch.after_update(demo_db, settings, demo=True, now=EVENING)
    assert dict(res)["summary"] == "not_configured"


# ---------------------------------------------------------------- scheduling
def test_systemd_units_use_india_time_and_project_paths(tmp_path):
    u = schedule.units(tmp_path, "18:45", exe="/x/imr")
    assert "OnCalendar=Mon..Fri *-*-* 18:45:00 Asia/Kolkata" in u["imr-update.timer"]
    assert "Persistent=true" in u["imr-update.timer"]
    assert f"IMR_HOME={tmp_path}" in u["imr-update.service"] and "ExecStart=/x/imr update" in u["imr-update.service"]
    assert "ExecStart=/x/imr telegram run" in u["imr-bot.service"] and "Restart=always" in u["imr-bot.service"]
