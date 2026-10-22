"""Small stand-alone helpers.

send_channel_message posts to the Telegram channel or group named in
.angel.env (TELEGRAM_CHANNEL_NAME) as the Telegram USER whose API app is
TELEGRAM_API_ID / TELEGRAM_API_HASH (my.telegram.org) - not the bot that
arb/telegram.py sends through. Needs telethon (venv/bin/pip install telethon).

The first use needs a one-time login - phone number, then the code Telegram
sends - which writes .cache/telegram/user.session. That file is a key to the
account: it is git-ignored (.cache/) and must never be shared. Once per
checkout:

    venv/bin/python arb/utils.py "hello"        # asks for phone + code, then sends

After that the function sends without asking anything.

read_channel_messages reads a channel through the same login: its last
read_last_messages messages at once, then every new one as it comes.
config/telegram.json sets the channel (https://t.me/btst_karo), how many of
the last messages to print at start (50) and the poll interval. run_bot
starts it when the engine starts.

    venv/bin/python arb/utils.py read                  # the last N, then live, until Ctrl+C
"""
import asyncio
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from arb.config import _load_env_file  # noqa: E402
from arb.instruments import IST  # noqa: E402

SESSION = os.path.join(ROOT, ".cache", "telegram", "user")
CONFIG = os.path.join(ROOT, "config", "telegram.json")


def send_channel_message(text, channel=None, parse_mode="html", interactive=False,
                         timeout=30.0, env_path=None, log=print):
    """Send `text` to TELEGRAM_CHANNEL_NAME (or `channel`): a @username, a
    t.me link, a numeric chat id, or the chat's exact title as Telegram shows
    it (the account must be a member). Returns True when sent.

    Never raises: a missing setting, no login yet, an unknown or ambiguous
    chat, a timeout or a network error returns False and logs why.
    interactive=True allows the one-time login prompt (phone + code)."""
    try:
        from telethon import TelegramClient
    except ImportError:
        log("[telegram-user] telethon is not installed: venv/bin/pip install telethon")
        return False
    env = _load_env_file(env_path or os.path.join(ROOT, ".angel.env"))
    env.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
    api_id = str(env.get("TELEGRAM_API_ID", "")).strip()
    api_hash = str(env.get("TELEGRAM_API_HASH", "")).strip()
    target = str(channel or env.get("TELEGRAM_CHANNEL_NAME", "")).strip()
    if not (api_id.isdigit() and api_hash and target):
        log("[telegram-user] TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_CHANNEL_NAME "
            "must be set in .angel.env")
        return False

    async def resolve(client):
        ref = int(target) if target.lstrip("-").isdigit() else target
        try:
            return await client.get_entity(ref)
        except Exception:
            pass
        # Not something Telegram resolves on its own: look through this
        # account's chats, which also puts their numeric ids in the cache.
        dialogs = await client.get_dialogs()
        if isinstance(ref, int):
            try:
                return await client.get_entity(ref)
            except Exception:
                return None
        # By title: an exact match only, so a near-miss never posts to the
        # wrong chat.
        want = target.lower()
        hits = [d.entity for d in dialogs if (d.name or "").strip().lower() == want]
        if len(hits) > 1:
            log(f"[telegram-user] {len(hits)} chats are titled {target!r} - "
                f"set TELEGRAM_CHANNEL_NAME to its @username or numeric id")
            return None
        return hits[0] if hits else None

    async def go():
        os.makedirs(os.path.dirname(SESSION), exist_ok=True)
        client = TelegramClient(SESSION, int(api_id), api_hash)
        try:
            if interactive:
                await client.start()            # prompts for phone and code the first time
            else:
                await client.connect()
                if not await client.is_user_authorized():
                    log("[telegram-user] not logged in yet - once, by hand: "
                        "venv/bin/python arb/utils.py \"test\"")
                    return False
            chat = await resolve(client)
            if chat is None:
                log(f"[telegram-user] no channel or group {target!r} among this account's chats")
                return False
            await client.send_message(chat, text, parse_mode=parse_mode, link_preview=False)
            return True
        finally:
            await client.disconnect()

    def run():
        try:
            return asyncio.run(go() if interactive else asyncio.wait_for(go(), timeout))
        except Exception as e:
            log(f"[telegram-user] send failed: {e!r}")
            return False

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    # Called from inside an event loop: asyncio.run cannot nest, so the send
    # gets a thread (and a loop) of its own.
    box = {}
    worker = threading.Thread(target=lambda: box.update(sent=run()), daemon=True)
    worker.start()
    worker.join()
    return box.get("sent", False)


READ_CHANNEL = "https://t.me/btst_karo"


