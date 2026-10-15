"""Decides which updates to send, sends them once, and records every message in the database.

The `notifications` table is what the dashboard's Updates page shows, so messages are visible there
even when Telegram is not set up (status 'not_configured') or sending failed (status 'failed').
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .. import engine
from ..data import db
from ..log import get_logger
from . import messages
from .telegram import Secret, TelegramClient, TelegramError, load_secret

log = get_logger("notify")
IST = ZoneInfo("Asia/Kolkata")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL,
    text TEXT NOT NULL, status TEXT NOT NULL, error TEXT, data_mode TEXT);
CREATE INDEX IF NOT EXISTS ix_notif_key ON notifications(key);
CREATE TABLE IF NOT EXISTS pattern_snapshots (
    run_time TEXT NOT NULL, index_name TEXT NOT NULL, rule_id TEXT NOT NULL, rule TEXT, verdict TEXT,
    t_in REAL, t_out REAL, PRIMARY KEY (run_time, index_name, rule_id));
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def already_sent(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM notifications WHERE key=? AND status='sent' LIMIT 1", (key,)).fetchone() is not None


def deliver(conn, kind: str, key: str, text: str, secret: Secret | None, client: TelegramClient | None,
            force: bool = False) -> str:
    """Send once per key (unless force) and record the attempt. Returns the status."""
    ensure_schema(conn)
    if not force and already_sent(conn, key):
        return "duplicate"
    status, error = "not_configured", None
    if secret and secret.chat_ids and client:
        try:
            for chat in secret.chat_ids:
                client.send(chat, text)
            status = "sent"
        except TelegramError as exc:
            status, error = "failed", str(exc)
            log.warning("Telegram send failed (%s): %s", kind, exc)
    conn.execute("INSERT INTO notifications (created_at, kind, key, text, status, error, data_mode) VALUES (?,?,?,?,?,?,?)",
                 (db.now_utc(), kind, key, text, status, error, db.data_mode(conn)))
    conn.commit()
    return status


def read_notifications(conn, limit: int = 200) -> pd.DataFrame:
    ensure_schema(conn)
    return pd.read_sql_query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", conn, params=(limit,))


def _snapshot(conn, res: pd.DataFrame) -> pd.DataFrame | None:
    prev_time = conn.execute("SELECT MAX(run_time) FROM pattern_snapshots").fetchone()[0]
    prev = pd.read_sql_query("SELECT index_name AS 'index', rule_id, verdict FROM pattern_snapshots WHERE run_time=?",
                             conn, params=(prev_time,)) if prev_time else None
    ts = db.now_utc()
    conn.executemany("INSERT OR REPLACE INTO pattern_snapshots VALUES (?,?,?,?,?,?,?)",
                     [(ts, r["index"], r["rule_id"], r["rule"], r["verdict"], r["t_in"], r["t_out"])
                      for _, r in res.iterrows()])
    return prev


def weekly_due(conn, settings, now: datetime | None = None) -> bool:
    if not settings.telegram["weekly_pattern_check"]:
        return False
    t = (now or datetime.now(IST)).astimezone(IST)
    last = conn.execute("SELECT MAX(created_at) FROM notifications WHERE kind='weekly' AND status IN ('sent','not_configured')"
                        ).fetchone()[0]
    recent = last is not None and (pd.Timestamp(t.replace(tzinfo=None)) - pd.Timestamp(last).tz_localize(None)).days < 6
    return t.strftime("%A") == settings.telegram["weekly_day"] and not recent


def after_update(paths, settings, rep=None, demo: bool = False, client: TelegramClient | None = None,
                 now: datetime | None = None) -> list[tuple[str, str]]:
    """Build and deliver all due messages. Returns [(kind, status)]."""
    secret = load_secret(paths)
    if client is None and secret:
        client = TelegramClient(secret.token)
    db_path = paths.db_file(demo)
    ctx = engine.load_context(db_path, settings)
    out = []
    if not ctx.indices:
        return out
    tg = settings.telegram
    dec = engine.run_decompositions(ctx, settings)
    with db.connect(db_path) as conn:
        ensure_schema(conn)
        if tg["daily_summary"]:
            reg, vix = engine.run_regime(ctx)
            scan = engine.run_scan(ctx, settings)
            text = messages.daily_summary(ctx, settings, dec, reg, vix, scan, engine.run_forecasts(ctx, settings))
            out.append(("summary", deliver(conn, "summary", f"summary:{ctx.asof:%Y-%m-%d}", text, secret, client)))
        if tg["unusual_alerts"]:
            for key, text in messages.unusual_alerts(ctx, dec):
                out.append(("unusual", deliver(conn, "unusual", key, text, secret, client)))
        if tg["data_problem_alerts"]:
            dp = messages.data_problems(ctx, rep)
            if dp:
                out.append(("data", deliver(conn, "data", dp[0], dp[1], secret, client)))
        if weekly_due(conn, settings, now):
            res, _, total = engine.run_patterns(ctx, settings, register=True)
            if not res.empty:
                prev = _snapshot(conn, res)
                text = messages.weekly_patterns(ctx, res, prev, total)
                out.append(("weekly", deliver(conn, "weekly", f"weekly:{(now or datetime.now(IST)):%Y-%m-%d}", text,
                                              secret, client)))
    return out
