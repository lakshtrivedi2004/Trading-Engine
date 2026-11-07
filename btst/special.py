"""Special sessions (owner, 7 Nov 2026): a day the exchange opens for a short
session at other hours - Muhurat trading on Diwali, on a Sunday or a holiday
too. One line per day in config/btst.json:
    "special_sessions": {"2026-11-08": {"name": "Muhurat trading", "open": "14:20", "close": "15:40"}}
That day is a trading day whatever the calendar says - the open trade's next
session, and a signal read in its window is bought. The desk's clock times
move with the session: the ones at the open (market_open, gift_target_at,
gift_target_until, session_refresh_at) by open - market_open, the ones at the
close (market_close, exit_at, scan_start, scan_end) by close - market_close.
8 Nov 2026, 14:20 - 15:40: GIFT Nifty read for the target 14:16, out at 15:09,
signals read 15:10 - 15:40. Any of them set on the day's line is used as set.
"""
from datetime import date

from arb.instruments import holidays, market_phase, now_ist

AT_OPEN = ("market_open", "gift_target_at", "gift_target_until", "session_refresh_at")
AT_CLOSE = ("market_close", "exit_at", "scan_start", "scan_end")
PREOPEN_SEC = 15 * 60


def _hm(s):
    p = [int(x) for x in str(s).split(":")]
    return p[0] * 3600 + p[1] * 60 + (p[2] if len(p) > 2 else 0)


def _clock(secs):
    secs %= 86400
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}" + (f":{secs % 60:02d}" if secs % 60 else "")


def special(cfg, day):
    """The day's special session ({name, open, close ...}), or None."""
    s = (cfg.get("special_sessions") or {}).get(str(day)[:10])
    return s if isinstance(s, dict) and s.get("open") and s.get("close") else None


def at(cfg, day, key, value):
    """A setting on `day`: a clock time moved with that day's special session."""
    s = special(cfg, day)
    if not s or value is None or (key not in AT_OPEN and key not in AT_CLOSE):
        return value
    if key in s:
        return s[key]
    base, edge = ((cfg.get("market_open", "09:15"), s["open"]) if key in AT_OPEN
                  else (cfg.get("market_close", "15:45"), s["close"]))
    return _clock(_hm(value) + _hm(edge) - _hm(base))


def trading_day(cfg, day):
    d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    return bool(special(cfg, d)) or (d.weekday() < 5 and d.isoformat() not in holidays())


def phase(cfg, ref=None):
    """market_phase at the desk's hours - a special session's day open at its own."""
    ref = ref or now_ist()
    s = special(cfg, ref.date())
    if not s:
        return market_phase(ref, start=cfg.get("market_open", "09:15"), end=cfg.get("market_close", "15:45"))
    d = ref.date()
    o = _hm(at(cfg, d, "market_open", cfg.get("market_open", "09:15")))
    c = _hm(at(cfg, d, "market_close", cfg.get("market_close", "15:45")))
    t = ref.hour * 3600 + ref.minute * 60 + ref.second
    return "closed" if t < o - PREOPEN_SEC else "preopen" if t < o else "open" if t < c else "closed"


def upcoming(cfg, today):
    """The special sessions from today on, with the times the desk keeps on each."""
    out = []
    for d, s in sorted((cfg.get("special_sessions") or {}).items()):
        if d >= str(today)[:10] and special(cfg, d):
            out.append(dict(s, date=d, **{k: at(cfg, d, k, cfg.get(k, v)) for k, v in
                                          (("exit_at", "15:14"), ("scan_start", "15:15"), ("scan_end", "15:45"),
                                           ("gift_target_at", "09:11"))}))
    return out
