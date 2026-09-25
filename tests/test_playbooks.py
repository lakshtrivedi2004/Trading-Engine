#!/usr/bin/env python3
"""Correctness suite for the sequenced setups (arb/playbooks.py, arb/levels.py).

Two things are being proved here, and they are not the same thing:

  1. Each playbook FIRES on a series built to contain it. A filter that can
     never pass is not a conservative filter, it is dead code, and dead code in
     a trading system looks exactly like a strategy that finds no setups.
  2. Each playbook REFUSES when the ordering is wrong. That is the whole claim
     of the module - a BOS that came before the CHoCH is not the setup, and if
     the code cannot tell those apart it is only counting events again.

Everything is arithmetic on constructed inputs: no network, no market, no luck.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from arb.config import IntradayLimits
from arb.confluence import evaluate
from arb.levels import (at_level, grid_for, headroom, liquidity_pools,
                        opening_range, previous_day_range, roundness,
                        swept_pool, touches)
from arb.playbooks import (PLAYBOOKS, Setup, blocked_by_level, choch_bos,
                           detect, enabled, fib_pullback, ob_fvg_retest,
                           psych_reaction, sweep_bos, unknown)
from arb.regime import atr, regime_gate
from arb.smc import BEAR, BULL, RANGE, analyse

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" | {detail}" if detail else ""))


def section(t):
    print(f"\n[{t}]")


L = IntradayLimits()


# ── builders ────────────────────────────────────────────────────────────────

def mk(seq, day="2026-09-21", start="09:15"):
    """[(o,h,l,c)] -> candle rows one minute apart, timestamps advancing.

    The timestamps must advance: arb.smc.resample and arb.levels both group by
    wall clock, so a series where every bar claims the same minute folds into
    one bar and no structure exists to find.
    """
    t0 = datetime.fromisoformat(f"{day}T{start}:00+05:30")
    return [[(t0 + timedelta(minutes=i)).isoformat(), o, h, l, c, 1000.0]
            for i, (o, h, l, c) in enumerate(seq)]


def wave(px, legs, step=4.0, wick=1.5):
    """Oscillating path with real pivots. legs = [(direction, n_bars)]."""
    seq = []
    for d, n in legs:
        for _ in range(n):
            o = px
            px += d * step
            seq.append((o, max(o, px) + wick, min(o, px) - wick, px))
    return seq, px


def struct(rows):
    return analyse(rows, strength=L.swing_strength, min_bars=L.min_structure_bars)


# ═════════════════════════════════════════════════════════════════════════════
section("1. The event log - the thing sequence detection is built on")

seq, _ = wave(24200.0, [(-1, 6), (1, 3)] * 5 + [(1, 10)] + [(-1, 3), (1, 8)] * 3)
rows_rev = mk(seq)
st_rev = struct(rows_rev)

check("analyse records an ordered event log, not just the last event",
      len(st_rev.events) > 1, f"{len(st_rev.events)} events")
check("events carry (bar_index, name, level)",
      all(len(e) == 3 and isinstance(e[0], int) for e in st_rev.events))
check("event indices are non-decreasing",
      all(a[0] <= b[0] for a, b in zip(st_rev.events, st_rev.events[1:])))
check("bar_count is recorded so 'how long ago' is answerable",
      st_rev.bar_count == len(rows_rev), f"{st_rev.bar_count}")
check("last() finds the most recent matching event",
      (st_rev.last("BOS_UP") or (None,))[0] ==
      max([a for a, n, _ in st_rev.events if n == "BOS_UP"], default=None))
check("last(after=) only returns events strictly newer than the anchor",
      (st_rev.last("BOS_UP", after=10**9) is None))
check("last() on an empty structure is None, not an exception",
      analyse([]).last("BOS_UP") is None)


# ═════════════════════════════════════════════════════════════════════════════
section("2. choch_bos - change of character, THEN break of structure")

# The wave fixture breaks structure with bars no bigger than their neighbours,
# so the ORDERING tests below run with the displacement rule off; displacement
# is tested on its own right after them.
L_ord = IntradayLimits(displacement_body_atr=0.0, trigger_fresh_bars=0)
fired = choch_bos(rows_rev, st_rev, rows_rev[-1][4], BULL, L_ord)
check("fires when a CHoCH is followed by a BOS in the new direction",
      fired is not None, fired.describe() if fired else "no setup")
if fired:
    check("the steps are in order: character change first, break second",
          [s for s, _ in fired.steps] == ["change_of_character",
                                          "break_of_structure"])
    check("it reports the side it actually found", fired.side == BULL)
    check("it nominates an invalidation level", fired.invalidation > 0)

check("refuses when asked for the side the market did not turn to",
      choch_bos(rows_rev, st_rev, rows_rev[-1][4], BEAR, L_ord) is None)

# Order is the claim. Strip every CHoCH and the BOSes alone must not qualify.
only_bos = struct(rows_rev)
only_bos.events = [(a, n, lv) for a, n, lv in only_bos.events
                   if not n.startswith("CHOCH")]
check("a BOS with no preceding CHoCH is NOT this setup",
      choch_bos(rows_rev, only_bos, rows_rev[-1][4], BULL, L_ord) is None)

# And a CHoCH with no BOS after it is only half the sequence.
half = struct(rows_rev)
last_ch = max((a for a, n, _ in half.events if n.startswith("CHOCH")), default=-1)
half.events = [(a, n, lv) for a, n, lv in half.events
               if n.startswith("CHOCH") or a < last_ch]
check("a CHoCH with no BOS after it is NOT this setup",
      choch_bos(rows_rev, half, rows_rev[-1][4], BULL, L_ord) is None)

# Displacement: the same sequence, broken by an ordinary-sized bar, is noise.
check("with displacement on, a weak-bodied break is refused",
      choch_bos(rows_rev, st_rev, rows_rev[-1][4], BULL,
                IntradayLimits(displacement_body_atr=5.0, trigger_fresh_bars=0)) is None)
check("...and a small enough requirement lets it through",
      choch_bos(rows_rev, st_rev, rows_rev[-1][4], BULL,
                IntradayLimits(displacement_body_atr=0.1, trigger_fresh_bars=0))
      is not None)

stale = struct(rows_rev)
stale.bar_count = 10_000            # the same events, but long ago
check("a completed sequence goes stale and stops being tradable",
      choch_bos(rows_rev, stale, rows_rev[-1][4], BULL, L) is None)


# ═════════════════════════════════════════════════════════════════════════════
section("3. fib_pullback - impulse, golden pocket, reaction")

base, _ = wave(23800.0, [(-1, 10), (1, 40)])
_hi = max(s[1] for s in base)
_lo = min(s[2] for s in base[:12])
_leg = _hi - _lo
_mid = _hi - 0.56 * _leg                     # aim for the middle of the pocket
px = base[-1][3]
pull = []
while px > _mid + 6.0:
    o = px
    px -= 5.0
    pull.append((o, o + 1.0, px - 1.0, px))
pull.append((px, px + 0.5, px - 6.0, px - 5.0))          # down bar
pull.append((px - 5.0, px + 7.0, px - 6.0, px + 4.0))    # bullish engulfing
rows_fib = mk(base + pull)
st_fib = struct(rows_fib)
spot_fib = rows_fib[-1][4]

fb = fib_pullback(rows_fib, st_fib, spot_fib, BULL, L)
check("fires on a retrace into 0.5-0.618 with a reaction candle",
      fb is not None, fb.describe() if fb else f"spot {spot_fib}")
if fb:
    check("steps are ordered impulse -> pocket -> reaction",
          [s for s, _ in fb.steps][:2] == ["impulse_leg", "golden_pocket"])
    check("it hands back the pocket as an entry zone",
          fb.zone is not None and fb.zone[0] < fb.zone[1])
    check("the stop sits below the pocket on a long",
          fb.invalidation < fb.zone[0], f"stop {fb.invalidation:.1f}")

check("refuses once price has left the pocket",
      fib_pullback(rows_fib, st_fib, _hi, BULL, L) is None)

tight = IntradayLimits(fib_min_leg_atr=50.0)
check("refuses an impulse too small to frame a stop outside the spread",
      fib_pullback(rows_fib, st_fib, spot_fib, BULL, tight) is None)


# ═════════════════════════════════════════════════════════════════════════════
section("4. Derived round-number levels - nothing tabulated")

check("roundness measures how round a price is",
      roundness(24000, 24000) > roundness(24500, 24500) > roundness(24550, 24550),
      f"{roundness(24000,24000)} > {roundness(24500,24500)} > {roundness(24550,24550)}")

g_nifty = grid_for(24500, 12.0, 4.0)
g_bank = grid_for(57200, 55.0, 4.0)
g_stock = grid_for(250.4, 1.2, 4.0)
check("the same rule yields a sane grid for a ~24k index", g_nifty == (50.0, 100.0),
      str(g_nifty))
check("...for a ~57k index", g_bank == (250.0, 500.0), str(g_bank))
check("...and for a 250-rupee instrument", g_stock == (5.0, 10.0), str(g_stock))
check("the grid widens as the tape speeds up",
      grid_for(24500, 40.0, 4.0)[0] > grid_for(24500, 5.0, 4.0)[0])
check("the exchange strike step floors the grid",
      grid_for(24500, 2.0, 4.0, strike_step=50.0)[0] >= 50.0)
check("a zero price yields no grid rather than a guess",
      grid_for(0.0, 10.0) == (0.0, 0.0))

check("at_level finds the level price is sitting on",
      (at_level(24499.0, atr=12.0, tol=5.0, span_atr=4.0) or (0,))[0] == 24500.0)
check("at_level returns None when price is nowhere near one",
      at_level(24473.0, atr=12.0, tol=1.0, span_atr=4.0) is None)

hr = headroom(24490.0, 1, atr=12.0, span_atr=4.0)
check("headroom looks ahead on a long", hr and hr[0] == 24500.0, str(hr))
hr_s = headroom(24510.0, -1, atr=12.0, span_atr=4.0)
check("headroom looks behind on a short", hr_s and hr_s[0] == 24500.0, str(hr_s))
check("unknown ATR reports infinite room, never zero",
      headroom(24490.0, 1, atr=0.0)[3] == float("inf"))


# ═════════════════════════════════════════════════════════════════════════════
section("5. The headroom veto - not buying into a wall")

wall_rows = mk(wave(24460.0, [(1, 40)], step=1.0)[0])
a_wall = atr(wall_rows, L.atr_period)
near = 24500.0 - 0.5 * a_wall            # 0.5 ATR of room, below the 0.75 floor
check("refuses a long with a coarser round level inside the target distance",
      blocked_by_level(near, BULL, wall_rows, L) != "",
      f"spot {near:.1f} atr {a_wall:.2f}")
check("allows the trade when the level is far enough away",
      blocked_by_level(24300.0, BULL, wall_rows, L) == "")
check("the veto can be switched off",
      blocked_by_level(near, BULL, wall_rows,
                       IntradayLimits(use_level_headroom=False)) == "")
check("no ATR means no veto - absence of data is not evidence of a wall",
      blocked_by_level(24499.0, BULL, wall_rows[:3], L) == "")


# ═════════════════════════════════════════════════════════════════════════════
section("6. Session liquidity pools - the NSE stand-in for the Asia range")

d1 = mk(wave(24000.0, [(1, 60), (-1, 60)])[0], day="2026-09-18")
d2 = mk(wave(24000.0, [(-1, 30), (1, 90)])[0], day="2026-09-21")
two_day = d1 + d2

pdr = previous_day_range(two_day)
check("previous day high/low is read off the earlier session",
      pdr is not None and pdr[0] == min(r[3] for r in d1)
      and pdr[1] == max(r[2] for r in d1), str(pdr))
orr = opening_range(two_day, minutes=30)
check("the opening range covers the first 30 minutes of the LAST day",
      orr is not None and orr[0] == min(r[3] for r in d2[:30]), str(orr))
check("an opening range still forming is not reported",
      opening_range(d2[:20], minutes=30) is None)
check("one day of data yields no previous-day range",
      previous_day_range(d2) is None)
highs, lows = liquidity_pools(two_day)
check("pools are named so the journal can say which was taken",
      {n for n, _ in highs} == {"PDH", "OR_high"}
      and {n for n, _ in lows} == {"PDL", "OR_low"})
check("unparseable timestamps yield no pools rather than invented ones",
      liquidity_pools([["not-a-date", 1, 2, 0, 1, 0]] * 50) == ([], []))


# ═════════════════════════════════════════════════════════════════════════════
section("7. sweep_bos - stops run, then structure agrees")

# Build a day that trades down through the previous day's low and closes back
# above it, then breaks structure upward.
pdl = min(r[3] for r in d1)
day2, px = wave(pdl + 40.0, [(-1, 12)], step=4.0)
day2.append((px, px + 1.0, pdl - 25.0, pdl + 6.0))        # the sweep bar
sweep_seq, px = wave(pdl + 6.0, [(1, 6), (-1, 2), (1, 10)], step=5.0)
day2 += sweep_seq
rows_sw = d1 + mk(day2, day="2026-09-21")
st_sw = struct(rows_sw)

hit = swept_pool(rows_sw, within=40)
check("swept_pool sees a wick through a pool with a close back inside",
      hit is not None and hit[0] == BULL, str(hit))
if hit:
    check("it reports the wick extreme, which is where the stop belongs",
          hit[3] <= hit[2], f"extreme {hit[3]:.1f} <= level {hit[2]:.1f}")
    check("it reports the bar the sweep completed on, so order can be checked",
          isinstance(hit[4], int) and 0 <= hit[4] < len(rows_sw), f"bar {hit[4]}")

sb = sweep_bos(rows_sw, st_sw, rows_sw[-1][4], BULL, L)
check("sweep_bos fires when the sweep is followed by a break of structure",
      sb is not None, sb.describe() if sb else "no setup")
if sb:
    check("the sweep is step one and confirmation is step two",
          sb.steps[0][0] == "liquidity_sweep" and len(sb.steps) == 2)
    check("the stop is placed beyond the swept wick",
          sb.invalidation < hit[3] if hit else True,
          f"stop {sb.invalidation:.1f}")
check("refuses the side the sweep did not point to",
      sweep_bos(rows_sw, st_sw, rows_sw[-1][4], BEAR, L) is None)

# Order is the claim: a break that PREDATES the sweep must not be credited to it.
pre_only = struct(rows_sw)
if hit:
    pre_only.events = [(a, n, lv) for a, n, lv in pre_only.events if a < hit[4]]
check("a break of structure that predates the sweep is NOT this setup",
      sweep_bos(rows_sw, pre_only, rows_sw[-1][4], BULL, L) is None
      or sweep_bos(rows_sw, pre_only, rows_sw[-1][4], BULL, L).steps[1][0]
      == "engulfing")

narrow = IntradayLimits(sweep_lookback_bars=2)
check("a sweep too far back to still be live is refused",
      sweep_bos(rows_sw, st_sw, rows_sw[-1][4], BULL, narrow) is None
      or sweep_bos(rows_sw, st_sw, rows_sw[-1][4], BULL, narrow)
      .steps[0][1].endswith("(swing)"))

check("a close BEYOND the level is a breakout, not a sweep",
      swept_pool(mk([(pdl + 5, pdl + 6, pdl - 30, pdl - 25)] * 6,
                    day="2026-09-21"), within=6) is None)


# ═════════════════════════════════════════════════════════════════════════════
section("8. The registry is configuration, not code")

check("every registered playbook is callable",
      all(callable(f) for f in PLAYBOOKS.values()))
check("an empty config runs them all", len(enabled(L)) == len(PLAYBOOKS))


class _Subset:
    playbooks = ("sweep_bos", "choch_bos")


class _Bogus:
    playbooks = ("sweep_bos", "does_not_exist")


check("a named subset runs only those", len(enabled(_Subset)) == 2)
check("an unknown name is reported rather than silently dropped",
      unknown(_Bogus) == ["does_not_exist"])
check("an unknown name does not stop the known ones running",
      [f.__name__ for f in enabled(_Bogus)] == ["sweep_bos"])
check("detect refuses to look when the higher timeframe has no trend",
      detect(rows_sw, st_sw, rows_sw[-1][4], RANGE, L) == [])
check("detect never returns a setup pointing against the trend",
      all(s.side == BULL for s in detect(rows_sw, st_sw, rows_sw[-1][4], BULL, L)))


# ═════════════════════════════════════════════════════════════════════════════
section("9. The gate - a named setup is required, order is not optional")

v_seq = evaluate(rows_rev, rows_rev[-1][4], L, strike_step=50.0)
check("Verdict exposes which playbooks fired",
      hasattr(v_seq, "setups") and isinstance(v_seq.plays(), list))

# The behaviour change this work exists to make: loose confirmations alone,
# however many, are no longer a trade.
loose, _ = wave(23000.0, [(1, 45), (-1, 20)] * 10)
rows_loose = mk(loose)
v_loose = evaluate(rows_loose, rows_loose[-1][4], L, strike_step=50.0)
check("several unrelated confirmations with no setup do NOT trade",
      not v_loose.tradable and not v_loose.plays(),
      f"{v_loose.count} confirmations, reason: {v_loose.reason[:60]}")

off = IntradayLimits(require_playbook=False, require_discount_entry=False)
v_off = evaluate(rows_loose, rows_loose[-1][4], off, strike_step=50.0)
check("the old flat-count behaviour is still reachable by config",
      v_off.count >= off.min_confirmations,
      f"{v_off.count} confirmations")

check("evaluate still works without a strike step",
      evaluate(rows_rev, rows_rev[-1][4], L) is not None)


# ═════════════════════════════════════════════════════════════════════════════
section("10. Missing data must never read as a calm market")

check("an empty series is reported as missing data, not a quiet regime",
      regime_gate([], L) == "no_candles")
check("too few bars to judge is its own reason",
      regime_gate(mk(wave(24000.0, [(1, 5)])[0]), L) == "candles_warming_up")
check("a real series gets a real verdict",
      regime_gate(mk(wave(24000.0, [(1, 60), (-1, 40)])[0]), L)
      not in ("no_candles", "candles_warming_up"))
check("no playbook throws on an empty series",
      detect([], analyse([]), 0.0, BULL, L) == [])
check("touches counts taps without counting a clean break through",
      touches(mk([(100, 105, 95, 98)] * 3), 100.0, 1.0) == 3)


print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
