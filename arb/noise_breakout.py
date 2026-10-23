"""Noise-area breakout (Zarattini, Aziz & Barbon 2024), as pure functions.

The rule, on COMPLETED 1-minute index bars (t = a bar's END minute, its start
HH:MM + 1):

    sigma(t)  mean over the previous `ndays` sessions (need >= `min_days`) of
              |close_t / that session's first-bar open - 1|
    bounds    ub = max(o, pc) * (1 + vm * sigma(t)), lb = min(o, pc) * (1 - vm * sigma(t))
              o = today's first-bar open, pc = the previous session's last close
    entry     only on the `grid`-minute grid from 09:15, lo <= t <= hi: long when
              the close is above ub, short when below lb
    stop      band = max(ub, twap) long / min(lb, twap) short (twap = mean of
              (h + l + c) / 3 over today's bars so far), clamped to at least
              minr x ATR20 of the PRIOR 20 bars from the close
    trail     a later bar whose low (long) / high (short) reaches the stop exits;
              on grid bars the stop then ratchets toward band(j), only tighter

This is research_out/momentum/screen.py (prep, gen kind "noise", band, twap_at,
exit_walk trail "noise") bar for bar, plus the two live gates the evidence
used (research_out/no_retest/flow_noise_robust.py): the half-day pace ratio
(atr_ratio_series) and the 4-hour trend (arb/regime.py:trend_4h, which equals
research/lab/common.py Features.h4_ret). research/noise_parity.py checks the
match over the year.

No I/O, no clock: everything is a function of the rows passed in.
Rows are [iso, open, high, low, close, (volume)], oldest first.
"""
import numpy as np

ORIGIN = 9 * 60 + 15                     # the grid starts at the 09:15 open
SESSION = ("09:15", "15:29")             # bar START times kept (research load_rows)
HALF = 12 * 60 + 15                      # research/lab/common.py HALF (atr_half_split)


def hhmm(s):
    """'HH:MM' -> minutes since midnight."""
    return int(str(s)[:2]) * 60 + int(str(s)[3:5])


def end_minute(iso):
    """A bar's END minute of day: its start HH:MM + 1."""
    return int(iso[11:13]) * 60 + int(iso[14:16]) + 1


def in_session(rows):
    """Rows whose bar starts inside 09:15 - 15:29, as the research data."""
    return [r for r in rows if SESSION[0] <= str(r[0])[11:16] <= SESSION[1]]


def by_day(rows):
    """{'YYYY-MM-DD': [rows]} in the rows' order."""
    out = {}
    for r in rows:
        out.setdefault(str(r[0])[:10], []).append(r)
    return out


def on_grid(t, grid=30, origin=ORIGIN):
    return (t - origin) % int(grid) == 0


# ── sigma ────────────────────────────────────────────────────────────────────
def sigma_profile(days_rows, ndays=14, min_days=10):
    """{end_minute: sigma} from the sessions in `days_rows` ({day: rows}),
    using the LAST `ndays` of them; {} when fewer than `min_days` exist.
    The caller passes only sessions before the trading day (screen.prep:
    keys[k-14:k], need >= 10)."""
    keys = sorted(days_rows)[-int(ndays):] if ndays > 0 else []
    if len(keys) < int(min_days) or not keys:
        return {}
    acc = {}
    for d in keys:
        rows = days_rows[d]
        if not rows:
            continue
        po = float(rows[0][1])
        for r in rows:
            acc.setdefault(end_minute(str(r[0])), []).append(abs(float(r[4]) / po - 1))
    return {t: float(np.mean(v)) for t, v in acc.items()}


# ── bars ─────────────────────────────────────────────────────────────────────
def true_ranges(rows):
    """TR per bar with the previous bar's close (the first bar: high - low),
    as screen.prep (across sessions, like the research series)."""
    out = []
    for i, r in enumerate(rows):
        h, l = float(r[2]), float(r[3])
        pc = float(rows[i - 1][4]) if i else float(r[4])
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def prior_atr(rows, i, period=20):
    """ATR of bars i-period .. i-1 (PRIOR bars only, screen.prep's atr[i]);
    0.0 unless i >= period + 1."""
    if i < period + 1:
        return 0.0
    tr = true_ranges(rows[i - period - 1:i])[1:]       # bars i-period .. i-1
    return sum(tr) / period


def pace_ratio(rows, period=20, base=200, cap=(0.5, 2.0)):
    """The research pace for the LAST row (flow_noise_robust.atr_ratio_series):
    ATR20 of the last 20 bars INCLUDING it, over the median TR of the `base`
    bars before it, capped; 0.0 with too little history (i < base + 21)."""
    i = len(rows) - 1
    if i < base + period + 1:
        return 0.0
    tr = true_ranges(rows[i - base - 1:])[1:]          # bars i-base .. i
    b = float(np.median(tr[:base]))
    a = sum(tr[base - period + 1:]) / period             # bars i-19 .. i
    if b <= 0:
        return 0.0
    return max(cap[0], min(cap[1], a / b))


def pace_ok(ratio, t, halves, half=HALF):
    """research/lab/common.py gate_halves: the first-half floor before 12:15
    (bar end minute), the second-half one after; 0 disables, unknown passes."""
    x = float(halves[0] if t < half else halves[1])
    return x <= 0 or ratio <= 0 or ratio >= x


# ── the band ─────────────────────────────────────────────────────────────────
def bounds(o, pc, s, vm=1.0):
    return max(o, pc) * (1 + vm * s), min(o, pc) * (1 - vm * s)


