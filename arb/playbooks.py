"""Named setups: a SEQUENCE of things happening in order, not a pile of facts.

Why this module exists
----------------------
arb/confluence.py counts independent confirmations. That is a real filter and it
is not the same thing as a setup, because counting is order-blind: an order
block, a volume spike and a VWAP side all being true at once scores three and
describes nothing in particular. A trader does not think that way. A trader
thinks "the trend changed, THEN it broke structure in the new direction, AND it
did it into a level I already cared about" - and the word doing the work in that
sentence is THEN.

So this module adds the missing axis. Each playbook is an ordered chain of
events that must have happened in sequence, recently, and in one direction. A
playbook that fires is one setup, however many steps it contains; two playbooks
firing together is genuine agreement between two different models.

The imports, and what survived the trip
---------------------------------------
Four forex/crypto strategies were folded in here. They were written for markets
this one is not, so each was taken apart and only the mechanism kept:

  Ethereum (Asia-session BOS)   The session window is the entire discipline
        layer in a 24/7 market, and NSE already has one imposed on it by the
        exchange. What transferred is the bias-then-BOS entry: a break of
        structure is only tradable in the direction the higher timeframe was
        already going. That is `choch_bos` and the bias half of every other
        playbook.

  London (Asia sweep -> London BOS)   The strongest of the four, and the one
        that needed the most translation. Its engine is "an obvious range gets
        its stops run, then price breaks structure the other way". NSE has no
        Asia/London handover, but it does have a gapping open and a previous
        day everybody marks, so the pools become PDH/PDL and the opening range
        (arb/levels.py). That is `sweep_bos`.

  New York (multi-pool liquidity sweep)   Same mechanism as London with four
        pools instead of two and an HTF bias filter on top. Folded into
        `sweep_bos` rather than kept separate - two copies of one idea is one
        idea with twice the overfitting surface.

  Fibonacci (NY golden-pocket pullback)   Instrument-agnostic and the cleanest
        to port: impulse leg, retrace into 0.5-0.618, confirm with a candle or
        a micro-break. That is `fib_pullback`.

What did NOT survive, and why
-----------------------------
  The 4H bias. All four scripts read trend on 4-hour candles. The NSE session is
  six hours fifteen minutes long, so a 4H bar is most of a trading day and there
  are roughly one and a half of them per session. A "trend" measured that way
  updates once a day and is a swing-trading input wearing an intraday label. The
  bias here stays on the 15-minute fold the system already computes
  (arb/confluence.py:day_trend), which on a 1-minute series is the same
  bias-to-entry timeframe ratio the forex scripts were actually getting.

  The session clocks. Asia 00:00-08:00 UTC and London 08:00-11:00 are windows
  where specific liquidity centres hand over to each other. Copied onto an
  Indian index they would just be arbitrary times of day. arb/regime.py already
  carries the NSE-specific version of this idea, sourced from work done on
  actual intraday equity data (Gao et al. 2018, Heston et al. 2010).

  The 2R-and-breakeven management. Not dropped, already present - see
  IntradayLimits.reward_risk and the trailing logic in arb/positions.py. It did
  not need importing twice.

An honest note on the survivability question
--------------------------------------------
These models were built on spot FX and gold, which are deep, nearly
24-hour, and cost a fraction of a basis point to trade. This system buys weekly
index options on a six-hour session, pays a round trip measured in premium
points, and holds an instrument that decays while the thesis plays out. The
structural mechanisms port cleanly because they are statements about where stops
rest, and stops rest in the same places everywhere. The ECONOMICS do not port:
a setup that is worth 0.8R gross on XAUUSD can be worth less than zero here
after the spread and the theta. That is why every playbook below reports its
setup and nothing else, and the decision to pay for it stays with
arb/intraday.py:plan_trade, which knows the costs. No playbook is allowed to
imply an edge it has not been charged for.
"""
from dataclasses import dataclass, field

from arb.candlesticks import engulfing, rejection, momentum_bar
from arb.levels import (at_level, by_day, grid_for, headroom, opening_range,
                        swept_pool, touches, _minutes_of)
