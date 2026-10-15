"""Telegram command bot (long polling). Run with:  imr telegram run   (or as the imr-bot service).

Only chat ids listed in config/telegram_secret.toml get answers; everyone else is told the bot is
private, and nothing about the data is revealed to them.
"""
from __future__ import annotations

import time
from typing import Callable

from .. import engine
from ..data import db
from ..log import get_logger
from . import messages
from .telegram import Secret, TelegramClient, TelegramError

log = get_logger("bot")

COMMANDS = [("today", "Today's summary: moves, explanations, regime"),
            ("movers", "BSE biggest gainers, losers and most volatile stocks"),
            ("forecast", "Next-session forecasts with their track record"),
            ("regime", "Trend, volatility, drawdown, India VIX"),
            ("health", "Data-health problems"),
            ("patterns", "Latest pattern-test verdicts"),
            ("status", "What data is stored and when it was updated"),
            ("help", "List commands")]
HELP = ("<b>India Index Research bot</b>\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS) +
        "\n\nI explain and test; I do not predict prices or give buy/sell advice.")


def heartbeat_file(paths):
    return paths.data_dir / "bot_heartbeat.txt"


class Bot:
    def __init__(self, paths, settings, secret: Secret, client: TelegramClient | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.paths, self.settings, self.secret = paths, settings, secret
        self.client = client or TelegramClient(secret.token)
        self.sleep = sleep
        self.offset: int | None = None
        self.warned: set[int] = set()

    # -------------------------------------------------------- answers
    def _ctx(self):
        path = self.paths.db_file(False)
        if not path.exists():
            path = self.paths.db_file(True)
        if not path.exists():
            return None
        return engine.load_context(path, self.settings)

    def answer(self, text: str) -> str:
        cmd = text.strip().split()[0].lower().split("@")[0] if text.strip() else ""
        if cmd in ("/start", "/help"):
            return HELP
        ctx = self._ctx()
        if ctx is None or not ctx.indices:
            return "No data stored yet. Run 'imr update' on the computer first."
        s = self.settings
        if cmd == "/today":
            dec = engine.run_decompositions(ctx, s)
            reg, vix = engine.run_regime(ctx)
            return messages.daily_summary(ctx, s, dec, reg, vix, engine.run_scan(ctx, s), engine.run_forecasts(ctx, s))
        if cmd == "/forecast":
            return messages.forecasts(ctx, engine.run_forecasts(ctx, s))
        if cmd == "/movers":
            return messages.movers(engine.run_scan(ctx, s))
        if cmd == "/regime":
            reg, vix = engine.run_regime(ctx)
            return messages.regime(ctx, reg, vix)
        if cmd == "/health":
            return messages.health(ctx)
        if cmd == "/patterns":
            with db.connect(ctx.db_path) as conn:
                from .dispatch import ensure_schema
                ensure_schema(conn)
                last = conn.execute("SELECT MAX(run_time) FROM pattern_snapshots").fetchone()[0]
                rows = conn.execute("SELECT index_name, rule, verdict FROM pattern_snapshots WHERE run_time=?",
                                    (last,)).fetchall() if last else []
            if not rows:
                return "No weekly pattern check stored yet. It runs automatically on " + s.telegram["weekly_day"] + "s."
            keep = [r for r in rows if not r[2].startswith("No reliable")]
            body = "\n".join(f"• {messages.E(i)} — {messages.E(r)}: <b>{messages.E(v)}</b>" for i, r, v in keep) or \
                "Every test: No reliable evidence."
            return f"🧪 <b>Pattern verdicts</b> (check of {last[:10]}, {len(rows)} tests)\n{body}"
        if cmd == "/status":
            with db.connect(ctx.db_path) as conn:
                tbl = db.read_series_table(conn)
            lines = [f"🗄 <b>Stored data</b> (mode {ctx.mode})"] + [
                f"• {messages.E(r.series)}: {r.rows} days, last {r.last_date}" for r in tbl.itertuples()]
            return "\n".join(lines)
        return "Unknown command. Send /help for the list."

    # -------------------------------------------------------- loop
    def handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = msg.get("text") or ""
        if chat is None or not text.startswith("/"):
            return
        if chat not in self.secret.chat_ids:
            if chat not in self.warned:
                self.client.send(chat, "This bot is private.")
                self.warned.add(chat)
                log.warning("Ignored command from unauthorised chat %s", chat)
            return
        try:
            reply = self.answer(text)
        except Exception as exc:                      # never let one bad command kill the bot
            log.exception("command %r failed", text)
            reply = f"Sorry, that failed ({type(exc).__name__}). Details are in logs/imr.log on the computer."
        self.client.send(chat, reply)

    def poll_once(self, wait: int = 50) -> int:
        ups = self.client.updates(self.offset, wait)
        for u in ups:
            self.offset = u["update_id"] + 1
            self.handle(u)
        return len(ups)

    def run_forever(self) -> None:
        try:
            self.client.set_commands(COMMANDS)
        except TelegramError as exc:
            log.warning("Could not register the command menu: %s", exc)
        log.info("Bot is running. Send /help to it in Telegram. Press Ctrl+C to stop.")
        backoff = 5
        while True:
            try:
                self.poll_once()
                backoff = 5
                heartbeat_file(self.paths).write_text(db.now_utc(), encoding="utf-8")   # not in the DB: keeps
                # the dashboard cache (keyed on the DB file's modification time) from being reset every poll
            except TelegramError as exc:
                log.warning("Telegram problem: %s (retrying in %ds)", exc, backoff)
                self.sleep(backoff)
                backoff = min(backoff * 2, 300)
