"""The BTST desk's log - every line to stdout (systemd appends it to btst.log)
and to an in-memory ring, both readable from the page (/logs).

A line is "[YYYY-MM-DD HH:MM:SS] LEVEL area | text": the date is in every
line because a BTST spans two days. LEVEL is ERROR, WARN, TRADE (orders and
fills) or INFO. The code logs "LEVEL area | text" (btst/strategy.py, broker.py,
run_btst.py); lines from shared code that carry no level (the Telegram reader
in arb/utils.py) are filed as INFO or WARN under "channel" / "telegram".
Secrets are scrubbed (arb/feed.py:redact, a MongoDB URI's password, a Google
API key) before anything is written.
"""
import collections
import os
import re
import threading

from arb.instruments import now_ist

LEVELS = ("ERROR", "WARN", "TRADE", "INFO")
_TAGGED = re.compile(r"^(ERROR|WARN|TRADE|INFO)\s+([\w.-]+) \| (.*)$", re.S)
_LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (ERROR|WARN|TRADE|INFO)\s+([\w.-]+) \| (.*)$")
_BAD = ("failed", "error", "not logged in", "refused", "unreadable", "could not", "traceback", "exception")
_SECRETS = [(re.compile(r"(mongodb(?:\+srv)?://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),   # DATABASE_URL's password
            (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza***")]                      # a Google API key
GROUPS = {
    "errors": lambda r: r["level"] in ("ERROR", "WARN"),
    "trades": lambda r: r["level"] == "TRADE" or r["area"] in ("order", "entry", "exit", "close", "decision"),
    "signals": lambda r: r["area"] in ("message", "signal", "ai", "channel", "window", "day"),
    "ai": lambda r: r["area"] == "ai",
}


def fmt(level, area, text):
    """The form the BTST code hands to a log function: 'LEVEL area | text'."""
    return f"{level if level in LEVELS else 'INFO':5s} {area} | {text}"


class Logbook:
    def __init__(self, path=None, keep=5000, echo=True):
        self.path, self.echo = path, echo
        self.hidden = []                # words never shown (the signal channel's link and name)
        self.ring = collections.deque(maxlen=int(keep))
        self.lock = threading.Lock()

    def log(self, text, area=None):
        """Write one line; `text` is 'LEVEL area | text' or plain text (filed
        under `area`, WARN when it reads like a failure)."""
        from arb.feed import redact
        text = redact(str(text)).rstrip()
        for pat, rep in _SECRETS:
            text = pat.sub(rep, text)
        text = self._hide(text)
        m = _TAGGED.match(text)
        if m:
            level, area, body = m.group(1), m.group(2), m.group(3)
        else:
            if area is None:
                area = ("telegram" if text.startswith("[telegram-user]")
                        else ("channel" if text.startswith("[") else "btst"))
            # a channel message is the channel's words, not ours: never a warning
            level = "WARN" if area != "channel" and any(b in text.lower() for b in _BAD) else "INFO"
            body = text
        body = body.replace("\r", "").replace("\n", "\n    ")     # continuation lines stay with their entry
        line = f"[{now_ist():%Y-%m-%d %H:%M:%S}] {level:5s} {area} | {body}"
        with self.lock:
            self.ring.append(line)
        if self.echo:
            print(line, flush=True)
        return line

    def hide(self, *words):
        """Never show these (the channel's link, @name and name - owner, 24 Oct
        2026): replaced by "channel" in new lines and in lines read back."""
        for w in words:
            w = str(w or "").strip()
            if len(w) >= 3 and w not in self.hidden:
                self.hidden.append(w)
        self.hidden.sort(key=len, reverse=True)     # the link before the bare name inside it

    def _hide(self, text):
        for w in self.hidden:
            if w in text:
                text = text.replace(w, "channel")
        return text

    def sink(self, area):
        """A one-argument log function for code that logs plain text."""
        return lambda text: self.log(text, area=area)

    def _source(self, n):
        if self.path and os.path.isfile(self.path):
            from arb.dashboard import _tail_lines
            return _tail_lines(self.path, n=n), os.path.basename(self.path)
        with self.lock:
            return [x for e in list(self.ring)[-n:] for x in e.split("\n")], "memory (this run)"

    def lines(self, limit=500, group=None, q=None, day=None, scan=None):
        """Newest first: [{at, level, area, text}] after the filters, and where
        they were read from. A line that is not in the desk's own form (a
        library's print) is kept, as WARN when it looks bad; an uncaught
        traceback on stderr is one ERROR entry ("crash")."""
        if scan is None:                # a filter (an older day, a job id) has to look further back
            scan = 40000 if (group or q or day) else max(200, int(limit) * 4)
        raw, source = self._source(int(scan))
        if self.hidden:
            raw = [self._hide(x) for x in raw]          # older lines written before it was hidden
        out, cur, tb = [], None, False
        for line in raw:
            m = _LINE.match(line)
            if m:
                cur, tb = {"at": m.group(1), "level": m.group(2), "area": m.group(3), "text": m.group(4)}, False
                out.append(cur)
            elif not line.strip():
                continue
            elif line.startswith("Traceback"):              # an uncaught one, from stderr: a crash
                cur, tb = {"at": cur["at"] if cur else "", "level": "ERROR", "area": "crash", "text": line}, True
                out.append(cur)
            elif cur is not None and line.startswith((" ", "\t")):
                cur["text"] += "\n" + line                  # continues the entry above it
            elif tb:
                cur["text"] += "\n" + line                  # "SomeError: ..." closes the traceback
                tb = False
            else:
                cur = {"at": "", "level": "WARN" if any(b in line.lower() for b in _BAD) else "INFO",
                       "area": "other", "text": line}
                out.append(cur)
        for r in out:                       # a client hanging up (a scanner on the port): not a crash
            if (r["area"] == "crash" and r["text"].rstrip().endswith(("Connection reset by peer", "Broken pipe"))) \
                    or (r["area"] == "other" and r["text"].startswith("Exception occurred during processing of request")):
                r["level"], r["area"] = "INFO", "web"
        if day:
            out = [r for r in out if r["at"].startswith(day)]
        if group in GROUPS:
            out = [r for r in out if GROUPS[group](r)]
        if q:
            ql = str(q).lower()
            out = [r for r in out if ql in r["text"].lower() or ql in r["area"]]
        return out[::-1][:int(limit)], source
