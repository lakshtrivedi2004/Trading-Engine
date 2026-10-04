#!/usr/bin/env python3
"""Follow the newest engine log and print only what matters, one line per event.

    venv/bin/python tools/live_watch.py            # newest logs/live_session_*.log
    venv/bin/python tools/live_watch.py --paper    # newest logs/session_*.log
    venv/bin/python tools/live_watch.py --from-start

Read-only: it never talks to Angel or to the engine. Events:
  TRADE   entries, exits, partial exits, adoptions
  ALERT   halts, CHECK THE ANGEL APP, square-off incomplete, refusals, naked
  FEED    reconnects, stalls, re-logins, history refused
  ERROR   loop errors, tracebacks, rate limits
  PULSE   the engine's once-a-minute status line, every --pulse minutes
It also follows logs/api_calls.csv and reports every rate-limited REST call.
"""
import argparse
import glob
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES = [
    ("TRADE", re.compile(r"ENTRY |EXIT |PARTIAL EXIT|adopted|RESTORED")),
    ("ALERT", re.compile(r"HALTED|CHECK THE ANGEL|SQUARE-OFF INCOMPLETE|NAKED|refused|"
                         r"LIVE START REFUSED|not filled|status unknown|daily loss|PAUSE|losses in a row")),
    ("FEED", re.compile(r"\[feed\]|history unavailable|historical API recovered|NO DATA|re-login")),
    ("ERROR", re.compile(r"Traceback|loop error|Error|EXIT FAILED|entry failed|exceeding access rate")),
]


def newest(pattern):
    files = sorted(glob.glob(os.path.join(ROOT, "logs", pattern)), key=os.path.getmtime)
    return files[-1] if files else None


def classify(line):
    for tag, rx in RULES:
        if rx.search(line):
            return tag
    if "ticks=" in line and "passes=" in line:
        return "PULSE"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--pulse", type=int, default=5, help="print the status line every N minutes")
    a = ap.parse_args()
    pattern = "session_*.log" if a.paper else "live_session_*.log"
    path = newest(pattern)
    while path is None:
        print(f"WAIT no logs/{pattern} yet", flush=True)
        time.sleep(15)
        path = newest(pattern)
    print(f"WATCH {path}", flush=True)
    fh = open(path, errors="replace")
    if not a.from_start:
        fh.seek(0, os.SEEK_END)
    api = os.path.join(ROOT, "logs", "api_calls.csv")
    api_pos = os.path.getsize(api) if os.path.exists(api) and not a.from_start else 0
    last_pulse = 0.0
    while True:
        line = fh.readline()
        if line:
            line = line.rstrip()
            tag = classify(line)
            if tag == "PULSE":
                if time.time() - last_pulse >= 60 * a.pulse:
                    last_pulse = time.time()
                    print(f"PULSE {line[:300]}", flush=True)
            elif tag:
                print(f"{tag} {line[:400]}", flush=True)
            continue
        # A newer session file (a restart) takes over.
        nxt = newest(pattern)
        if nxt and nxt != path:
            path = nxt
            fh = open(path, errors="replace")
            print(f"WATCH {path} (new session)", flush=True)
            continue
        if os.path.exists(api) and os.path.getsize(api) > api_pos:
            with open(api, errors="replace") as af:
                af.seek(api_pos)
                for row in af:
                    if ",rate_limited," in row or ",error," in row:
                        print(f"ERROR api {row.strip()[:300]}", flush=True)
                api_pos = af.tell()
        time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