from arb.regime import atr
from arb.volume import has_volume, vwap
from arb.smc import BULL, BEAR, RANGE, _swings


@dataclass
class Setup:
    """One named, ordered setup that has actually completed on the chart."""
    name: str
    side: int
    steps: list = field(default_factory=list)     # [(step_name, detail)] in order
    invalidation: float = 0.0                      # index level where it dies
    zone: tuple = None                             # (low, high) entry zone
    note: str = ""

    def describe(self):
        return f"{self.name}: " + " -> ".join(s for s, _ in self.steps)

    @property
    def depth(self):
        """How many ordered steps had to line up for this to exist."""
        return len(self.steps)


def _p(candles, i):
    c = candles[i]
    return c[1], c[2], c[3], c[4]          # o, h, l, c


def _recent(st, at, bars):
    """Did event at bar `at` happen within `bars` of the end of the series?"""
    return st.bar_count > 0 and (st.bar_count - at) <= bars


# ── 1. change of character, then break of structure ─────────────────────────

def choch_bos(candles, st, spot, trend, L, strike_step=0.0):
    """CHoCH first, then a BOS confirming the new direction.

    The most conservative trend-change entry the method offers. A CHoCH alone is one bar's worth of
    evidence that the old trend is done - acting on it is catching a falling
    knife and calling it structure. Waiting for the next BOS in the new
    direction costs part of the move and buys the only confirmation available:
    the market did it twice.

    The BOS must come AFTER the CHoCH, not merely exist alongside it. That
    ordering is the whole setup, and it is why arb/smc.py keeps an event log
    rather than only the latest event.
    """
    window = int(getattr(L, "sequence_window_bars", 40))
    ch = st.last("CHOCH_UP", "CHOCH_DOWN")
    if ch is None:
        return None
    at, name, level = ch
    side = BULL if name.endswith("_UP") else BEAR
    if side != trend:
        return None
    bos = st.last("BOS_UP" if side == BULL else "BOS_DOWN", after=at)
    if bos is None:
        return None
    bos_at, bos_name, bos_level = bos
    if not _recent(st, bos_at, window):
        return None
    # The entry is the moment the sequence COMPLETES. Forty bars after the BOS
    # the setup is still "true" but price is somewhere else and the stop is a
    # leg away; re-firing it every bar made it most of the signals by itself.
    fresh = int(getattr(L, "trigger_fresh_bars", 5) or 0)
    if fresh and not _recent(st, bos_at, fresh):
        return None
    # Displacement: the bar that broke structure must have done it with a real
    # body, not a one-tick close past the level. On 1-minute bars a swing is
    # taken out every few minutes by noise alone; a strong-bodied break is the
    # SMC definition of a break that means something.
    need = float(getattr(L, "displacement_body_atr", 0.8) or 0.0)
    if need > 0 and 0 <= bos_at < len(candles):
        o, _, _, c = _p(candles, bos_at)
        a = atr(candles, period=int(getattr(L, "atr_period", 20)))
        if a > 0 and abs(c - o) < need * a:
            return None

    return Setup(
        name="choch_bos",
        side=side,
        steps=[("change_of_character", f"{name} at {level:.1f}"),
               ("break_of_structure",
                f"{bos_name} at {bos_level:.1f}, {st.bar_count - bos_at} bars ago")],
        invalidation=st.swing_low if side == BULL else st.swing_high,
        note="trend flipped, then confirmed itself")


# ── 2. liquidity sweep, then break of structure ─────────────────────────────