def twap(today_rows):
    xs = [(float(r[2]) + float(r[3]) + float(r[4])) / 3 for r in today_rows]
    return sum(xs) / len(xs)


def band(today_rows, side, o, pc, sigma, vm=1.0):
    """The stop level at the last of `today_rows` (today's bars up to j):
    max(ub, twap) long, min(lb, twap) short; None without a sigma there."""
    s = sigma.get(end_minute(str(today_rows[-1][0])))
    if s is None:
        return None
    ub, lb = bounds(o, pc, s, vm)
    tw = twap(today_rows)
    return max(ub, tw) if side > 0 else min(lb, tw)


def today_of(rows):
    """Index of the first row of the last row's session."""
    day = str(rows[-1][0])[:10]
    k = len(rows) - 1
    while k > 0 and str(rows[k - 1][0])[:10] == day:
        k -= 1
    return k


# ── the entry ────────────────────────────────────────────────────────────────
def signal(rows, prev_close, sigma, vm=1.0, minr=1.5, lo="09:45", hi="14:45", grid=30,
           period=20, require_open=None):
    """The decision on the LAST of `rows` (completed bars, oldest first). They
    must hold the whole of today from its first bar (o is that bar's open) and
    at least `period` + 1 bars before the last (the ATR); prior sessions may be
    included. `require_open` ('09:15'): refuse when today's first bar starts
    later - a missing opening bar is a wrong o, not a different day.

    Returns a dict: side (+1 / -1 / 0), why, and when side != 0: close, stop,
    ub, lb, sigma, band, atr, t, o, pc."""
    if not rows:
        return {"side": 0, "why": "no_rows"}
    t = end_minute(str(rows[-1][0]))
    if not (hhmm(lo) <= t <= hhmm(hi)):
        return {"side": 0, "why": "outside_window", "t": t}
    if not on_grid(t, grid):
        return {"side": 0, "why": "off_grid", "t": t}
    a = prior_atr(rows, len(rows) - 1, period)
    if a <= 0:
        return {"side": 0, "why": "atr_unknown", "t": t}
    s = sigma.get(t) if sigma else None
    if s is None:
        return {"side": 0, "why": "no_sigma", "t": t}
    k = today_of(rows)
    today = rows[k:]
    if require_open and str(today[0][0])[11:16] != require_open:
        return {"side": 0, "why": "open_bar_missing", "t": t}
    o = float(today[0][1])
    pc = float(prev_close) if prev_close else o
    ub, lb = bounds(o, pc, s, vm)
    c = float(rows[-1][4])
    side = 1 if c > ub else -1 if c < lb else 0
    base = {"t": t, "o": o, "pc": pc, "ub": ub, "lb": lb, "sigma": s, "close": c, "atr": a}
    if not side:
        return dict(base, side=0, why="inside_noise_area")
    lvl = band(today, side, o, pc, sigma, vm)
    stop = min(lvl, c - minr * a) if side > 0 else max(lvl, c + minr * a)
    return dict(base, side=side, why="breakout", band=lvl, stop=stop)


# ── the trail ────────────────────────────────────────────────────────────────
def trail_step(state, today_rows, o, pc, sigma, vm=1.0, grid=30, last=None):
    """One completed bar after the entry (the last of `today_rows`, today's
    bars up to it), screen.exit_walk with trail "noise".

    `state`: {"side", "stop"}. Returns a NEW state; when the trade is over it
    carries "exit": {"reason": "stop" | "eod", "px": the research's index exit,
    "iso": the bar}. The stop is checked first; then, if `last` (end minute)
    is reached, an "eod" exit at the close; then on a grid bar the stop moves
    toward band(j), only ever tighter."""
    st = dict(state)
    r = today_rows[-1]
    side, stop = st["side"], float(st["stop"])
    h, l = float(r[2]), float(r[3])
    if (side > 0 and l <= stop) or (side < 0 and h >= stop):
        px = min(float(r[1]), stop) if side > 0 else max(float(r[1]), stop)
        st["exit"] = {"reason": "stop", "px": px, "iso": str(r[0])}
        return st
    t = end_minute(str(r[0]))
    if last is not None and t >= last:
        st["exit"] = {"reason": "eod", "px": float(r[4]), "iso": str(r[0])}
        return st
    if on_grid(t, grid):
        lvl = band(today_rows, side, o, pc, sigma, vm)
        if lvl is not None:
            new = max(stop, lvl) if side > 0 else min(stop, lvl)
            if new != stop:
                st["stop"] = new
                st["moved"] = {"iso": str(r[0]), "stop": new}
            else:
                st.pop("moved", None)
        else:
            st.pop("moved", None)
    else:
        st.pop("moved", None)
    return st


def trail(rows_after, today_before, side, stop, o, pc, sigma, vm=1.0, grid=30, last=None):
    """Walk `rows_after` (today's completed bars after the entry bar, in order)
    from the entry stop; `today_before` = today's bars up to and including the
    entry bar. Returns (final state, stop path [(iso, stop)])."""
    st = {"side": side, "stop": stop}
    today = list(today_before)
    path = []
    for r in rows_after:
        today.append(r)
        st = trail_step(st, today, o, pc, sigma, vm, grid, last)
        if "exit" in st:
            break
        if "moved" in st:
            path.append((st["moved"]["iso"], st["moved"]["stop"]))
    return st, path
