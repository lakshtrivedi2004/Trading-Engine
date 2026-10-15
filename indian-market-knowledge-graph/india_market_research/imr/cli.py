"""Command line:  imr <command>   (or  python -m imr <command>)

  imr update        download missing days, FII/DII, holidays; run data-health checks
  imr check         re-run data-health checks on stored data (no internet)
  imr demo          build the SIMULATED demo database (no internet)
  imr status        what is stored, per series
  imr dashboard     open the dashboard in your browser
  imr report        write today's HTML snapshot to reports/
  imr telegram ...  setup | test | run | status   (Telegram bot)
  imr notify        send the due Telegram updates now (normally automatic after 'imr update')
  imr schedule ...  install [--bot] | remove | status   (daily automatic update, always-on bot)
"""
from __future__ import annotations

import argparse
import sys

from .log import UserError, setup_logging
from .paths import get_paths
from .settings import load_settings

STATUS_ICON = {"ok": "  ok ", "warn": " WARN", "fail": " FAIL"}


def _print_health(health, show_ok: bool = False) -> None:
    if health is None or health.empty:
        return
    rows = health if show_ok else health[health["status"] != "ok"]
    n_warn, n_fail = (health["status"] == "warn").sum(), (health["status"] == "fail").sum()
    print(f"\nData health: {len(health)} checks, {n_warn} warning(s), {n_fail} failure(s).")
    for r in rows.itertuples():
        print(f"{STATUS_ICON[r.status]}  {r.message}")
        if r.status != "ok" and str(getattr(r, "detail", "")).startswith("What to do"):
            print(f"        {r.detail}")


def cmd_update(args, paths, settings, log) -> int:
    from .data import db
    from .data.update import run_update

    with db.connect(paths.db_file()) as conn:
        if db.get_meta(conn, "data_mode") == "demo":
            raise UserError("data/market.db is marked as a demo database.",
                            "Delete data/market.db and run 'imr update' again.")
        log.info("Updating data in %s ...", paths.db_file())
        rep = run_update(conn, settings, paths, use_network=not args.offline)
    for m in rep.messages:
        print(" -", m)
    _print_health(rep.health, args.all)
    if rep.failed:
        print(f"\n{len(rep.failed)} series could not be downloaded: {', '.join(rep.failed)}. "
              f"Stored data (if any) is still used. Details: logs/imr.log")
    if not args.no_notify and not args.offline:
        _notify(paths, settings, rep)
    return 0


def _notify(paths, settings, rep=None, demo: bool = False) -> None:
    from .notify.dispatch import after_update
    from .notify.telegram import load_secret

    results = after_update(paths, settings, rep, demo=demo)
    if not results:
        return
    sent = [k for k, st in results if st == "sent"]
    if load_secret(paths) is None:
        print(f"\nUpdates prepared ({len(results)}) and shown on the dashboard's Updates page. "
              f"Telegram is not set up yet: run 'imr telegram setup'.")
    else:
        failed = [k for k, st in results if st == "failed"]
        print(f"\nTelegram: {len(sent)} message(s) sent" + (f", {len(failed)} failed (see Updates page)" if failed else "") + ".")


def cmd_check(args, paths, settings, log) -> int:
    from .data import db
    from .data.quality import run_checks

    with db.connect(paths.db_file(args.demo)) as conn:
        _print_health(run_checks(conn, settings), args.all)
    return 0


def cmd_demo(args, paths, settings, log) -> int:
    from .data import db
    from .data.demo import build_demo_db
    from .data.quality import run_checks
    from .data.update import load_config_files

    with db.connect(paths.db_file(demo=True)) as conn:
        info = build_demo_db(conn, settings)
        load_config_files(conn, paths)
        health = run_checks(conn, settings)
    print(f"SIMULATED demo database built: data/demo.db ({info['days']:,} days). "
          f"These numbers mean nothing about real markets.")
    _print_health(health, args.all)
    return 0


