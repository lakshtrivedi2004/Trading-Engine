"""Where the stop and the target go once a trade is on - read off the chart.

Everything here is in INDEX points on the index candles, not in option premium.
Structure lives on the index: a swing low is a price on NIFTY, not on a call
whose delta changes as it moves. Option delta drifts with every point (gamma),
so a stop in premium points means a different index distance at +1R than it
did at entry. The position keeps a premium floor as well (arb/positions.py),
but the level that moves with the market is this one.

The trailing rule, as the owner drew it:

    Once the trade has made 1:1, move the stop to the LAST OPPOSITE SWING that
    formed after entry - the most recent higher low on a long, the most recent
    lower high on a short. If that swing is already beyond entry, a stop-out
    there is a profit. Never worse than breakeven-plus-costs, and it only ever
    moves in the trade's favour, following each new swing as it confirms.

    A reversal pattern after 1:1 (an opposing engulfing / rejection candle, or
    a change of character against the trade) tightens it to the nearer swing
    found with a smaller pivot strength, because that is the market saying the
    move may be done.

Modes (IntradayLimits.trail_mode): "structure" (the rule above), "atr" (a
chandelier: best price since entry minus k ATR, LeBeau), "hybrid" (whichever is
more protective), "premium" (the older R-step ratchet on the option price).

The target is not a fixed multiple either (IntradayLimits.target_mode):

    "structure"  the next liquidity level beyond entry that is at least
                 min_target_r away - a swing, PDH/PDL, the opening range, or a
                 major round number - exited slightly BEFORE the level, because
                 take-profit orders cluster at round numbers (Osler 2003) and
                 price tends to stall into them. Capped at max_target_r. With
                 no level in range it falls back to reward_risk.
    "fixed"      reward_risk x the initial risk.
    "trail"      no target: the trade ends on the trail, the time stop or the
                 square-off.

And at the target (on_target): "exit", or "extend" - if the last completed bar
is still an impulse in the trade's direction, the target moves to the next
level and the stop is locked to within extend_giveback_r of the old target.
Only momentum earns an extension; a stalling bar at the target is an exit.

Nothing here is a number chosen per instrument. Every distance is ATR, R, or a
level the market printed.
"""
from arb.candlesticks import engulfing, rejection
from arb.levels import grid_for, liquidity_pools
from arb.regime import atr, close_position
from arb.smc import BULL, _swings, analyse, resample
from arb.volume import has_volume


def _completed(candles):
    """Every bar but the last, which is the live one still being built."""
    return candles[:-1] if len(candles) > 1 else []


def _after(candles, iso):
    """Index of the first bar strictly after `iso`, or len(candles)."""
    for i, c in enumerate(candles):
        if str(c[0]) > str(iso):
            return i
    return len(candles)


def structure_stop(candles, side, since_iso, L, tight=False):
    """The last opposite swing that confirmed after entry, padded by ATR.

    Long: the most recent swing LOW after entry, minus a pad. Short: the most
    recent swing HIGH after entry, plus a pad. None until one has confirmed -
    a pivot needs `strength` bars on its right before it exists, which is the
    lag the method accepts in exchange for not trailing on noise.
    """
    done = _completed(candles)
    if len(done) < 5:
        return None
    a = atr(done, period=int(getattr(L, "atr_period", 20)))
    # Swings may be read on a folded timeframe. Intraday index returns tend to
    # REVERSE at around the 15-minute horizon (Baule, Schlie & Zhou 2025, US
    # data), so a pivot every few 1-minute bars is mostly noise; a 3-5 minute
    # fold finds the swings a chart reader would actually mark. A reversal
    # warning drops back to the base timeframe for the nearer swing.
    factor = 1 if tight else int(getattr(L, "trail_timeframe", 1) or 1)
    if factor > 1:
        done = resample(done, factor, min_fill=float(getattr(L, "htf_min_fill", 0.6)))
        if len(done) < 5:
            return None
    strength = 1 if tight else int(getattr(L, "trail_swing_strength", 2))
    highs, lows = _swings(done, max(1, strength))
    start = _after(done, since_iso)
    pad = float(getattr(L, "trail_pad_atr", 0.10)) * a
    if side == BULL:
        pts = [i for i in lows if i >= start]
        return (done[pts[-1]][3] - pad) if pts else None
    pts = [i for i in highs if i >= start]
    return (done[pts[-1]][2] + pad) if pts else None