def sweep_bos(candles, st, spot, trend, L, strike_step=0.0):
    """A pool of stops taken, then structure broken the other way.

    The London and New York models, merged. Step one is a sweep: price wicks
    through a level everyone can see - the previous day's high or low, or the
    opening range - and CLOSES back inside. That close is what separates a
    sweep from a breakout, and the distinction is the entire model. A close
    beyond the level means the level went; a close back inside means the orders
    resting beyond it were collected and price did not want to stay there.

    Step two is confirmation in the reclaim direction: a break of structure, or
    an engulfing candle. Without it this is just a long wick, and long wicks
    happen constantly.

    The stop goes behind the sweep wick, because that price is the one the
    model says should not trade again.
    """
    within = int(getattr(L, "sweep_lookback_bars", 8))
    window = int(getattr(L, "sequence_window_bars", 40))

    hit = swept_pool(candles, within=within,
                     minutes=int(getattr(L, "opening_range_minutes", 30)),
                     start=str(getattr(L, "session_start", "09:15")))
    src = "session"
    if hit is None:
        # Fall back to the swing-point sweep arb/smc.py already computes, so a
        # day without usable session levels can still see the pattern. That one
        # carries no bar index, so ordering cannot be enforced against it and
        # only the candle confirmation is accepted.
        if st.sweep == RANGE:
            return None
        side, name, level = st.sweep, "swing", st.sweep_level
        extreme, swept_at = level, None
        src = "swing"
    else:
        side, name, level, extreme, swept_at = hit

    if side != trend:
        return None

    # The confirmation has to come AFTER the sweep. A break of structure that
    # happened before the pool was taken is not this model - it is an unrelated
    # earlier event that would otherwise be credited to a sweep it predates.
    want = ("BOS_UP", "CHOCH_UP") if side == BULL else ("BOS_DOWN", "CHOCH_DOWN")
    bos = st.last(*want, after=swept_at) if swept_at is not None else None
    confirmed, detail = None, ""
    if bos is not None and _recent(st, bos[0], window):
        confirmed, detail = "break_of_structure", f"{bos[1]} at {bos[2]:.1f}"
    elif engulfing(candles) == side:
        confirmed, detail = "engulfing", "engulfing candle on the reclaim"
    if confirmed is None:
        return None

    pad = float(getattr(L, "sweep_stop_pad_atr", 0.05)) * \
        atr(candles, period=int(getattr(L, "atr_period", 20)))
    return Setup(
        name="sweep_bos",
        side=side,
        steps=[("liquidity_sweep", f"{name} {level:.1f} taken and reclaimed ({src})"),
               (confirmed, detail)],
        invalidation=extreme - pad if side == BULL else extreme + pad,
        note="stops run, then structure agreed")


# ── 3. fibonacci golden-pocket pullback ─────────────────────────────────────

def fib_pullback(candles, st, spot, trend, L, strike_step=0.0):
    """Impulse leg, retrace into 0.5-0.618, then a reaction.

    The New York fib model, ported whole because it never depended on a session
    in the first place. Three ordered steps: a leg large enough to be an
    impulse rather than noise, a pullback that reaches the golden pocket, and a
    candle that says the pocket held.

    The leg size floor is in ATR units and it matters more here than it did in
    the original: a 12-point NIFTY "impulse" retraced to its 0.618 gives an
    entry-to-stop distance smaller than the option spread, and a stop inside
    the spread is not a stop.
    """
    strength = int(getattr(L, "swing_strength", 2))
    min_leg = float(getattr(L, "fib_min_leg_atr", 1.5))
    lo_f = float(getattr(L, "fib_zone_start", 0.50))
    hi_f = float(getattr(L, "fib_zone_end", 0.618))
    stop_f = float(getattr(L, "fib_stop", 0.79))

    a = atr(candles, period=int(getattr(L, "atr_period", 20)))
    if a <= 0:
        return None
    highs, lows = _swings(candles, strength)
    if not highs or not lows:
        return None

    if trend == BULL:
        hi_i = highs[-1]
        prior = [j for j in lows if j < hi_i]
        if not prior:
            return None
        lo_i = prior[-1]
        leg = candles[hi_i][2] - candles[lo_i][3]
        if leg < min_leg * a:
            return None
        zone_hi = candles[hi_i][2] - lo_f * leg      # shallower edge (0.50)
        zone_lo = candles[hi_i][2] - hi_f * leg      # deeper edge (0.618)
        inside = zone_lo <= spot <= zone_hi
        stop = candles[hi_i][2] - stop_f * leg
    elif trend == BEAR:
        lo_i = lows[-1]
        prior = [j for j in highs if j < lo_i]
        if not prior:
            return None
        hi_i = prior[-1]
        leg = candles[hi_i][2] - candles[lo_i][3]
        if leg < min_leg * a:
            return None
        zone_lo = candles[lo_i][3] + lo_f * leg
        zone_hi = candles[lo_i][3] + hi_f * leg
        inside = zone_lo <= spot <= zone_hi
        stop = candles[lo_i][3] + stop_f * leg
    else:
        return None

    if not inside:
        return None

    react = None
    if engulfing(candles) == trend:
        react = "engulfing"
    elif rejection(candles) == trend:
        react = "rejection_wick"
    elif momentum_bar(candles) == trend:
        react = "momentum_bar"
    if react is None:
        return None

    return Setup(
        name="fib_pullback",
        side=trend,
        steps=[("impulse_leg", f"{leg:.1f} pts = {leg / a:.1f} ATR"),
               ("golden_pocket", f"price {spot:.1f} in {zone_lo:.1f}-{zone_hi:.1f}"),
               (react, "reaction inside the pocket")],
        invalidation=stop,
        zone=(zone_lo, zone_hi),
        note="pullback into 0.5-0.618 held")


