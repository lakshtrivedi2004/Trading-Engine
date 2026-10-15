"""Minimal Telegram Bot API client (HTTPS via requests) and the local secret file.

The bot token and the allowed chat ids live in config/telegram_secret.toml (file mode 600), never in
settings.toml and never in logs. Only chats listed there can use the bot.
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..log import UserError, get_logger

log = get_logger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000                     # Telegram limit is 4096 characters per message


class TelegramError(Exception):
    pass


@dataclass
class Secret:
    token: str
    chat_ids: list[int] = field(default_factory=list)
    bot_username: str = ""

    @property
    def masked(self) -> str:
        return self.token[:6] + "…" + self.token[-4:] if len(self.token) > 12 else "…"


def secret_path(paths) -> Path:
    return paths.config_dir / "telegram_secret.toml"


def load_secret(paths) -> Secret | None:
    p = secret_path(paths)
    if not p.exists():
        return None
    try:
        d = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UserError(f"config/telegram_secret.toml is not readable ({exc}).",
                        "Run 'imr telegram setup' again to recreate it.")
    if not d.get("token"):
        return None
    return Secret(str(d["token"]), [int(c) for c in d.get("chat_ids", [])], str(d.get("bot_username", "")))


def save_secret(paths, secret: Secret) -> Path:
    p = secret_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (f'# Telegram bot credentials. Keep this file private (do not share or commit it).\n'
            f'token = "{secret.token}"\nchat_ids = [{", ".join(str(c) for c in secret.chat_ids)}]\n'
            f'bot_username = "{secret.bot_username}"\n')
    p.write_text(body, encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return p


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # a single giant line
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class TelegramClient:
    def __init__(self, token: str, post: Callable | None = None, timeout: float = 20.0):
        self.token = token
        self.timeout = timeout
        if post is None:
            import requests

            session = requests.Session()
            post = session.post
        self._post = post

    def call(self, method: str, http_timeout: float | None = None, **params):
        """`params` go to Telegram as-is (one of them may itself be called 'timeout', e.g. getUpdates);
        `http_timeout` is how long we wait for the HTTPS response."""
        try:
            r = self._post(API.format(token=self.token, method=method), json=params,
                           timeout=http_timeout or self.timeout)
        except Exception as exc:
            raise TelegramError(f"could not reach Telegram ({type(exc).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"Telegram returned HTTP {r.status_code}") from None
        if not data.get("ok"):
            desc = data.get("description", f"HTTP {r.status_code}")
            if r.status_code == 401:
                desc = "the bot token was rejected (401 Unauthorized)"
            raise TelegramError(desc)
        return data["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def send(self, chat_id: int, text: str) -> int:
        """Send HTML text, split into several messages if needed. Returns number of messages sent."""
        parts = split_message(text)
        for p in parts:
            self.call("sendMessage", chat_id=chat_id, text=p, parse_mode="HTML", disable_web_page_preview=True)
        return len(parts)

    def updates(self, offset: int | None = None, wait: int = 50) -> list[dict]:
        params = {"timeout": wait, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=wait + 15, **params)

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
