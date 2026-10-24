#!/usr/bin/env python3
"""BTST desk: the Telegram channel's BTST signals traded overnight (owner, 23 Oct 2026).

    venv/bin/python run_btst.py              # page on config/btst.json "port" (9000)
    venv/bin/python run_btst.py --port 9001

Mode is .btst_mode (repo root): "live" sends real orders, anything else is
paper. The page switches it (POST /api/mode), only while no trade is open.

Reads from .angel.env: the Angel login (ANGEL_*), TELEGRAM_BTST_CHANNEL (the
signal channel's link or @name - only there, never in the code), TELEGRAM_API_ID / _HASH (the
user login, see arb/utils.py), GEMINI_API_KEY (signal reading), DATABASE_URL
(MongoDB), TELEGRAM_BOT_TOKEN / _CHAT_ID (alerts). The procedure is
btst/strategy.py; settings are config/btst.json. Every step is logged
(btst/logbook.py) to btst.log (systemd) and shown on the page's /logs.
"""
import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
logging.getLogger("smartConnect").setLevel(logging.WARNING)

from arb.config import CostModel, Credentials, _load_env_file      # noqa: E402
from arb.feed import AngelSession                                  # noqa: E402
from arb.instruments import load_scrip_master, now_ist             # noqa: E402
from arb.utils import read_channel_messages                        # noqa: E402
from btst import web                                               # noqa: E402
from btst.broker import AngelBtstBroker, PaperBtstBroker           # noqa: E402
from btst.holidays import Holidays                                 # noqa: E402
from btst.logbook import Logbook, fmt                              # noqa: E402
from btst.session import AngelSessions, Api                        # noqa: E402
from btst.ticks import PriceWatch                                  # noqa: E402
from btst.strategy import BtstStrategy                             # noqa: E402
from services.mongo import MongoStore                              # noqa: E402

MODE_FILE = os.path.join(ROOT, ".btst_mode")
CONFIG = os.path.join(ROOT, "config", "btst.json")
READER_RETRY_SEC = 300.0
BOOK = Logbook(path=os.path.join(ROOT, "btst.log"))


def log(msg, level="INFO", area="desk"):
    BOOK.log(fmt(level, area, msg))


def read_mode():
    try:
        m = open(MODE_FILE).read().strip().lower()
    except OSError:
        m = ""
    return "live" if m == "live" else "paper"


class DailyMaster:
    """The scrip master's rows, loaded once a day. load_scrip_master() answers
    (rows, where from) - the rows alone go to find_option."""

    def __init__(self, loader=load_scrip_master, today=lambda: now_ist().date()):
        self.loader, self.today, self.day, self.rows = loader, today, None, None

    def __call__(self):
        today = self.today()
        if self.day != today:
            t0 = time.time()
            try:
                rows, src = self.loader()
            except Exception as e:
                log(f"scrip master load failed: {e!r}", "ERROR", "contract")
                raise
            if not isinstance(rows, list) or not rows:
                log(f"scrip master unusable ({type(rows).__name__}, from {src})", "ERROR", "contract")
                raise ValueError(f"scrip master unusable: {type(rows).__name__} from {src}")
            self.rows, self.day = rows, today
            log(f"scrip master for {today}: {len(rows):,} contracts ({src}) in {time.time() - t0:.1f}s",
                area="contract")
        return self.rows