# ── 4. reaction at a psychological level ────────────────────────────────────

def psych_reaction(candles, st, spot, trend, L, strike_step=0.0):
    """Price meets a round number it has already respected, and refuses it.

    Not a standalone reason to trade and not used as one. This fires only when
    price is AT a level, that level has turned price away before, and the
    current bar rejects it in the direction the higher timeframe already wants.
    Three ordered facts, the last of which is the trigger.

    Osler (2003) is the evidence for why the level matters at all: take-profit
    orders cluster at round numbers, which is what makes price stall into one.
    The same paper is the reason this is a confirmation and never a signal -
    stops cluster just BEYOND the level, so the identical setup precedes an
    acceleration through it about as often as a turn away from it.
    """
    a = atr(candles, period=int(getattr(L, "atr_period", 20)))
    if a <= 0:
        return None
    tol = float(getattr(L, "psych_tolerance_atr", 0.35)) * a
    span = float(getattr(L, "psych_span_atr", 4.0))
    hit = at_level(spot, atr=a, tol=tol, span_atr=span, strike_step=strike_step)
    if hit is None:
        return None
    lvl, spacing, dist = hit

    seen = touches(candles[-int(getattr(L, "psych_history_bars", 180)):], lvl, tol)
    if seen < int(getattr(L, "psych_min_touches", 2)):
        return None

    react = None
    if engulfing(candles) == trend:
        react = "engulfing"
    elif rejection(candles) == trend:
        react = "rejection_wick"
    if react is None:
        return None

    # The level has to be behind the trade, not in front of it. Rejecting 24500
    # from below and then buying is buying straight back into it.
    if (trend == BULL and spot < lvl) or (trend == BEAR and spot > lvl):
        return None

    return Setup(
        name="psych_reaction",
        side=trend,
        steps=[("psych_level",
                f"level {lvl:.0f} (multiple of {spacing:g}), {dist:.1f} pts away"),
               ("prior_respect", f"turned price away {seen}x"),
               (react, "rejected in the trend direction")],
        invalidation=lvl - tol if trend == BULL else lvl + tol,
        note="round number reclaimed")


# ── 5. break of structure, then retest of what caused it ────────────────────

def ob_fvg_retest(candles, st, spot, trend, L, strike_step=0.0):
    """A BOS, then price comes back into the order block or gap that made it.

    The core SMC continuation entry and the sequenced version of what
    arb/confluence.py previously scored flat. The order block only means
    anything BECAUSE a break of structure followed it; scoring "price is in a
    zone" without requiring the break first counts a zone that never proved
    itself.

    Entering the retest rather than the breakout is the whole reason the
    reward-to-risk works: the stop sits under the zone instead of a full leg
    away at the swing.
    """
    window = int(getattr(L, "sequence_window_bars", 40))
    bos = st.last("BOS_UP" if trend == BULL else "BOS_DOWN",
                  "CHOCH_UP" if trend == BULL else "CHOCH_DOWN")
    if bos is None or not _recent(st, bos[0], window):
        return None

    zone = st.active_zone(spot, trend)
    gap = st.active_fvg(spot, trend)
    if zone is None and gap is None:
        return None

    where = zone if zone is not None else gap
    kind = "order_block" if zone is not None else "fair_value_gap"

    react = None
    if engulfing(candles) == trend:
        react = "engulfing"
    elif rejection(candles) == trend:
        react = "rejection_wick"
    if react is None:
        return None

    pad = float(getattr(L, "zone_stop_pad_atr", 0.10)) * \
        atr(candles, period=int(getattr(L, "atr_period", 20)))
    return Setup(
        name="ob_fvg_retest",
        side=trend,
        steps=[("break_of_structure", f"{bos[1]} at {bos[2]:.1f}"),
               (kind, f"{where.low:.1f}-{where.high:.1f} retested"),
               (react, "defended on the retest")],
        invalidation=where.low - pad if trend == BULL else where.high + pad,
        zone=(where.low, where.high),
        note="break, then the zone that caused it held")


