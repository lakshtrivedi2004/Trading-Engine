"""Volatility regime and session phase: the two gates the literature survives on.

Mesfin (2026) tested fourteen OHLCV signal families on 947 days of five-minute
futures data and none cleared a five-part validation bar. Eleven failed for the
same reason: their gross edge per trade, 0.07 to 1.50 points, sat below the
2.0-point round-trip friction floor. The edge was real and too small to collect.

The two signals that *did* survive in that study shared a shape our structure
read does not have: they fired only inside a classified volatility regime, and
they held for 60-75 minutes rather than predicting the next bar. The regime
classifier used four features, and three of them need only OHLC:

    ATR ratio               current 20-bar ATR over its own trailing baseline
    close position in range where the bar closed inside its own high-low
    bar return              the 5-minute return itself

The fourth was a volume z-score. An index has no volume of its own, so
arb/volume.py proxies it from the front future's traded volume, and it is used
as a confirmation (arb/confluence.py), not as a gate here. Without that series
every volume check abstains - see research/PAPERS.md section 1.

What this module provides is the part we can honestly build: a measure of
whether the tape is moving enough for a move to clear its own costs, and where
in the session we are. Neither predicts direction. Both decide whether the
direction the structure read produced is worth paying friction to act on.
"""
from arb.instruments import now_ist


def true_range(prev_close, high, low):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(candles, period=20):
    """Average true range over the last ``period`` completed bars.

    ``candles`` are [iso, o, h, l, c, v]. Returns 0.0 when there is not enough
    history, which callers must treat as "unknown", never as "calm".
    """
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(len(candles) - period, len(candles)):
        trs.append(true_range(candles[i - 1][4], candles[i][2], candles[i][3]))
    return sum(trs) / len(trs) if trs else 0.0


def atr_ratio(candles, period=20, baseline_bars=200, cap=(0.5, 2.0)):
    """Current ATR against its own trailing baseline, capped.

    The baseline is the median true range over the preceding ``baseline_bars``,
    computed on prior bars only. Mesfin fixed this constant over the whole
    sample and flagged it as mild look-ahead; a trailing median costs nothing
    and removes that objection.

    Returns 0.0 when history is too short to judge.
    """
    if len(candles) < period + 1:
        return 0.0
    recent = atr(candles, period)
    if recent <= 0:
        return 0.0
    window = candles[-(baseline_bars + 1):]
    if len(window) < period + 2:
        return 0.0
    trs = sorted(true_range(window[i - 1][4], window[i][2], window[i][3])
                 for i in range(1, len(window)))
    mid = len(trs) // 2
    base = trs[mid] if len(trs) % 2 else 0.5 * (trs[mid - 1] + trs[mid])
    if base <= 0:
        return 0.0
    return max(cap[0], min(cap[1], recent / base))


def close_position(candle):
    """Where the bar closed inside its own range: 0.0 at the low, 1.0 at the high.

    A bar that closes at its extreme is an impulse; one that closes mid-range is
    indecision wearing the same colour. 0.5 is returned for a zero-range bar.
    """
    _, _, high, low, close = candle[0], candle[1], candle[2], candle[3], candle[4]
    rng = high - low
    if rng <= 0:
        return 0.5
    return (close - low) / rng


def efficiency(closes):
    """Kaufman's efficiency ratio: net move over path length. 1 is a straight
    line, 0 is price going nowhere however much it wiggles - a sideways tape."""
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path > 0 else 0.0


def premium_gate(closes, cost_points, L):
    """Why the OPTION's own chart refuses this trade, or "".

    Our P&L is the premium, not the index. Theta and IV can hold a premium flat
    while the index moves, so the premium bought must itself have risen by more
    than the round trip over the last `premium_rise_bars` (research/PAPERS.md 0e).
    """
    n = int(getattr(L, "premium_rise_bars", 0) or 0)
    if n <= 0:
        return ""
    if len(closes) < n + 1:
        return "premium_chart_warming_up"
    return "" if closes[-1] - closes[-1 - n] > cost_points else "premium_not_rising"


def _minutes(hhmm):
    hh, mm = (int(x) for x in hhmm.split(":"))
    return hh * 60 + mm


def session_phase(start="09:15", end="15:30", now=None):
    """Which third of the session we are in, by the clock.

    Gao, Han, Li and Zhou (JFE 2018) find the first half-hour return predicts the
    last half-hour return, and that the effect concentrates on volatile, high
    volume days. Heston, Korajczyk and Sadka (JF 2010) find intraday return
    continuation is strongest in the first and last half-hour periods, and that
    what happens in between is largely temporary liquidity imbalance and
    bid-ask bounce - the part a scalper pays for and does not collect.

    Returns one of "open", "midday", "close", or "outside".
    """
    n = now or now_ist()
    mins = n.hour * 60 + n.minute
    s, e = _minutes(start), _minutes(end)
    if mins < s or mins > e:
        return "outside"
    if mins < s + 60:
        return "open"
    if mins >= e - 60:
        return "close"
    return "midday"


