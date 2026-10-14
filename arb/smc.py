"""Smart Money Concepts on the index candle series.

Market structure, not indicators. Everything here is derived from swing points:
a swing high is a bar whose high exceeds its neighbours on both sides, a swing
low the mirror. From those four things follow, and nothing else is invented:

  BOS   break of structure - price closes beyond the last swing in the direction
        the trend was already going. Continuation.
  CHoCH change of character - price closes beyond the last swing *against* the
        trend. The first warning that the trend has ended.
  OB    order block - the last opposite-colour candle before the impulse that
        caused a BOS. The zone institutions are assumed to have filled in.
  S/R   support and resistance - swing lows and highs, clustered so twenty taps
        of the same level count once.

The bias this produces is the only thing the trader is allowed to act on, and it
is deliberately conservative: unless structure is unambiguous the bias is 0 and
no trade is taken. A range is not an opportunity.
"""
from dataclasses import dataclass, field
from datetime import datetime

BULL, BEAR, RANGE = 1, -1, 0


@dataclass
class Zone:
    """A price band - an order block or a support/resistance shelf."""
    low: float
    high: float
    kind: str                  # "bull_ob" | "bear_ob" | "support" | "resistance"
    at: int = 0                # bar index it was formed on
    touches: int = 1

    def contains(self, px, pad=0.0):
        return (self.low - pad) <= px <= (self.high + pad)

    @property
    def mid(self):
        return 0.5 * (self.low + self.high)


@dataclass
class FVG:
    """A fair value gap: three candles where the middle one moved so hard that
    the wicks either side of it never overlapped.

    The gap is the price band nobody traded on the way through. Price returning
    to it is the market filling business it skipped, which is why an untouched
    gap is a magnet and a filled one is spent history.
    """
    low: float
    high: float
    side: int                  # BULL: gap left below by an up-move
    at: int = 0
    mitigated: bool = False

    @property
    def mid(self):
        return 0.5 * (self.low + self.high)

    @property
    def size(self):
        return self.high - self.low

    def contains(self, px):
        return self.low <= px <= self.high


@dataclass
class Structure:
    """What the chart is currently saying. `bias` is the only tradable output."""
    bias: int = RANGE
    last_event: str = ""          # "BOS_UP" | "BOS_DOWN" | "CHOCH_UP" | "CHOCH_DOWN"
    event_at: int = -1
    swing_high: float = 0.0
    swing_low: float = 0.0
    zones: list = field(default_factory=list)
    closes: list = field(default_factory=list)
    reason: str = "no data"
    fvgs: list = field(default_factory=list)
    sweep: int = RANGE            # a liquidity sweep just completed, and its side
    sweep_level: float = 0.0
    range_low: float = 0.0        # the dealing range the market is working inside
    range_high: float = 0.0
    events: list = field(default_factory=list)   # [(bar_index, name, level)]
    bar_count: int = 0            # bars analysed, so "how long ago" is answerable
    event_zones: dict = field(default_factory=dict)   # event bar -> the order block it made

    def last(self, *names, after=None):
        """The most recent structural event matching any of `names`, or None.

        Returns `(bar_index, name, level)`. `after` requires the event to be
        strictly newer than that bar index, which is the whole mechanism behind
        sequence detection: ask for a CHoCH, then ask for a BOS `after` the bar
        the CHoCH landed on, and an answer to both means the market did those
        two things in that order.

        Recency is deliberately left to the caller, because only the caller
        knows how long its own series is and how stale is too stale for it.
        """
        for at, name, level in reversed(self.events):
            if names and name not in names:
                continue
            if after is not None and at <= after:
                continue
            return (at, name, level)
        return None

    def active_zone(self, px, side):
        """The order block price is currently sitting in, on the given side."""
        want = "bull_ob" if side == BULL else "bear_ob"
        for z in reversed(self.zones):
            if z.kind == want and z.contains(px, pad=self._pad()):
                return z
        return None

    def _pad(self):
        """Tolerance for 'price is in the zone': a tenth of recent range."""
        if len(self.closes) < 10:
            return 0.0
        window = self.closes[-40:]
        return 0.10 * (max(window) - min(window))

    def invalidation(self, side):
        """The level that proves this idea wrong. Long dies below the last swing
        low, short dies above the last swing high - that is what 'structure' is."""
        return self.swing_low if side == BULL else self.swing_high

    @property
    def equilibrium(self):
        """The midpoint of the dealing range. Everything below it is discount,
        everything above is premium."""
        return 0.5 * (self.range_low + self.range_high)

    def pricing(self, px):
        """Where `px` sits in the dealing range: "discount", "premium", or
        "equilibrium" for the indecisive middle.

        The oldest rule in the method and the one most often skipped: buy in
        discount, sell in premium. Buying an uptrend at the top of its own range
        is buying from the people the method says to follow.
        """
        span = self.range_high - self.range_low
        if span <= 0:
            return "equilibrium"
        frac = (px - self.range_low) / span
        if frac < 0.45:
            return "discount"
        if frac > 0.55:
            return "premium"
        return "equilibrium"

    def active_fvg(self, px, side):
        """An unfilled imbalance on this side that price is currently inside."""
        for g in reversed(self.fvgs):
            if g.side == side and not g.mitigated and g.contains(px):
                return g
        return None


