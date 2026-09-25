"""Psychological price levels and the session ranges that hold the day's stops.

Two things the imported forex playbooks lean on that had nowhere to live here.

ROUND NUMBERS. Traders place orders at numbers they can say out loud. NIFTY
stalls at 24500 far more often than at 24487, and the reason is not geometry, it
is that "24500" is where brackets, targets and stops get typed. Osler (Journal
of Finance, 2003) is the citation usually given: stop-loss orders cluster just
beyond round numbers and take-profit orders cluster AT them, which produces two
distinct and opposite behaviours - price accelerates through a round number once
the stops beyond it go, and stalls into one while the profit-takers are filled.
Both matter here, and they matter in opposite directions, so this module reports
the level and the distance and refuses to guess which of the two is happening.

The practical use is not "buy at the round number". It is headroom: buying a
call a few points below a level that has already turned price away twice is
paying for a move with a wall in front of it. `headroom()` answers that, in ATR
units, and it is the honest half of this idea.

Which numbers count as round is DERIVED, never tabulated. A lookup table of
"NIFTY respects 50s, BANKNIFTY respects 100s" would be a guess frozen into
code, wrong for any instrument not in it and wrong for those in it on a day
whose volatility has changed. Instead the grid is chosen from the universal
"nice number" ladder - mantissas 1, 2.5 and 5 across the decades around the
price - by picking the spacing nearest a configured multiple of current ATR.
Levels price cannot reach inside the trade's horizon are not levels it will
react to, and a grid finer than the tape's own noise is a ruler, not a set of
levels. The same rule therefore produces 50s on NIFTY, 250s on BANKNIFTY and
5s on a 250-rupee stock, with nothing instrument-specific written down.

SESSION RANGES. The forex models run on a handover between liquidity centres -
Asia builds a range, London sweeps it, New York continues or reverses it. NSE
has no handover: one session, 09:15 to 15:30, and then nothing. Transplanting
"Asia high" literally would be reading a level built while the Indian market was
shut and no Indian stop was resting there.

What does transfer is the mechanism, not the clock. A liquidity pool is an
obvious price that a lot of people used as a reference, so their stops sit just
beyond it. On NSE the obvious prices are:

    PDH / PDL       the previous day's high and low. The strongest pools on the
                    chart, and the ones a gap open runs straight into.
    OPENING RANGE   the first stretch of the session, length configured. India
                    gaps often, so the open is a genuine price-discovery
                    auction and the range it settles into is where the day's
                    first brackets get placed.

Those are the NSE analogue of the Asia range, and they are used the same way:
a wick through, a close back inside, and the pool has been taken.
"""
from datetime import datetime

# The "nice number" ladder: every round price anyone actually quotes is one of
# these mantissas times a power of ten. Deriving the grid from this instead of a
# per-index lookup table means a new index, a new segment or a redenominated
# contract needs no code change and no configuration - the ladder is a property
# of how people write numbers, not of any particular instrument.
NICE = (1.0, 2.5, 5.0)


def _ladder(price, lo_decades=3, hi_decades=2):
    """Candidate round-number spacings around `price`, ascending.

    Built as NICE mantissas across the decades bracketing the price's own
    magnitude, so it scales itself: a 24,000 index gets 10/25/50/100/250/500/
    1000..., a 250 stock gets 0.1/0.25/0.5/1/2.5/5..., and neither is written
    down anywhere.
    """
    if price <= 0:
        return []
    import math
    decade = int(math.floor(math.log10(abs(price))))
    out = []
    for d in range(decade - lo_decades, decade + hi_decades + 1):
        for m in NICE:
            step = m * (10.0 ** d)
            if step > 0:
                out.append(step)
    return sorted(set(out))


def roundness(level, price=0.0):
    """How round a price is, as the spacing it is the first multiple of.

    24500 is a multiple of 500 but not of 1000; 24000 is a multiple of 1000.
    The larger the spacing a level belongs to, the more people are looking at
    it. This replaces any notion of a fixed "major"/"minor" label: significance
    is measured, not declared.
    """
    best = 0.0
    for step in _ladder(price or level):
        if step <= 0:
            continue
        # Tolerate float error proportional to the step being tested.
        if abs(level / step - round(level / step)) < 1e-6:
            best = max(best, step)
    return best