def regime_gate(candles, L, now=None, name=None):
    """The reason to refuse this bar, or "" if the tape is worth trading.

    Refusals are deliberately named after what failed so the dashboard's reject
    counter stays readable. `name` picks a per-index pace from L.atr_by_index
    (first half, second half of the day), which replaces min_atr_ratio and the
    midday rule for that index.
    """
    halves = (getattr(L, "atr_by_index", None) or {}).get(name) if name else None
    need = float(L.min_atr_ratio)
    if halves:
        t = now or now_ist()
        second = t.hour * 60 + t.minute >= _minutes(str(getattr(L, "atr_half_split", "12:15")))
        need = float(halves[1 if second else 0])
    # Never trade a sideways market: the index must have travelled at least as
    # efficiently as a random walk would (expected ratio 1/sqrt(n)), so the bar
    # is derived from the window, not typed in. Its own switch (sideways_bars =
    # 0), not use_regime_gate, so it cannot go by accident.
    n = int(getattr(L, "sideways_bars", 0) or 0)
    if n > 0 and len(candles) > n + 1:
        if efficiency([r[4] for r in candles[-(n + 2):-1]]) * n ** 0.5 < L.min_trend_efficiency:
            return "index_sideways"
    if not getattr(L, "use_regime_gate", True):
        return ""

    # "No data" and "quiet tape" are different facts and must not share a
    # counter. A feed that has died looks exactly like a calm market to every
    # downstream check, and a reject reason that cannot tell them apart will
    # report a broken session as a well-behaved one all day.
    if not candles:
        return "no_candles"

    ar = 0.0
    if need > 0:
        if len(candles) < L.atr_period + 1:
            return "candles_warming_up"
        ar = atr_ratio(candles, period=L.atr_period,
                       baseline_bars=L.atr_baseline_bars)
        if ar <= 0:
            # Not enough history to know the tape's own normal. That is a fact
            # about our data, not about the market, and it is only a refusal
            # when the operator asked for one.
            if not getattr(L, "trade_unknown_regime", True):
                return "regime_unknown"
        elif ar < need:
            return "regime_too_quiet"

    mode = str(getattr(L, "midday_mode", "strict"))
    if getattr(L, "skip_midday", False):
        mode = "block"
    if not halves and mode != "off" and session_phase(now=now) == "midday":
        if mode == "block":
            return "midday_chop"
        # "strict": the middle of the day is tradable on a day that is actually
        # moving - that is when the continuation effect survives lunch.
        need = float(getattr(L, "midday_min_atr_ratio", 1.0))
        if ar < need:
            return "midday_chop"

    if L.min_close_position > 0 and len(candles) >= 2:
        # The last COMPLETED bar. The final row is usually the live bar, whose
        # close moves with every tick and says nothing yet.
        cp = close_position(candles[-2])
        # Symmetric: an impulse in either direction closes near an extreme.
        if min(cp, 1.0 - cp) > (0.5 - L.min_close_position):
            return "bar_indecisive"

    return ""


def trend_4h(candles, bars=5, split="13:15"):
    """+1 / -1: the direction of the last `bars` completed 4-hour bars of the
    1-minute series (each session cut at `split`), 0 with too little history.
    The bucket still forming is left out. research/PAPERS.md 0k: 3-6 bars all
    helped out of sample; daily 3-5 day trends did not (short-term reversal)."""
    closes, key = [], None
    for r in candles:
        k = (r[0][:10], r[0][11:16] >= split)
        if k != key:
            closes.append(r[4]); key = k
        else:
            closes[-1] = r[4]
    closes = closes[:-1]
    if len(closes) <= bars:
        return 0
    d = closes[-1] - closes[-1 - bars]
    return (d > 0) - (d < 0)


def run_in_direction(candles, side, bars=30, period=20):
    """How far price has already travelled in `side`'s direction, in ATRs.

    For a long: the last close minus the lowest low of the last `bars` bars.
    For a short: the highest high of those bars minus the last close. The ATR is
    the one before the window's last bar, as the entry code uses. 0.0 without
    enough history. `candles` must be completed bars (owner's 1 Oct review: a
    put bought after a 12-ATR fall, at the bottom, is the move already spent).
    """
    if len(candles) < max(bars, period + 2):
        return 0.0
    a = atr(candles[:-1], period=period)
    if a <= 0:
        return 0.0
    win = candles[-bars:]
    c = candles[-1][4]
    if side > 0:
        return (c - min(r[3] for r in win)) / a
    return (max(r[2] for r in win) - c) / a


def swept_against(candles, side, lookback=10, pool_bars=30, reclaim_bars=3):
    """Did the market recently run the stops on the OTHER side and reclaim?

    A sell-side sweep (a low below the prior `pool_bars` lows, closed back above
    that pool within `reclaim_bars`) is the manipulation leg of a move UP, so a
    put bought after it buys the trap. Mirror for calls. Looks at sweeps whose
    extreme bar is within the last `lookback` completed bars, and only while
    price is still on the reclaimed side. Returns "" or a short reason.
    """
    n = len(candles)
    if n < pool_bars + lookback + 1:
        return ""
    last = candles[-1][4]
    for k in range(n - lookback, n):
        pool = candles[k - pool_bars:k]
        if side < 0:                      # a put fears a sell-side sweep
            lvl = min(r[3] for r in pool)
            if candles[k][3] < lvl and last > lvl and any(
                    r[4] > lvl for r in candles[k:min(n, k + 1 + reclaim_bars)]):
                return "sellside_swept"
        else:                             # a call fears a buy-side sweep
            lvl = max(r[2] for r in pool)
            if candles[k][2] > lvl and last < lvl and any(
                    r[4] < lvl for r in candles[k:min(n, k + 1 + reclaim_bars)]):
                return "buyside_swept"
    return ""