def make_llm(cfg, env):
    try:
        if str(cfg.get("ai_provider", "gemini_api")) == "vertex":
            from services.vertex_ai_mime import VertexAILLM
            return VertexAILLM(max_retries=1, timeout_sec=60)
        from services.vertex_ai_mime import GeminiAPILLM
        key = str(env.get("GEMINI_API_KEY", "")).strip()
        if not key:
            log("GEMINI_API_KEY is not set: signals are read by the strict pattern alone", "WARN", "ai")
            return None
        llm = GeminiAPILLM(api_key=key, model_name=cfg.get("ai_model"), max_retries=1, timeout_sec=60)
        log(f"model client ready: Gemini API, {cfg.get('ai_model')}", area="ai")
        return llm
    except Exception as e:
        log(f"no model client ({e!r}): signals are read by the strict pattern alone", "ERROR", "ai")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-telegram-alerts", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG))
    log(f"starting (pid {os.getpid()}); config {CONFIG}: "
        + ", ".join(f"{k}={v}" for k, v in cfg.items()), area="start")
    env = _load_env_file(os.path.join(ROOT, ".angel.env"))
    env.update({k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "GEMINI_"))})
    channel = str(env.get("TELEGRAM_BTST_CHANNEL", "")).strip()
    # never shown on the page or in the log: the link, @name and bare name
    BOOK.hide(channel, channel.rstrip("/").rsplit("/", 1)[-1], channel.rstrip("/").rsplit("/", 1)[-1].lstrip("@"))
    if not channel:
        log("TELEGRAM_BTST_CHANNEL is not set in .angel.env - cannot start", "ERROR", "start")
        raise SystemExit("TELEGRAM_BTST_CHANNEL is not set in .angel.env (the signal channel's t.me link)")
    creds = Credentials.load()
    if creds.missing():
        log(f"missing Angel credentials in .angel.env: {creds.missing()} - cannot start", "ERROR", "start")
        raise SystemExit(f"missing Angel credentials in .angel.env: {creds.missing()}")

    store = MongoStore(db_name=cfg.get("mongo_db", "trading_btst"))
    try:
        store.ping()
        store.ensure_indexes()
    except Exception as e:
        log(f"MongoDB unreachable: {e!r} - cannot start", "ERROR", "mongo")
        raise
    log(f"MongoDB ok (db {cfg.get('mongo_db', 'trading_btst')})", area="mongo")
    hols = Holidays(log=BOOK.log)
    hols.refresh()
    sess = AngelSessions(lambda: AngelSession(creds).login(retry_for_sec=120.0, log=BOOK.sink("angel")),
                         log=BOOK.log)
    sess.ensure()
    api = Api(sess)
    llm = make_llm(cfg, env)

    master = DailyMaster()

    rest = AngelBtstBroker(api, protection_pct=cfg.get("market_protection_pct", 2.5), log=BOOK.log)

    prices = PriceWatch(session_fn=sess.session, rest_quote=rest.quote, log=BOOK.log,
                        ltp_poll_sec=float(cfg.get("ltp_poll_sec", 1)), ws_fresh_sec=float(cfg.get("ws_fresh_sec", 5)),
                        use_ws=bool(cfg.get("use_websocket", True)),
                        hours=(cfg.get("market_open", "09:15"), cfg.get("market_close", "15:45")),
                        ws_dead_sec=float(cfg.get("ws_dead_sec", 90)))

    def make_broker(mode):
        log(f"broker: {mode.upper()}" + (" - real orders, CARRYFORWARD" if mode == "live"
                                        else " - simulated fills at the live price (websocket ticks)"), area="mode")
        return rest if mode == "live" else PaperBtstBroker(prices.price, log=BOOK.log, watch=prices)

    notifier = None
    if cfg.get("notify_telegram", True) and not args.no_telegram_alerts \
            and creds.telegram_bot_token and creds.telegram_chat_id:
        from arb.telegram import TelegramNotifier
        notifier = TelegramNotifier(creds.telegram_bot_token, creds.telegram_chat_id).start()
        log("Telegram alerts on (bot)", area="telegram")
    else:
        log("Telegram alerts off", area="telegram")

    mode = read_mode()
    strategy = BtstStrategy(cfg, store, make_broker(mode), master, llm, channel, log=BOOK.log,
                            notifier=notifier, cost_model=CostModel.load(), prices=prices)
    strategy.resume()
    lock = threading.Lock()

    def switch_mode(to):
        log(f"mode switch to {to!r} asked from the page", area="mode")
        if to not in ("paper", "live"):
            return {"ok": False, "error": "to must be paper or live"}
        with lock:
            j = store.open_job()
            if j:
                log(f"mode switch refused - {j['job_id']} is open", "WARN", "mode")
                return {"ok": False, "error": "a trade is open - switch once it is closed"}
            with open(MODE_FILE, "w") as fh:
                fh.write(to)
            strategy.broker = make_broker(to)
            log(f"mode switched to {to.upper()}", "WARN" if to == "live" else "INFO", "mode")
            if notifier:
                notifier.send(f"🌙 <b>BTST</b> mode is now <b>{to.upper()}</b>")
            return {"ok": True, "mode": to}

    port = args.port or int(cfg.get("port", 9000))
    web.serve(strategy, store, switch_mode, port=port, book=BOOK)
    log(f"BTST desk {mode.upper()} | page http://0.0.0.0:{port} (logs /logs) | "
        f"scan {cfg.get('scan_start')}-{cfg.get('scan_end')} | model {cfg.get('ai_model') if llm else 'none'}",
        area="start")
    if notifier:
        notifier.send(f"🌙 <b>BTST DESK UP</b> · <code>{mode.upper()}</code>\nPage :{port}")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def start_reader():
        log(f"starting the Telegram reader on the signal channel (last {cfg.get('read_last_messages', 50)} "
            f"messages, poll {cfg.get('channel_poll_sec', 5)} s)", area="channel")
        return read_channel_messages(channel=channel, last_messages=int(cfg.get("read_last_messages", 50)),
                                     on_message=strategy.on_message,
                                     poll_sec=float(cfg.get("channel_poll_sec", 5)), stop_event=stop,
                                     log=BOOK.log)
    reader, reader_at = start_reader(), time.time()
    beat_at, reader_down_said = time.time(), False
    while not stop.is_set():
        try:
            hols.refresh()                          # once a day
            sess.daily(cfg.get("session_refresh_at", "09:14"))
        except Exception as e:
            log(f"daily refresh error: {e!r}", "ERROR", "angel")
        strategy.health = {"angel": sess.state(), "reader": "up" if reader.is_alive() else "DOWN",
                           "holidays": hols.state(), "price": prices.state()}
        if not reader.is_alive():
            strategy.status_note = "Telegram reader stopped (see the log) - retrying every 5 min"
            if not reader_down_said:
                log("the Telegram reader has stopped - no signals can arrive; retrying every "
                    f"{READER_RETRY_SEC:.0f} s", "ERROR", "channel")
                reader_down_said = True
            if time.time() - reader_at > READER_RETRY_SEC:
                reader, reader_at, reader_down_said = start_reader(), time.time(), False
        strategy.step()
        if time.time() - beat_at >= float(cfg.get("heartbeat_sec", 300)):
            beat_at = time.time()
            j = store.open_job()
            log(f"alive | {strategy.broker.mode.upper()} | reader {'up' if reader.is_alive() else 'DOWN'} | "
                f"{strategy.status_note} | open job {j['job_id'] + ' ' + j['status'] if j else 'none'} | "
                f"{strategy.inbox.qsize()} message(s) queued", area="heartbeat")
        stop.wait(strategy.wait_sec())
    log("BTST desk stopping (signal received)", area="start")
    prices.stop()
    if notifier:
        notifier.stop()


if __name__ == "__main__":
    main()