def cmd_status(args, paths, settings, log) -> int:
    from .data import db

    path = paths.db_file(args.demo)
    if not path.exists():
        print(f"No database yet at {path}. Run 'imr update' (or 'imr demo').")
        return 1
    with db.connect(path) as conn:
        mode = db.data_mode(conn)
        tbl = db.read_series_table(conn)
        flows = db.read_flows(conn)
        tests = db.count_tests(conn)
    print(f"Database: {path}  (mode: {mode.upper()})")
    if tbl.empty:
        print("No price series stored.")
    else:
        print(tbl[["series", "kind", "ticker_used", "first_date", "last_date", "rows", "source"]]
              .to_string(index=False))
    print(f"FII/DII flows: {0 if flows is None else len(flows)} days.  Pattern tests run (all time): {tests}.")
    return 0


def cmd_dashboard(args, paths, settings, log) -> int:
    from .web.app import serve

    if not paths.db_file().exists() and not paths.db_file(True).exists():
        raise UserError("No database yet.", "Run 'imr update' (real data) or 'imr demo' (simulated) first.")
    try:
        serve(args.port, open_browser=not args.no_browser)
    except OSError as exc:
        raise UserError(f"Could not start the dashboard on port {args.port} ({exc}).",
                        f"Another program may be using that port: try 'imr dashboard --port {args.port + 1}'.")
    return 0


