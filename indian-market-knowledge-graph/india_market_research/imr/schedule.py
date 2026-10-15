"""Daily scheduled update and the always-on Telegram bot as systemd *user* services (no sudo needed).

Files written to ~/.config/systemd/user/:
  imr-update.service + imr-update.timer   run 'imr update' Mon-Fri at 18:45 India time (catch-up if missed)
  imr-bot.service                         run 'imr telegram run' continuously, restart on failure
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
DEFAULT_TIME = "18:45"


def imr_executable() -> str:
    exe = Path(sys.executable).with_name("imr")
    return str(exe) if exe.exists() else f"{sys.executable} -m imr"


def units(root: Path, at: str = DEFAULT_TIME, exe: str | None = None) -> dict[str, str]:
    exe = exe or imr_executable()
    env = f"Environment=IMR_HOME={root}\nWorkingDirectory={root}\n"
    return {
        "imr-update.service": f"[Unit]\nDescription=India Index Research - daily data update\nAfter=network-online.target\n"
                              f"Wants=network-online.target\n\n[Service]\nType=oneshot\n{env}ExecStart={exe} update\n",
        "imr-update.timer": f"[Unit]\nDescription=India Index Research - run the update every weekday evening\n\n"
                            f"[Timer]\nOnCalendar=Mon..Fri *-*-* {at}:00 Asia/Kolkata\nPersistent=true\n"
                            f"RandomizedDelaySec=120\n\n[Install]\nWantedBy=timers.target\n",
        "imr-bot.service": f"[Unit]\nDescription=India Index Research - Telegram bot\nAfter=network-online.target\n"
                           f"Wants=network-online.target\n\n[Service]\n{env}ExecStart={exe} telegram run\n"
                           f"Restart=always\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n",
    }


def cron_line(root: Path, exe: str | None = None) -> str:
    """Alternative for systems without systemd. Cron uses the computer's local time zone."""
    return (f"45 18 * * 1-5 IMR_HOME={root} {exe or imr_executable()} update >> {root}/logs/cron.log 2>&1"
            "   # 18:45 local time; adjust if your computer is not on India time")


def _systemctl(*args) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 127, "systemctl not found"
    p = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def install(root: Path, with_bot: bool, at: str = DEFAULT_TIME) -> list[str]:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in units(root, at).items():
        if name == "imr-bot.service" and not with_bot:
            continue
        (UNIT_DIR / name).write_text(text, encoding="utf-8")
        out.append(f"wrote {UNIT_DIR / name}")
    code, msg = _systemctl("daemon-reload")
    if code:
        return out + [f"systemctl failed: {msg}"]
    for unit in ["imr-update.timer"] + (["imr-bot.service"] if with_bot else []):
        code, msg = _systemctl("enable", "--now", unit)
        out.append(f"enabled {unit}" if code == 0 else f"could not enable {unit}: {msg}")
    return out


def remove() -> list[str]:
    out = []
    for unit in ["imr-update.timer", "imr-bot.service"]:
        _systemctl("disable", "--now", unit)
    for name in units(Path(".")):
        p = UNIT_DIR / name
        if p.exists():
            p.unlink()
            out.append(f"removed {p}")
    _systemctl("daemon-reload")
    return out or ["nothing was installed"]


def status() -> str:
    lines = []
    for unit in ["imr-update.timer", "imr-bot.service"]:
        code, msg = _systemctl("is-active", unit)
        lines.append(f"{unit}: {msg or 'unknown'}")
    code, msg = _systemctl("list-timers", "imr-update.timer", "--no-pager")
    if code == 0:
        lines.append(msg)
    return "\n".join(lines)