def chandelier_stop(candles, side, since_iso, L):
    """Best price since entry, less k ATR (LeBeau's chandelier exit)."""
    done = _completed(candles)
    start = _after(done, since_iso)
    seg = done[start:]
    a = atr(done, period=int(getattr(L, "atr_period", 20)))
    if not seg or a <= 0:
        return None
    k = float(getattr(L, "chandelier_atr_mult", 3.0))
    if side == BULL:
        return max(c[2] for c in seg) - k * a
    return min(c[3] for c in seg) + k * a


def reversal_warning(candles, side, since_iso, L):
    """A pattern against the trade on the last completed bar, or ""."""
    done = _completed(candles)
    if len(done) < 3:
        return ""
    if engulfing(done) == -side:
        return "opposing_engulfing"
    if rejection(done) == -side:
        return "opposing_rejection"
    st = analyse(done, strength=int(getattr(L, "swing_strength", 2)),
                 min_bars=min(len(done), int(getattr(L, "min_structure_bars", 25))), limits=L)
    want = "CHOCH_DOWN" if side == BULL else "CHOCH_UP"
    ev = st.last(want)
    if ev is not None and ev[0] >= _after(done, since_iso):
        return "choch_against"
    return ""


def next_liquidity(candles, side, from_px, min_dist, L, strike_step=0.0):
    """Nearest level at least `min_dist` beyond `from_px` in the trade's
    direction: prior swing highs/lows, PDH/PDL, the opening range, and the
    major round number. None when nothing qualifies."""
    done = _completed(candles) or candles
    look = done[-int(getattr(L, "psych_history_bars", 180)):]
    strength = int(getattr(L, "swing_strength", 2))
    highs, lows = _swings(look, strength)
    levels = []
    if side == BULL:
        levels += [look[i][2] for i in highs]
    else:
        levels += [look[i][3] for i in lows]
    his, los = liquidity_pools(done, minutes=int(getattr(L, "opening_range_minutes", 30)),
                               start=str(getattr(L, "session_start", "09:15")))
    levels += [lv for _, lv in (his if side == BULL else los)]
    a = atr(done, period=int(getattr(L, "atr_period", 20)))
    if a > 0:
        _, major = grid_for(from_px, a, float(getattr(L, "psych_span_atr", 4.0)),
                            strike_step)
        if major and major > 0:
            if side == BULL:
                levels.append((int(from_px // major) + 1) * major)
            else:
                levels.append(int(-(-from_px // major) - 1) * major)
    if side == BULL:
        ok = [lv for lv in levels if lv - from_px >= min_dist]
        return min(ok) if ok else None
    ok = [lv for lv in levels if from_px - lv >= min_dist]
    return max(ok) if ok else None


def expected_move(candles, horizon, pct=50.0, lookback=750):
    """How far the index typically travels within `horizon` bars, in points.

    Measured, not assumed: for every bar in the lookback, the best excursion
    over the next `horizon` bars in EACH direction (highest high above the
    close, lowest low below it), pooled, and the `pct` percentile taken. At
    50 it is a move the market made in half of all windows either way - the
    owner's "easily achievable". Pooling both directions keeps a trending
    week from inflating the number for the side it happened to trend.
    """
    done = _completed(candles)[-int(lookback):]
    n = len(done)
    if n < horizon + 20:
        return 0.0
    xs = []
    for t in range(n - horizon):
        c = done[t][4]
        win = done[t + 1:t + 1 + horizon]
        xs.append(max(r[2] for r in win) - c)
        xs.append(c - min(r[3] for r in win))
    xs.sort()
    k = min(len(xs) - 1, max(0, int(round(pct / 100.0 * (len(xs) - 1)))))
    return max(0.0, xs[k])


def volume_factor(candles, L):
    """Participation now against its own recent normal, as a multiplier.

    Recent bars' traded volume (the front future, arb/volume.py) over the
    session-long average, raised to `target_volume_power` and clamped. A busy
    tape travels further than a quiet one; without volume data it is 1.0,
    never a guess.
    """
    done = _completed(candles)
    if not has_volume(done):
        return 1.0
    vols = [c[5] for c in done if len(c) > 5 and c[5] > 0]
    recent = int(getattr(L, "target_volume_recent_bars", 5))
    if len(vols) < recent + 20:
        return 1.0
    now = sum(vols[-recent:]) / recent
    base = sum(vols[-(recent + 120):-recent]) / len(vols[-(recent + 120):-recent])
    if base <= 0:
        return 1.0
    f = (now / base) ** float(getattr(L, "target_volume_power", 0.5))
    lo = float(getattr(L, "target_volume_min", 0.75))
    hi = float(getattr(L, "target_volume_max", 1.5))
    return max(lo, min(hi, f))


def momentum_target(candles, side, entry, L, strike_step=0.0):
    """Entry plus the move the market is currently making easily, or 0.0."""
    h = int(getattr(L, "target_horizon_bars", 15))
    move = expected_move(candles, h, float(getattr(L, "target_percentile", 50.0)),
                         int(getattr(L, "target_lookback_bars", 750)))
    if move <= 0:
        return 0.0
    dist = move * volume_factor(candles, L)
    tgt = entry + side * dist
    # Take profit just before a major round number in the path rather than
    # at it: that is where everybody else's take-profits sit (Osler 2003).
    a = atr(_completed(candles) or candles, period=int(getattr(L, "atr_period", 20)))
    if a > 0:
        _, major = grid_for(entry, a, float(getattr(L, "psych_span_atr", 4.0)), strike_step)
        if major > 0:
            lvl = ((int(entry // major) + 1) * major if side == BULL
                   else int(-(-entry // major) - 1) * major)
            front = float(getattr(L, "target_front_run_atr", 0.10)) * a
            if side == BULL and entry < lvl < tgt:
                tgt = lvl - front
            elif side != BULL and tgt < lvl < entry:
                tgt = lvl + front
    return tgt


def initial_target(candles, side, entry, risk, L, strike_step=0.0):
    """The index target for a new trade, or 0.0 for "no target".

    In "momentum" mode the target is NOT stretched to fit the risk: it is
    what the market is doing. Whether that pays enough for the stop is a
    separate question the engine asks (min_target_r), and a no is a skip.
    """
    mode = str(getattr(L, "target_mode", "momentum"))
    rr = float(getattr(L, "reward_risk", 2.0))
    if risk <= 0:
        return 0.0
    if mode == "trail":
        return 0.0
    fixed = entry + side * rr * risk
    if mode == "momentum":
        t = momentum_target(candles, side, entry, L, strike_step)
        if t == 0.0:
            return fixed
        hi = float(getattr(L, "max_target_r", 4.0)) * risk
        if abs(t - entry) > hi:
            t = entry + side * hi
        # target_floor_r (owner: "minimum 1:2"): never aim for less than this
        # multiple of the stop; the momentum read can only push it further.
        floor = float(getattr(L, "target_floor_r", 0.0) or 0.0) * risk
        if floor and abs(t - entry) < floor:
            t = entry + side * floor
        return t
    if mode != "structure":
        return fixed
    lo = float(getattr(L, "min_target_r", 1.5)) * risk
    hi = float(getattr(L, "max_target_r", 4.0)) * risk
    lvl = next_liquidity(candles, side, entry, lo, L, strike_step)
    if lvl is None or abs(lvl - entry) > hi:
        return fixed
    a = atr(_completed(candles) or candles, period=int(getattr(L, "atr_period", 20)))
    front = float(getattr(L, "target_front_run_atr", 0.10)) * a
    tgt = lvl - side * front
    # Front-running must not pull the target back inside the minimum.
    if abs(tgt - entry) < lo:
        tgt = entry + side * lo
    return tgt


def _impulse(candles, side, L):
    """Is the last completed bar still moving hard in the trade's direction?"""
    done = _completed(candles)
    if len(done) < 2:
        return False
    c = done[-1]
    a = atr(done, period=int(getattr(L, "atr_period", 20)))
    body = (c[4] - c[1]) * side
    cp = close_position(c)
    outer = float(getattr(L, "extend_close_pos", 0.3))
    closes_strong = (cp >= 1.0 - outer) if side == BULL else (cp <= outer)
    return a > 0 and body >= float(getattr(L, "extend_body_atr", 0.5)) * a and closes_strong


def try_extend(pos, candles, index_px, L, strike_step=0.0):
    """At the target: push it to the next level if momentum is still there.

    Returns a note, or "" when the trade should simply take the target.

    on_target = "trail" (owner, 16 Oct 2026): the target is a waypoint, not an
    exit. On reaching it the stop is locked extend_giveback_r behind it, the
    target is dropped, and the swing trail (update) takes the trade from there.
    """
    # Only an INDEX target is extended or turned into a trail. review() also
    # reports "target" for the premium target, which exists only when there is
    # no index target - and locking behind an index target of 0 put a put's
    # stop at +g*R index points (exit-path review, 19 Oct 2026).
    if pos.index_target <= 0:
        return ""
    mode = str(getattr(L, "on_target", "extend"))
    if mode == "trail" and pos.index_risk > 0:
        old = pos.index_target
        lock = old - pos.side * float(getattr(L, "extend_giveback_r", 0.75)) * pos.index_risk
        if pos.side == BULL:
            pos.index_invalidation = max(pos.index_invalidation, lock)
        else:
            pos.index_invalidation = min(pos.index_invalidation, lock) \
                if pos.index_invalidation > 0 else lock
        pos.index_target = 0.0
        pos.index_trailed = True
        return f"target {old:.1f} reached, now trailing, stop locked {pos.index_invalidation:.1f}"
    if mode != "extend":
        return ""
    if pos.extensions >= int(getattr(L, "max_target_extensions", 2)):
        return ""
    if not _impulse(candles, pos.side, L):
        return ""
    risk = pos.index_risk
    if risk <= 0:
        return ""
    step = float(getattr(L, "extend_min_r", 1.0)) * risk
    lvl = next_liquidity(candles, pos.side, index_px, step, L, strike_step)
    new_tgt = lvl if lvl is not None else index_px + pos.side * step
    old = pos.index_target
    lock = old - pos.side * float(getattr(L, "extend_giveback_r", 0.75)) * risk
    if pos.side == BULL:
        pos.index_invalidation = max(pos.index_invalidation, lock)
    else:
        pos.index_invalidation = min(pos.index_invalidation, lock) \
            if pos.index_invalidation > 0 else lock
    pos.index_target = new_tgt
    pos.extensions += 1
    pos.index_trailed = True
    return f"target extended {old:.1f} -> {new_tgt:.1f}, stop locked {pos.index_invalidation:.1f}"


def update(pos, candles, L, index_px=0.0):
    """Once per completed bar: move the index stop if the rule says so.

    Returns a short note when the stop moved, else "". The stop only ever
    tightens, and never to within `trail_min_gap_atr` of the current price -
    a trail that stops you out on the bar that set it is not a trail.
    """
    mode = str(getattr(L, "trail_mode", "structure"))
    if mode == "premium" or pos.index_risk <= 0 or pos.index_entry <= 0:
        return ""
    done = _completed(candles)
    if not done:
        return ""
    px = index_px or done[-1][4]
    side = pos.side
    r_index = (px - pos.index_entry) * side / pos.index_risk
    past_trigger = r_index >= float(getattr(L, "trail_trigger_r", 1.0))
    # trail_from_first_swing (owner's rule, 1 Oct 2026): follow the FIRST
    # higher low after entry, then the second, the third..., from the moment
    # one confirms - not only after +1R. The breakeven floor still waits for
    # trail_trigger_r: before that the stop is wherever the last swing says.
    early = bool(getattr(L, "trail_from_first_swing", False))
    armed = early or pos.index_trailed or pos.trailed or past_trigger
    if not armed:
        return ""

    since = pos.entry_bar
    cost_idx = (pos.cost_points / pos.delta) if pos.delta > 0 else 0.0
    breakeven = pos.index_entry + side * (
        cost_idx if getattr(L, "breakeven_covers_costs", True) else 0.0)
    cands = [breakeven] if (not early or past_trigger or pos.trailed) else []
    warn = ""
    if getattr(L, "tighten_on_reversal", True):
        warn = reversal_warning(candles, side, since, L)
    if mode in ("structure", "hybrid"):
        s = structure_stop(candles, side, since, L, tight=bool(warn))
        if s is not None:
            cands.append(s)
    if mode in ("atr", "hybrid"):
        c = chandelier_stop(candles, side, since, L)
        if c is not None:
            cands.append(c)

    a = atr(done, period=int(getattr(L, "atr_period", 20)))
    gap = float(getattr(L, "trail_min_gap_atr", 0.25)) * a
    if side == BULL:
        safe = [x for x in cands if x < px - gap]
        best = max(safe) if safe else None
        better = best is not None and best > pos.index_invalidation
    else:
        safe = [x for x in cands if x > px + gap]
        best = min(safe) if safe else None
        better = best is not None and (pos.index_invalidation <= 0
                                       or best < pos.index_invalidation)
    if not better:
        return ""
    old = pos.index_invalidation
    pos.index_invalidation = best
    pos.index_trailed = True
    why = "breakeven" if best == breakeven else ("tight swing" if warn else mode)
    return (f"index stop {old:.1f} -> {best:.1f} ({why}"
            + (f", {warn}" if warn else "") + f", {r_index:+.2f}R)")