def cmd_report(args, paths, settings, log) -> int:
    from . import engine
    from .report.html import write_report

    path = paths.db_file(args.demo)
    if not path.exists():
        raise UserError(f"No database at {path}.", "Run 'imr update' (or 'imr demo') first.")
    ctx = engine.load_context(path, settings)
    if not ctx.indices:
        raise UserError("No usable index data in the database.", "Run 'imr update' and check the data-health messages.")
    out = write_report(ctx, settings, paths)
    print(f"Report saved: {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())
    return 0


def cmd_notify(args, paths, settings, log) -> int:
    _notify(paths, settings, demo=args.demo)
    return 0


def cmd_telegram(args, paths, settings, log) -> int:
    from .notify.telegram import Secret, TelegramClient, TelegramError, load_secret, save_secret

    if args.action == "setup":
        token = args.token or input("Paste the bot token from @BotFather (looks like 123456:ABC...): ").strip()
        client = TelegramClient(token)
        try:
            me = client.get_me()
        except TelegramError as exc:
            raise UserError(f"Telegram did not accept that token ({exc}).",
                            "Copy the token again from @BotFather in Telegram (/mybots -> your bot -> API Token).")
        chat_ids = [int(c) for c in args.chat_id] if args.chat_id else []
        if not chat_ids:
            print(f"Token OK: @{me['username']}. Now open Telegram, find @{me['username']} and send it: /start")
            print("Waiting up to 2 minutes for your message...")
            import time as _t
            deadline, offset = _t.time() + 120, None
            while _t.time() < deadline and not chat_ids:
                for u in client.updates(offset, wait=20):
                    offset = u["update_id"] + 1
                    chat = (u.get("message") or {}).get("chat") or {}
                    if chat.get("id"):
                        chat_ids = [int(chat["id"])]
                        print(f"Linked chat: {chat.get('first_name') or chat.get('title') or chat['id']}")
                        break
            if not chat_ids:
                raise UserError("No /start message arrived within 2 minutes.",
                                "Run 'imr telegram setup' again and send /start to the bot while it waits.")
        path = save_secret(paths, Secret(token, chat_ids, me.get("username", "")))
        client.send(chat_ids[0], "✅ India Index Research is connected. Send /help for commands.")
        print(f"Saved to {path} (private file). Test it with 'imr telegram test'; start the command bot with "
              f"'imr telegram run' or install it as a service with 'imr schedule install --bot'.")
        return 0
    secret = load_secret(paths)
    if secret is None:
        raise UserError("Telegram is not set up.", "Run 'imr telegram setup' first.")
    if args.action == "status":
        from .notify.bot import heartbeat_file
        hb = heartbeat_file(paths)
        print(f"Bot @{secret.bot_username or '?'}, token {secret.masked}, linked chats: {secret.chat_ids}")
        print(f"Last bot heartbeat: {hb.read_text().strip() if hb.exists() else 'never (bot not running)'}")
        return 0
    if args.action == "test":
        client = TelegramClient(secret.token)
        for chat in secret.chat_ids:
            client.send(chat, "🔔 Test message from India Index Research. Everything works.")
        print(f"Test message sent to {len(secret.chat_ids)} chat(s).")
        return 0
    if args.action == "run":
        from .notify.bot import Bot
        Bot(paths, settings, secret).run_forever()
        return 0
    return 1


def cmd_schedule(args, paths, settings, log) -> int:
    from . import schedule

    if args.action == "install":
        for line in schedule.install(paths.root, args.bot, args.time):
            print(" -", line)
        print("\nThe update now runs Mon-Fri at", args.time, "India time" + (", and the bot runs all the time." if args.bot else "."))
        print("To keep them running while you are logged out, run once:  loginctl enable-linger $USER")
        print("No systemd? Use this cron line instead (crontab -e):\n  " + schedule.cron_line(paths.root))
    elif args.action == "remove":
        for line in schedule.remove():
            print(" -", line)
    else:
        print(schedule.status())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="imr", description="India Index Research Platform")
    p.add_argument("-v", "--verbose", action="store_true", help="show debug messages")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("update", help="download missing data and run data-health checks")
    s.add_argument("--offline", action="store_true", help="only load config files and re-check stored data")
    s.add_argument("--no-notify", action="store_true", help="don't send Telegram updates after this run")
    s.add_argument("--all", action="store_true", help="also list checks that passed")
    s = sub.add_parser("check", help="re-run data-health checks (no internet)")
    s.add_argument("--demo", action="store_true")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("demo", help="build the SIMULATED demo database")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("status", help="show what is stored")
    s.add_argument("--demo", action="store_true")
    s = sub.add_parser("dashboard", help="open the dashboard in your browser")
    s.add_argument("--port", type=int, default=8501)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    s = sub.add_parser("notify", help="send due Telegram updates now")
    s.add_argument("--demo", action="store_true")
    s = sub.add_parser("telegram", help="Telegram bot: setup | test | run | status")
    s.add_argument("action", choices=["setup", "test", "run", "status"])
    s.add_argument("--token", help="bot token (otherwise you are asked)")
    s.add_argument("--chat-id", action="append", help="chat id to allow (otherwise detected from your /start)")
    s = sub.add_parser("schedule", help="daily automatic update / always-on bot: install | remove | status")
    s.add_argument("action", choices=["install", "remove", "status"])
    s.add_argument("--bot", action="store_true", help="also run the Telegram command bot as a service")
    s.add_argument("--time", default="18:45", help="update time, India time (HH:MM), default 18:45")
    s = sub.add_parser("report", help="write today's HTML snapshot")
    s.add_argument("--demo", action="store_true")
    s.add_argument("--open", action="store_true", help="open it in your browser")
    args = p.parse_args(argv)

    paths = get_paths().ensure()
    log = setup_logging(paths.logs_dir, args.verbose)
    try:
        settings = load_settings(paths.settings_file)
        return {"update": cmd_update, "check": cmd_check, "demo": cmd_demo, "status": cmd_status,
                "dashboard": cmd_dashboard, "report": cmd_report, "notify": cmd_notify, "telegram": cmd_telegram,
                "schedule": cmd_schedule}[args.cmd](
            args, paths, settings, log)
    except UserError as exc:
        log.error("Problem: %s", exc)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except Exception as exc:  # last resort: log the traceback, show a short message
        log.debug("Unexpected error", exc_info=True)
        log.error("Unexpected error: %s: %s\nWhat to do next: see logs/imr.log for details; if it persists, "
                  "run with -v and share the log.", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
