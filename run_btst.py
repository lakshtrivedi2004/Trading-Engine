#!/usr/bin/env python3
"""BTST desk: the Telegram channel's BTST signals traded overnight (owner, 23 Oct 2026).

    venv/bin/python run_btst.py              # page on config/btst.json "port" (9000)
    venv/bin/python run_btst.py --port 9001

Mode is .btst_mode (repo root): "live" sends real orders, anything else is
paper. The page switches it (POST /api/mode), only while no trade is open.

Reads from .angel.env: the Angel login (ANGEL_*), TELEGRAM_BTST_CHANNEL (the
signal channel, e.g. https://t.me/btst_karo), TELEGRAM_API_ID / _HASH (the
user login, see arb/utils.py), GEMINI_API_KEY (signal reading), DATABASE_URL
(MongoDB), TELEGRAM_BOT_TOKEN / _CHAT_ID (alerts). The procedure is
btst/strategy.py; settings are config/btst.json.
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
from btst.strategy import BtstStrategy                             # noqa: E402
from services.mongo import MongoStore                              # noqa: E402

MODE_FILE = os.path.join(ROOT, ".btst_mode")
CONFIG = os.path.join(ROOT, "config", "btst.json")
READER_RETRY_SEC = 300.0


def log(msg):
    print(f"[{now_ist():%H:%M:%S}] {msg}", flush=True)


def read_mode():
    try:
        m = open(MODE_FILE).read().strip().lower()
    except OSError:
        m = ""
    return "live" if m == "live" else "paper"


class Session:
    """One Angel login a day (the session token does not outlive it)."""

    def __init__(self, creds):
        self.creds, self.api, self.day = creds, None, None
        self.lock = threading.Lock()

    def ensure(self):
        with self.lock:
            today = now_ist().date()
            if self.api is None or self.day != today:
                self.api = AngelSession(self.creds).login(retry_for_sec=120.0, log=log).api
                self.day = today
            return self.api


class Api:
    """The current day's SmartConnect, logging in again when the day turns."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s.ensure(), name)


def make_llm(cfg, env):
    try:
        if str(cfg.get("ai_provider", "gemini_api")) == "vertex":
            from services.vertex_ai_mime import VertexAILLM
            return VertexAILLM(max_retries=1, timeout_sec=60)
        from services.vertex_ai_mime import GeminiAPILLM
        key = str(env.get("GEMINI_API_KEY", "")).strip()
        if not key:
            log("GEMINI_API_KEY is not set: signals are read by the strict pattern alone")
            return None
        return GeminiAPILLM(api_key=key, model_name=cfg.get("ai_model"), max_retries=1, timeout_sec=60)
    except Exception as e:
        log(f"no model client ({e!r}): signals are read by the strict pattern alone")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-telegram-alerts", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG))
    env = _load_env_file(os.path.join(ROOT, ".angel.env"))
    env.update({k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "GEMINI_"))})
    channel = str(env.get("TELEGRAM_BTST_CHANNEL", "")).strip()
    if not channel:
        raise SystemExit("TELEGRAM_BTST_CHANNEL is not set in .angel.env (e.g. https://t.me/btst_karo)")
    creds = Credentials.load()
    if creds.missing():
        raise SystemExit(f"missing Angel credentials in .angel.env: {creds.missing()}")

    store = MongoStore(db_name=cfg.get("mongo_db", "trading_btst"))
    store.ping()
    store.ensure_indexes()
    sess = Session(creds)
    sess.ensure()
    api = Api(sess)
    llm = make_llm(cfg, env)

    masters = {"day": None, "rows": None}

    def master():
        today = now_ist().date()
        if masters["day"] != today:
            masters["rows"], masters["day"] = load_scrip_master(), today
        return masters["rows"]

    def make_broker(mode):
        live = AngelBtstBroker(api, protection_pct=cfg.get("market_protection_pct", 2.5), log=log)
        return live if mode == "live" else PaperBtstBroker(live.ltp, log=log)

    notifier = None
    if cfg.get("notify_telegram", True) and not args.no_telegram_alerts \
            and creds.telegram_bot_token and creds.telegram_chat_id:
        from arb.telegram import TelegramNotifier
        notifier = TelegramNotifier(creds.telegram_bot_token, creds.telegram_chat_id).start()

    mode = read_mode()
    strategy = BtstStrategy(cfg, store, make_broker(mode), master, llm, channel, log=log,
                            notifier=notifier, cost_model=CostModel.load())
    strategy.resume()
    lock = threading.Lock()

    def switch_mode(to):
        if to not in ("paper", "live"):
            return {"ok": False, "error": "to must be paper or live"}
        with lock:
            if store.open_job():
                return {"ok": False, "error": "a trade is open - switch once it is closed"}
            with open(MODE_FILE, "w") as fh:
                fh.write(to)
            strategy.broker = make_broker(to)
            log(f"mode switched to {to.upper()}")
            if notifier:
                notifier.send(f"🌙 <b>BTST</b> mode is now <b>{to.upper()}</b>")
            return {"ok": True, "mode": to}

    port = args.port or int(cfg.get("port", 9000))
    web.serve(strategy, store, switch_mode, port=port)
    log(f"BTST desk {mode.upper()} | page http://0.0.0.0:{port} | channel {channel} | "
        f"scan {cfg.get('scan_start')}-{cfg.get('scan_end')} | model {cfg.get('ai_model') if llm else 'none'}")
    if notifier:
        notifier.send(f"🌙 <b>BTST DESK UP</b> · <code>{mode.upper()}</code>\nChannel {channel}\nPage :{port}")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def start_reader():
        return read_channel_messages(channel=channel, last_messages=int(cfg.get("read_last_messages", 50)),
                                     on_message=strategy.on_message,
                                     poll_sec=float(cfg.get("channel_poll_sec", 5)), stop_event=stop, log=log)
    reader, reader_at = start_reader(), time.time()
    while not stop.is_set():
        if not reader.is_alive():
            strategy.status_note = "Telegram reader stopped (see the log) - retrying every 5 min"
            if time.time() - reader_at > READER_RETRY_SEC:
                reader, reader_at = start_reader(), time.time()
        strategy.step()
        stop.wait(float(cfg.get("poll_sec", 3)))
    log("BTST desk stopping")
    if notifier:
        notifier.stop()


if __name__ == "__main__":
    main()