# ── 6. initial-balance breakout (Umar Punjabi) ──────────────────────────────

def ib_breakout(candles, st, spot, trend, L, strike_step=0.0):
    """The first 30 minutes set the day's balance; trade the side that breaks.

    Ported from the one setup Umar Punjabi describes in checkable terms
    (Raj Shamani podcast FO414): "first 30 minutes candle is called initial
    balance... if the high break of 30 minutes candle is there, I will buy, if
    the low break is there, I will sell", with the stop at the point of no
    return on the other side of the opening structure, fixed-rupee risk and
    roughly 1:2 to 1:3 reward. He trades it on Nasdaq and gold; NSE has the
    same opening auction, which is what the setup actually leans on.

    Ordered steps: the balance completes, a bar CLOSES beyond it (a wick
    through is a sweep and belongs to sweep_bos), and price is still holding
    beyond it now. Either the break itself is fresh, or price has pulled back
    to the broken edge and been defended - the retest variant.

    What was added, because a bot cannot use discretion:
      * the break must be recent (`ib_entry_window_bars`) - a break an hour ago
        is a trend already under way, not this setup;
      * price may not have run more than `ib_max_extension_atr` beyond the edge,
        so the stop is not a full leg away;
      * only the FIRST break per side counts (`ib_first_break_only`); a third
        poke through the same edge is chop around a level.
    Evidence caveat: Mesfin (2026) found opening-range breakout gross-positive
    but unprovable after costs on MNQ. That is why the cost floor in
    plan_trade still applies in full, and why the result must be measured.
    """
    minutes = int(getattr(L, "ib_minutes", 0) or getattr(L, "opening_range_minutes", 30))
    start = str(getattr(L, "session_start", "09:15"))
    ib = opening_range(candles, minutes=minutes, start=start)
    if ib is None or trend == RANGE:
        return None
    lo, hi = ib
    if hi <= lo:
        return None

    last_entry = str(getattr(L, "ib_last_entry", "13:00") or "")
    now_m = _minutes_of(candles[-1])
    if last_entry and now_m is not None:
        hh, mm = (int(x) for x in last_entry.split(":"))
        if now_m > hh * 60 + mm:
            return None

    a = atr(candles, period=int(getattr(L, "atr_period", 20)))
    if a <= 0:
        return None
    days = by_day(candles)
    if not days:
        return None
    today = days[-1][1]
    offset = len(candles) - len(today)
    h0, m0 = (int(x) for x in start.split(":"))
    ib_end = h0 * 60 + m0 + minutes

    edge = hi if trend == BULL else lo
    beyond = (lambda c: c[4] > hi) if trend == BULL else (lambda c: c[4] < lo)

    # Completed bars only: the final row is the live bar.
    done = today[:-1] if len(today) > 1 else []
    breaks = [i for i, c in enumerate(done)
              if (_minutes_of(c) or 0) >= ib_end and beyond(c)]
    if not breaks:
        return None
    first = breaks[0]
    if bool(getattr(L, "ib_first_break_only", True)):
        # Every close beyond the edge since the first break must be part of one
        # continuous excursion. A return inside the balance and a second break
        # is a new, weaker event.
        run = [i for i in range(first, len(done)) if not beyond(done[i])]
        if run and any(beyond(done[j]) for j in range(run[0], len(done))):
            return None

    window = int(getattr(L, "ib_entry_window_bars", 10))
    age = len(done) - first
    # Still holding beyond the edge now (the live price, not just the close).
    holding = spot > hi if trend == BULL else spot < lo
    if not holding:
        return None
    ext = (spot - hi) if trend == BULL else (lo - spot)
    tol = float(getattr(L, "ib_retest_tol_atr", 0.3)) * a

    step = None
    zone = None
    if age <= window and ext <= float(getattr(L, "ib_max_extension_atr", 1.5)) * a:
        step = ("break", f"closed beyond {edge:.1f} {age} bars ago")
    elif ext <= tol:
        # Retest: price came back to the broken edge and is still on the right
        # side of it. A reaction candle is what separates "defended" from
        # "about to fall back in".
        react = None
        if engulfing(candles) == trend:
            react = "engulfing"
        elif rejection(candles) == trend:
            react = "rejection_wick"
        elif momentum_bar(candles) == trend:
            react = "momentum_bar"
        if react is None:
            return None
        step = ("retest", f"{react} on the retest of {edge:.1f}")
        zone = (edge - tol, edge) if trend == BULL else (edge, edge + tol)
    if step is None:
        return None

    mode = str(getattr(L, "ib_stop", "mid"))
    mid = 0.5 * (lo + hi)
    pad = float(getattr(L, "zone_stop_pad_atr", 0.10)) * a
    if mode == "far":
        stop = lo - pad if trend == BULL else hi + pad
    elif mode == "edge":
        stop = hi - tol - pad if trend == BULL else lo + tol + pad
    else:
        stop = mid

    return Setup(
        name="ib_breakout",
        side=trend,
        steps=[("initial_balance", f"{minutes}m balance {lo:.1f}-{hi:.1f}"),
               step],
        invalidation=stop,
        zone=zone,
        note="initial balance broken and held")