def read_channel_messages(channel=None, last_messages=None, on_message=None, poll_sec=None,
                          background=True, stop_event=None, env_path=None, log=print,
                          config_path=None):
    """Read a channel through the logged-in user (see send_channel_message):
    its last `last_messages` messages at once, oldest first, then every new
    one as it arrives, until stop_event is set (or the process ends).

    channel, last_messages and poll_sec default to config/telegram.json
    (read_channel, read_last_messages, read_poll_sec); without it,
    https://t.me/btst_karo, 50 and 10 s. last_messages 0 prints none at start.

    on_message(msg) gets each telethon Message (msg.id, msg.date, msg.text);
    by default it is printed as "[btst_karo #id 21 Oct 16:40:05] text".
    Telegram pushes new posts only for channels the account has joined, so
    the channel is also polled every poll_sec seconds - it works joined or
    not, and nothing is printed twice.

    background=True runs in a daemon thread and returns it; False blocks.
    Never raises: no telethon, no login, an unknown channel or a lost
    connection is logged, and the reader retries or stops."""
    stop = stop_event or threading.Event()
    cfg = {}
    try:
        with open(config_path or CONFIG) as fh:
            cfg = json.load(fh) or {}
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"[telegram-user] {config_path or CONFIG} unreadable ({e!r}); using the defaults")
    channel = channel or cfg.get("read_channel") or READ_CHANNEL
    try:
        last_messages = max(0, int(cfg.get("read_last_messages", 50)
                                   if last_messages is None else last_messages))
        poll_sec = max(1.0, float(cfg.get("read_poll_sec", 10.0) if poll_sec is None else poll_sec))
    except (TypeError, ValueError) as e:
        log(f"[telegram-user] bad number in {config_path or CONFIG} ({e!r}); using 50 and 10 s")
        last_messages, poll_sec = 50, 10.0
    label = str(channel).rstrip("/").rsplit("/", 1)[-1].lstrip("@") or str(channel)

    def show(msg, what=""):
        when = msg.date.astimezone(IST).strftime("%d %b %H:%M:%S") if msg.date else "?"
        # raw_text: the words without Telegram's **bold** / __italic__ markers.
        text = getattr(msg, "raw_text", None) or msg.text or ""
        body = text.strip() or (f"[{type(msg.media).__name__}]" if msg.media else "[no text]")
        log(f"[{label}{what} #{msg.id} {when}] {body}")

    async def go():
        from telethon import TelegramClient, errors, events
        from telethon.sessions import SQLiteSession, StringSession
        env = _load_env_file(env_path or os.path.join(ROOT, ".angel.env"))
        env.update({k: v for k, v in os.environ.items() if k.startswith("TELEGRAM_")})
        api_id = str(env.get("TELEGRAM_API_ID", "")).strip()
        api_hash = str(env.get("TELEGRAM_API_HASH", "")).strip()
        if not (api_id.isdigit() and api_hash):
            log("[telegram-user] TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .angel.env")
            return
        if not os.path.exists(SESSION + ".session"):
            log("[telegram-user] not logged in yet - once, by hand: venv/bin/python arb/utils.py \"test\"")
            return
        # An in-memory copy of the login: the session file stays free for
        # send_channel_message, which opens it while this reader runs.
        disk = SQLiteSession(SESSION)
        try:
            copy = StringSession.save(disk)
        finally:
            disk.close()
        client = TelegramClient(StringSession(copy), int(api_id), api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                log("[telegram-user] not logged in yet - once, by hand: venv/bin/python arb/utils.py \"test\"")
                return
            chat = await client.get_entity(channel)
            seen, last = set(), 0

            def emit(msg, start=False):
                """Each message once. The ones printed at start always are;
                on_message, when given, gets every one."""
                nonlocal last
                if msg is None or msg.id in seen:
                    return
                seen.add(msg.id)
                last = max(last, msg.id)
                if len(seen) > 1000:
                    seen.difference_update(sorted(seen)[:500])
                try:
                    if start or on_message is None:
                        show(msg)
                    if on_message is not None:
                        on_message(msg)
                except Exception as e:
                    log(f"[telegram-user] on_message failed on #{msg.id}: {e!r}")

            # At start: the last N, oldest first. With N = 0 only the newest
            # id is taken, so live reading starts after it, as with any N.
            recent = list(await client.get_messages(chat, limit=max(1, last_messages)) or [])
            if not recent:
                log(f"[{label}] no messages yet")
            elif last_messages:
                log(f"[{label}] last {len(recent)} message{'s' if len(recent) != 1 else ''}, oldest first:")
                for msg in reversed(recent):
                    emit(msg, start=True)
            else:
                last = recent[0].id

            async def pushed(event):
                emit(event.message)
            client.add_event_handler(pushed, events.NewMessage(chats=chat))
            log(f"[telegram-user] reading {label} live (pushed if joined, polled every {poll_sec:g} s)")

            while not stop.is_set():
                try:
                    if not client.is_connected():
                        await client.connect()
                    fresh = await client.get_messages(chat, min_id=last, limit=50)
                    for msg in reversed(list(fresh or [])):
                        emit(msg)
                    wait = poll_sec
                except errors.FloodWaitError as e:
                    log(f"[telegram-user] Telegram asks to wait {e.seconds} s before reading again")
                    wait = e.seconds + 1
                except Exception as e:
                    log(f"[telegram-user] read failed, retrying: {e!r}")
                    wait = max(poll_sec, 15.0)
                for _ in range(int(wait * 10)):
                    if stop.is_set():
                        break
                    await asyncio.sleep(0.1)
        finally:
            await client.disconnect()

    def run():
        try:
            asyncio.run(go())
        except ImportError:
            log("[telegram-user] telethon is not installed: venv/bin/pip install telethon")
        except Exception as e:
            log(f"[telegram-user] channel reader stopped: {e!r}")

    if not background:
        run()
        return None
    worker = threading.Thread(target=run, name="channel-reader", daemon=True)
    worker.start()
    return worker


if __name__ == "__main__":
    if sys.argv[1:2] == ["read"]:
        try:
            read_channel_messages(*(sys.argv[2:3] or [None]), background=False)
        except KeyboardInterrupt:
            pass
    else:
        msg = " ".join(sys.argv[1:]) or "Trading-Engine: test message"
        print("sent" if send_channel_message(msg, interactive=True) else "not sent")