def grid_for(price, atr=0.0, span_atr=1.0, strike_step=0.0):
    """The round-number spacing worth watching, given how fast the tape is.

    A grid finer than the market's own noise is not a set of levels, it is a
    ruler - price crosses it constantly and nothing happens. A grid far coarser
    than the session's range never gets reached. So the spacing is chosen to sit
    near `span_atr` times the current ATR: the levels price can plausibly travel
    between within the horizon this system trades on.

    `strike_step`, when the caller knows it, sets a floor. The exchange's own
    strike interval is the market's declaration of the smallest increment worth
    quoting a level at, and a psychological level finer than the strikes cannot
    be traded around with the instruments available.

    Returns (minor, major): the chosen spacing and the next one up the ladder.
    Falls back to a magnitude-derived pair when ATR is unknown, because a
    missing input must not silently produce a confident answer.
    """
    rungs = _ladder(price)
    if not rungs:
        return (0.0, 0.0)
    rungs = [r for r in rungs if r >= strike_step] or rungs
    if atr > 0 and span_atr > 0:
        want = atr * span_atr
        minor = min(rungs, key=lambda r: abs(r - want))
    else:
        minor = min(rungs, key=lambda r: abs(r - abs(price) * 0.002))
    above = [r for r in rungs if r > minor * 1.01]
    major = above[0] if above else minor * 2.0
    return (minor, major)


def nearest_levels(price, atr=0.0, span_atr=1.0, strike_step=0.0):
    """The round numbers bracketing `price`, nearest first.

    Returns [(level, spacing, distance)] where `spacing` is the grid the level
    belongs to - the measured roundness, so a caller can weigh a 1000-multiple
    differently from a 250-multiple without either being named in code.
    """
    minor, major = grid_for(price, atr, span_atr, strike_step)
    out, seen = [], set()
    for step in (minor, major):
        if step <= 0:
            continue
        for lvl in (int(price / step) * step, (int(price / step) + 1) * step):
            lvl = float(lvl)
            if lvl in seen:
                continue
            seen.add(lvl)
            out.append((lvl, roundness(lvl, price) or step, abs(price - lvl)))
    out.sort(key=lambda t: (t[2], -t[1]))
    return out


def at_level(price, atr=0.0, tol=0.0, span_atr=1.0, strike_step=0.0):
    """The round number `price` is currently sitting on, or None.

    `tol` is absolute and the caller scales it off ATR - "at the level" means
    something different on a quiet tape than on a fast one, and this module is
    not the place that knows which it is looking at.
    """
    for lvl, spacing, dist in nearest_levels(price, atr, span_atr, strike_step):
        if dist <= tol:
            return (lvl, spacing, dist)
    return None


def headroom(price, side, atr=0.0, span_atr=1.0, strike_step=0.0):
    """How far the next round number ahead is, in ATR units.

    `side` is +1 for a long (levels above) and -1 for a short (levels below).
    Returns (level, spacing, distance, atr_units); atr_units is infinite when
    ATR is unknown, because "no data" must never read as "no obstacle".

    This is the check that stops the system buying into a wall: a 2R target
    means nothing if a level the whole market is watching sits at 0.4R.
    """
    minor, major = grid_for(price, atr, span_atr, strike_step)
    best = None
    for step in (minor, major):
        if step <= 0:
            continue
        if side > 0:
            lvl = (int(price / step) + 1) * step
            if lvl - price < step * 1e-3:
                lvl += step
            dist = lvl - price
        else:
            lvl = int(price / step) * step
            if price - lvl < step * 1e-3:
                lvl -= step
            dist = price - lvl
        if dist <= 0:
            continue
        if best is None or dist < best[2]:
            best = (float(lvl), roundness(float(lvl), price) or step, dist)
    if best is None:
        return None
    units = (best[2] / atr) if atr > 0 else float("inf")
    return (best[0], best[1], best[2], units)


def touches(candles, level, tol):
    """How many times the series has traded into `level` and left again.

    A level price has already refused twice is a different proposition from one
    it has never met. Counted as bars whose range spans the level but whose
    close is on one side of it - a bar that simply closed through is not a
    touch, it is a break.
    """
    n = 0
    for c in candles:
        _, _, h, l, cl = c[0], c[1], c[2], c[3], c[4]
        if (l - tol) <= level <= (h + tol) and abs(cl - level) > tol:
            n += 1
    return n