def _swings(candles, strength=2):
    """Indices of swing highs and lows.

    `strength` bars must be lower (higher) on BOTH sides. Two is the smallest
    value that still means anything on a 1-minute chart; larger values find
    fewer, more meaningful pivots and lag more.
    """
    highs, lows = [], []
    n = len(candles)
    for i in range(strength, n - strength):
        h, l = candles[i][2], candles[i][3]
        left = range(i - strength, i)
        right = range(i + 1, i + strength + 1)
        if all(candles[j][2] < h for j in left) and all(candles[j][2] <= h for j in right):
            highs.append(i)
        if all(candles[j][3] > l for j in left) and all(candles[j][3] >= l for j in right):
            lows.append(i)
    return highs, lows


def _order_block(candles, impulse_at, side, max_back=12):
    """The last opposite-colour candle before the impulse that broke structure.

    Walking back from the break, the first candle that closed against the
    breakout direction is the zone price is expected to retrace into.
    """
    lo = max(0, impulse_at - max_back)
    for i in range(impulse_at, lo - 1, -1):
        o, h, l, c = candles[i][1], candles[i][2], candles[i][3], candles[i][4]
        down = c < o
        if side == BULL and down:
            return Zone(low=l, high=max(o, c), kind="bull_ob", at=i)
        if side == BEAR and not down:
            return Zone(low=min(o, c), high=h, kind="bear_ob", at=i)
    return None


def _levels(candles, highs, lows, tol_pct=0.0008):
    """Swing points clustered into S/R shelves. A level touched repeatedly is one
    level with several touches, not several levels."""
    out = []
    for idx, kind in [(highs, "resistance"), (lows, "support")]:
        pts = [(i, candles[i][2] if kind == "resistance" else candles[i][3]) for i in idx]
        for i, px in pts:
            for z in out:
                if z.kind == kind and abs(z.mid - px) <= px * tol_pct:
                    z.low, z.high = min(z.low, px), max(z.high, px)
                    z.touches += 1
                    z.at = max(z.at, i)
                    break
            else:
                out.append(Zone(low=px, high=px, kind=kind, at=i))
    return out