# ── 7. pullback to session VWAP ─────────────────────────────────────────────

def vwap_pullback(candles, st, spot, trend, L, strike_step=0.0):
    """Trend side of VWAP, a pullback that tags it, and a candle that leaves it.

    Zarattini & Aziz (SSRN 4631351) trade QQQ long above session VWAP and short
    below it; the rule is crude and it is one of the few intraday trend rules
    with published numbers (Sharpe ~2.1 in 2018-2023, before our costs - not
    verified against NSE). Buying every bar above VWAP would pay the round trip
    constantly, so this takes only the PULLBACK: price is on the trend side,
    one of the last few bars reached back to VWAP without closing through it,
    and the current bar reacts away. The stop sits just the other side of VWAP,
    which is where "the day's buyers are winning" stops being true.

    Abstains without real traded volume. An index has none of its own; the
    engine proxies it from the front future (arb/volume.py), and without that
    proxy this is a moving average with a misleading name.
    """
    if trend == RANGE or not has_volume(candles):
        return None
    days = by_day(candles)
    if not days or len(days[-1][1]) < int(getattr(L, "vwap_min_session_bars", 15)):
        return None
    w = vwap(candles)
    a = atr(candles, period=int(getattr(L, "atr_period", 20)))
    if not w or a <= 0:
        return None
    tol = float(getattr(L, "vwap_touch_atr", 0.25)) * a
    look = int(getattr(L, "vwap_touch_bars", 5))
    done = candles[:-1][-look:]
    if not done:
        return None

    if trend == BULL:
        if spot <= w:
            return None
        touched = any(c[3] <= w + tol for c in done)
        held = all(c[4] >= w - tol for c in done)
    else:
        if spot >= w:
            return None
        touched = any(c[2] >= w - tol for c in done)
        held = all(c[4] <= w + tol for c in done)
    if not (touched and held):
        return None

    react = None
    if engulfing(candles) == trend:
        react = "engulfing"
    elif rejection(candles) == trend:
        react = "rejection_wick"
    elif momentum_bar(candles) == trend:
        react = "momentum_bar"
    if react is None:
        return None

    pad = float(getattr(L, "zone_stop_pad_atr", 0.10)) * a
    return Setup(
        name="vwap_pullback",
        side=trend,
        steps=[("vwap_side", f"{'above' if trend == BULL else 'below'} VWAP {w:.1f}"),
               ("pullback", f"tagged VWAP within {tol:.1f} pts"),
               (react, "left VWAP in the trend direction")],
        invalidation=(w - tol - pad) if trend == BULL else (w + tol + pad),
        zone=(w - tol, w + tol),
        note="pullback to session VWAP held")