# ── session structure ───────────────────────────────────────────────────────

def _dt(row):
    try:
        return datetime.fromisoformat(row[0])
    except (ValueError, TypeError, AttributeError, IndexError):
        return None


def _minutes_of(row):
    d = _dt(row)
    return None if d is None else d.hour * 60 + d.minute


def by_day(candles):
    """Group rows into trading days, oldest first: [(date, [rows])].

    Rows whose timestamp cannot be parsed are dropped rather than guessed at,
    and a series with no parseable timestamps yields nothing - the caller then
    sees "no session levels" instead of levels built from an invented clock.
    """
    out, cur, key = [], [], None
    for row in candles:
        d = _dt(row)
        if d is None:
            continue
        if d.date() != key:
            if cur:
                out.append((key, cur))
            key, cur = d.date(), []
        cur.append(row)
    if cur:
        out.append((key, cur))
    return out


def opening_range(candles, minutes=30, start="09:15"):
    """High and low of the first `minutes` of the CURRENT day, or None.

    India gaps. The first half hour is therefore a real auction rather than a
    continuation of yesterday, and the range it prints is where the day's first
    brackets are placed - which is exactly what makes it a liquidity pool.

    Returns None until the window has actually closed. An opening range that is
    still forming has a high that can still move, and a pool whose level keeps
    changing cannot be swept.
    """
    days = by_day(candles)
    if not days:
        return None
    _, rows = days[-1]
    h, m = (int(x) for x in start.split(":"))
    s0 = h * 60 + m
    window = [r for r in rows if (_minutes_of(r) or 0) < s0 + minutes
              and (_minutes_of(r) or 0) >= s0]
    if not window:
        return None
    last = _minutes_of(rows[-1]) or 0
    if last < s0 + minutes:
        return None                      # still being built
    return (min(r[3] for r in window), max(r[2] for r in window))


def previous_day_range(candles):
    """(low, high) of the most recent COMPLETED day, or None.

    PDH and PDL are the levels every desk marks, which is precisely why the
    stops are there.
    """
    days = by_day(candles)
    if len(days) < 2:
        return None
    _, rows = days[-2]
    return (min(r[3] for r in rows), max(r[2] for r in rows))


def liquidity_pools(candles, minutes=30, start="09:15"):
    """The day's known pools as {name: level}, split into highs and lows.

    Returns (highs, lows), each a list of (name, level). Empty lists when there
    is not enough history, which callers must read as "no pools known" and not
    as "no pools exist".
    """
    highs, lows = [], []
    pdr = previous_day_range(candles)
    if pdr:
        lows.append(("PDL", pdr[0]))
        highs.append(("PDH", pdr[1]))
    orr = opening_range(candles, minutes=minutes, start=start)
    if orr:
        lows.append(("OR_low", orr[0]))
        highs.append(("OR_high", orr[1]))
    return highs, lows


def swept_pool(candles, within=5, minutes=30, start="09:15"):
    """Did the last few bars run a known pool and fail to hold beyond it?

    The sweep test, applied to session levels rather than swing points: a bar
    whose WICK trades through the level and whose CLOSE comes back inside it
    did not break the level, it collected the orders resting past it. Taking
    sell-side liquidity below a low and reclaiming it is bullish; the mirror is
    bearish.

    Returns (side, name, level, extreme, at) where `extreme` is the wick's
    furthest point - the price the stop belongs behind - and `at` is the bar the
    sweep completed on, which callers need to require that their confirmation
    came AFTER it. None when no pool was taken.
    """
    highs, lows = liquidity_pools(candles, minutes=minutes, start=start)
    if not highs and not lows:
        return None
    n = len(candles)
    for i in range(n - 1, max(n - 1 - within, 0) - 1, -1):
        hi, lo, cl = candles[i][2], candles[i][3], candles[i][4]
        for name, lvl in lows:
            if lo < lvl <= cl:
                return (1, name, lvl, lo, i)
        for name, lvl in highs:
            if hi > lvl >= cl:
                return (-1, name, lvl, hi, i)
    return None