def analyse(candles, strength=2, min_bars=25, limits=None):
    """Read market structure off an OHLC series.

    `candles` is [[iso, o, h, l, c, v], ...] oldest first - exactly what
    CandleStore.get() returns. Two research flags on `limits` (13 Oct 2026
    review, off by default so the tested behaviour is unchanged):
      smc_consume_swings  a swing breaks once; later closes past the same
                          level do not re-fire BOS/CHoCH (nor re-label a
                          pullback candle as a fresh order block)
      smc_today_only      swings, events, order blocks and gaps from today's
                          bars only - the overnight gap is not a structure break
    """
    st = Structure()
    if not candles or len(candles) < min_bars:
        st.reason = f"only {len(candles)} bars, need {min_bars}"
        return st
    st.closes = [c[4] for c in candles]
    consume = bool(getattr(limits, "smc_consume_swings", False))
    off = 0
    if getattr(limits, "smc_today_only", False):
        day = candles[-1][0][:10]
        off = next((k for k, c in enumerate(candles) if c[0][:10] == day), 0)
        if len(candles) - off < min_bars:
            st.reason = f"only {len(candles) - off} bars today, need {min_bars}"
            return st

    highs, lows = _swings(candles, strength)
    highs, lows = [k for k in highs if k >= off], [k for k in lows if k >= off]
    if len(highs) < 2 or len(lows) < 2:
        st.reason = "not enough swing points"
        return st

    st.swing_high = candles[highs[-1]][2]
    st.swing_low = candles[lows[-1]][3]

    # Walk forward and keep the most recent structural break. A break in the
    # direction of the prevailing trend is a BOS; one against it is a CHoCH, and
    # a CHoCH flips the trend that later BOSes are measured against.
    trend = RANGE
    last_event, event_at, zones = "", -1, []
    events, used = [], set()
    hi_i, lo_i = 0, 0
    for i in range(min(highs[0], lows[0]) + 1, len(candles)):
        close = candles[i][4]
        while hi_i < len(highs) and highs[hi_i] < i - strength:
            hi_i += 1
        while lo_i < len(lows) and lows[lo_i] < i - strength:
            lo_i += 1
        prior_high = candles[highs[hi_i - 1]][2] if hi_i > 0 else None
        prior_low = candles[lows[lo_i - 1]][3] if lo_i > 0 else None

        if consume:
            if prior_high is not None and ("h", highs[hi_i - 1]) in used:
                prior_high = None
            if prior_low is not None and ("l", lows[lo_i - 1]) in used:
                prior_low = None

        if prior_high is not None and close > prior_high:
            ev = "BOS_UP" if trend in (BULL, RANGE) else "CHOCH_UP"
            if ev != last_event or i - event_at > strength:
                last_event, event_at, trend = ev, i, BULL
                events.append((i, ev, prior_high))
                used.add(("h", highs[hi_i - 1]))
                ob = _order_block(candles, i, BULL)
                if ob and ob.at >= off:
                    zones.append(ob)
                    st.event_zones[i] = ob
        elif prior_low is not None and close < prior_low:
            ev = "BOS_DOWN" if trend in (BEAR, RANGE) else "CHOCH_DOWN"
            if ev != last_event or i - event_at > strength:
                last_event, event_at, trend = ev, i, BEAR
                events.append((i, ev, prior_low))
                used.add(("l", lows[lo_i - 1]))
                ob = _order_block(candles, i, BEAR)
                if ob and ob.at >= off:
                    zones.append(ob)
                    st.event_zones[i] = ob

    st.last_event, st.event_at = last_event, event_at
    st.events = events
    st.bar_count = len(candles)
    st.zones = _levels(candles, highs, lows) + zones[-6:]
    st.fvgs = [g for g in find_fvgs(candles) if g.at - 1 >= off]
    st.sweep, st.sweep_level = liquidity_sweep(candles, strength=strength)
    st.range_low, st.range_high = dealing_range(candles)

    # A structural break only counts while it is recent. An hour-old BOS on a
    # 1-minute chart is history, not a signal.
    stale_after = 30
    if not last_event:
        st.bias, st.reason = RANGE, "no break of structure yet"
    elif len(candles) - event_at > stale_after:
        st.bias = RANGE
        st.reason = f"{last_event} was {len(candles) - event_at} bars ago, stale"
    else:
        st.bias = BULL if last_event.endswith("_UP") else BEAR
        st.reason = f"{last_event} {len(candles) - event_at} bars ago"
    return st


def find_fvgs(candles, lookback=90, min_size=0.0):
    """Three-candle imbalances in the last `lookback` bars, oldest first.

    Bullish gap: the middle candle closed up and the high of the candle BEFORE
    it sits below the low of the candle AFTER it - price jumped a band nobody
    traded. Bearish is the mirror. A gap is mitigated once a later bar trades
    back through it; a mitigated gap is not a signal, it is a filled order.
    """
    out = []
    n = len(candles)
    for i in range(max(1, n - lookback), n - 1):
        prev_h, prev_l = candles[i - 1][2], candles[i - 1][3]
        nxt_h, nxt_l = candles[i + 1][2], candles[i + 1][3]
        o, c = candles[i][1], candles[i][4]
        if c > o and prev_h < nxt_l and (nxt_l - prev_h) > min_size:
            out.append(FVG(low=prev_h, high=nxt_l, side=BULL, at=i))
        elif c < o and prev_l > nxt_h and (prev_l - nxt_h) > min_size:
            out.append(FVG(low=nxt_h, high=prev_l, side=BEAR, at=i))

    for g in out:
        for j in range(g.at + 2, n):
            if g.side == BULL and candles[j][3] <= g.low:
                g.mitigated = True
                break
            if g.side == BEAR and candles[j][2] >= g.high:
                g.mitigated = True
                break
    return out