# The registry. Adding a playbook means writing a function with this signature
# and naming it here; nothing else in the system needs to change. Which of them
# are live is a configuration decision, not a code one - see `enabled()`.
PLAYBOOKS = {
    "choch_bos": choch_bos,
    "sweep_bos": sweep_bos,
    "fib_pullback": fib_pullback,
    "psych_reaction": psych_reaction,
    "ob_fvg_retest": ob_fvg_retest,
    "ib_breakout": ib_breakout,
    "vwap_pullback": vwap_pullback,
}


def enabled(L):
    """The playbooks this configuration wants, in registry order.

    `L.playbooks` may name a subset; empty or absent means all of them. An
    unknown name is ignored rather than raising, so a stale config file cannot
    stop the engine from starting - but it is returned by `unknown()` so the
    caller can say so out loud instead of silently trading a smaller set than
    the operator thinks they configured.
    """
    want = list(getattr(L, "playbooks", None) or [])
    if not want:
        return list(PLAYBOOKS.values())
    return [PLAYBOOKS[n] for n in PLAYBOOKS if n in set(want)]


def unknown(L):
    """Names in the config that match no registered playbook."""
    return sorted(set(getattr(L, "playbooks", None) or []) - set(PLAYBOOKS))


def detect(candles, st, spot, trend, L, strike_step=0.0, only=None):
    """Every playbook that has completed, in the trend direction, deepest first.

    `trend` is the higher-timeframe bias and is not negotiable - a playbook
    pointing the other way is not reported at all, because the system has no
    mechanism for acting on it and a setup it cannot trade is noise on the
    dashboard.

    Ordering is by how many ordered steps a setup required. It is a tie-break
    for display, not a score: two playbooks firing is what matters, and which
    of them is listed first changes nothing about the trade.
    """
    if trend == RANGE or not candles:
        return []
    fns = enabled(L)
    if only:
        # Restrict to a named subset - used to ask "did a REVERSAL model fire
        # against the bias", which must not be answered by a continuation one.
        keep = {PLAYBOOKS[n] for n in only if n in PLAYBOOKS}
        fns = [f for f in fns if f in keep]
    out = []
    for fn in fns:
        try:
            s = fn(candles, st, spot, trend, L, strike_step)
        except Exception:
            # A playbook that throws must not take the whole hunt down with it.
            continue
        if s is not None and s.side == trend:
            out.append(s)
    out.sort(key=lambda s: -s.depth)
    return out


def blocked_by_level(spot, side, candles, L, strike_step=0.0):
    """Is a round number sitting between here and a plausible target?

    Returns a reason string, or "". The level's weight is its measured
    roundness - the spacing it is the first multiple of - compared against the
    grid currently in view, so nothing is labelled "major" in code. A level
    that belongs to a coarser grid than the one being traded is one more people
    are watching, and that is the one worth refusing to trade into.

    This is the half of the psychological-level idea that protects money, and
    it is applied as a veto rather than a confirmation.
    """
    if not bool(getattr(L, "use_level_headroom", True)):
        return ""
    a = atr(candles, period=int(getattr(L, "atr_period", 20)))
    if a <= 0:
        return ""
    span = float(getattr(L, "psych_span_atr", 4.0))
    room = headroom(spot, side, atr=a, span_atr=span, strike_step=strike_step)
    if room is None:
        return ""
    lvl, spacing, dist, units = room
    minor, _ = grid_for(spot, a, span, strike_step)
    need = float(getattr(L, "min_headroom_atr", 0.75))
    # Only a level coarser than the working grid counts as a wall; the working
    # grid itself is crossed constantly and refusing it would refuse everything.
    if spacing > minor and units < need:
        return "no_headroom_to_round_level"
    return ""