def liquidity_sweep(candles, strength=2, within=3):
    """Did the last few bars run a pool of stops and fail to hold the ground?

    Stops cluster in obvious places - just under an old low, just over an old
    high. A bar whose WICK takes out that level and whose CLOSE comes back
    inside it did not break the level; it collected the orders resting beyond
    it. That is the single most characteristic SMC entry trigger, and it is
    direction-giving: sell-side liquidity taken below a low, then reclaimed,
    is bullish.

    Returns (side, level) with side BULL/BEAR/RANGE. Only sweeps completed in
    the last `within` bars count - a sweep from an hour ago is just a low.
    """
    n = len(candles)
    if n < strength * 2 + 4:
        return RANGE, 0.0
    highs, lows = _swings(candles, strength)
    for i in range(n - 1, max(n - 1 - within, strength) - 1, -1):
        prior_lows = [j for j in lows if j < i - strength]
        prior_highs = [j for j in highs if j < i - strength]
        low_i, high_i, close = candles[i][3], candles[i][2], candles[i][4]
        if prior_lows:
            lvl = candles[prior_lows[-1]][3]
            if low_i < lvl <= close:
                return BULL, lvl
        if prior_highs:
            lvl = candles[prior_highs[-1]][2]
            if high_i > lvl >= close:
                return BEAR, lvl
    return RANGE, 0.0


def dealing_range(candles, lookback=60):
    """The high and low the market is currently working between.

    Premium and discount mean nothing without a range to measure them against,
    and the range has to be the recent one: the day's extremes, not the week's.
    """
    window = candles[-lookback:] if len(candles) > lookback else candles
    if not window:
        return 0.0, 0.0
    return min(c[3] for c in window), max(c[2] for c in window)


def resample(candles, factor, seconds=60, min_fill=1.0):
    """Fold the series into bars `factor` times longer, oldest first.

    Higher-timeframe structure without a second API subscription: fifteen
    1-minute bars are one 15-minute bar, and the group's open/high/low/close/
    volume is exactly what the 15-minute candle would have been.

    Grouping is by WALL CLOCK, not by position in the list. That distinction is
    the whole correctness of this function. The caller passes a rolling window,
    so a position-based fold re-groups differently on every bar - 09:15-09:30
    one minute and 09:16-09:31 the next - and the higher-timeframe structure
    read off it flickers for no reason the market did. Clock buckets are stable:
    the same minute always lands in the same 15-minute bar, exactly as it does
    on the chart the rule was written for.

    Only complete buckets are emitted. A still-forming higher-timeframe bar has
    a high and low that can still move, and a day trend that changes inside a
    bar is not a day trend. The cost is up to `factor - 1` bars of lag on the
    trend read, which for a read that is meant to hold all session is nothing.

    `min_fill` relaxes "complete" to "at least this share of its minutes".
    Live bars are built from ticks and a quiet minute leaves a hole; at 1.0 a
    single hole deletes the whole higher-timeframe bar it sits in, and enough
    holes leave the day with no trend at all. The newest bucket is still never
    emitted unless it is genuinely full, because that one may still be forming.

    Falls back to position folding if the timestamps cannot be parsed, so a
    malformed feed degrades to the old behaviour rather than to no structure.
    """
    if factor <= 1:
        return list(candles)
    span = seconds * factor

    groups, order = {}, []
    for c in candles:
        try:
            key = int(datetime.fromisoformat(c[0]).timestamp()) // span
        except (ValueError, TypeError, AttributeError, IndexError):
            return _resample_by_position(candles, factor)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    need = max(1, min(factor, int(-(-factor * float(min_fill) // 1))))
    out = []
    for n, key in enumerate(order):
        grp = groups[key]
        newest = n == len(order) - 1
        if len(grp) < (factor if newest else need):
            # Incomplete: either the oldest bucket the window clipped, the one
            # still being built, or one with too many missing minutes to trust.
            continue
        out.append([grp[0][0], grp[0][1],
                    max(c[2] for c in grp), min(c[3] for c in grp),
                    grp[-1][4],
                    sum((c[5] if len(c) > 5 else 0.0) for c in grp)])
    return out


def _resample_by_position(candles, factor):
    """Positional fallback for series without parseable timestamps."""
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        grp = candles[i:i + factor]
        out.append([grp[0][0], grp[0][1],
                    max(c[2] for c in grp), min(c[3] for c in grp),
                    grp[-1][4],
                    sum((c[5] if len(c) > 5 else 0.0) for c in grp)])
    return out
