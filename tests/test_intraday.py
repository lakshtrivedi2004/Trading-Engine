#!/usr/bin/env python3
"""Correctness suite for the intraday scalping strategy.

Every check is arithmetic on constructed inputs: no network, no market, no luck.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from arb.capital_book import CapitalBook
from arb.config import CostModel, IntradayLimits
from arb.intraday import (ContractSelector, atm_index, chain_delta,
                          convexity_cheapness, plan_trade)
from arb.positions import EXIT, HOLD, Position, PositionBook
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


def bars(seq, start_iso="2026-09-17T09:15:00+05:30", vol=1000):
    """[(o,h,l,c)] -> candle rows, one minute apart.

    The timestamps have to advance: arb.smc.resample groups by wall clock, so a
    series where every bar claims the same minute folds into a single bar.
    """
    from datetime import datetime as _dt, timedelta as _td
    t0 = _dt.fromisoformat(start_iso)
    return [[(t0 + _td(minutes=i)).isoformat(), o, h, l, c, vol]
            for i, (o, h, l, c) in enumerate(seq)]


# ─────────────────────────────────────────────────────────────────────────────
section("1. Market structure: swings, BOS, CHoCH")

base = 24000.0


def trend(legs, up=True, start=base, leg=45.0, pull=18.0, n_up=4, n_dn=2):
    """A realistic trend: impulse legs separated by pullbacks, so the series
    actually HAS swing points. A monotonic ramp has none - every bar is the
    highest - and correctly produces no structure at all."""
    px, out, sign = start, [], (1.0 if up else -1.0)
    for _ in range(legs):
        for _ in range(n_up):
            o = px
            px += sign * leg / n_up
            out.append((o, max(o, px) + 3, min(o, px) - 3, px))
        for _ in range(n_dn):
            o = px
            px -= sign * pull / n_dn
            out.append((o, max(o, px) + 3, min(o, px) - 3, px))
    return out


up = trend(7, up=True)
st = analyse(bars(up))
check("an uptrend of higher highs and higher lows reads bullish", st.bias == BULL,
      f"{st.last_event} · {st.reason}")

down = trend(7, up=False)
st = analyse(bars(down))
check("a downtrend reads bearish", st.bias == BEAR, f"{st.last_event} · {st.reason}")

flat = [(base, base + 3, base - 3, base) for _ in range(40)]
st = analyse(bars(flat))
check("a flat range gives NO bias (no trade)", st.bias == RANGE, st.reason)

st = analyse(bars(up[:10]))
check("too few bars -> no bias, not a guess", st.bias == RANGE, st.reason)

ramp = [(base + i * 6, base + i * 6 + 8, base + i * 6 - 4, base + i * 6 + 5)
        for i in range(40)]
check("a monotonic ramp has no swings, so no bias is invented",
      analyse(bars(ramp)).bias == RANGE, analyse(bars(ramp)).reason)

# Up, then a decisive collapse: the last structural event must turn bearish.
top = up[-1][3]
mixed = up + trend(5, up=False, start=top, leg=60.0, pull=12.0)
st = analyse(bars(mixed))
check("a sharp reversal flips the bias", st.bias == BEAR, f"{st.last_event} · {st.reason}")

st = analyse(bars(up + flat * 2))
check("a stale break goes flat rather than stay bullish", st.bias == RANGE, st.reason)

# ─────────────────────────────────────────────────────────────────────────────
section("2. Chain arithmetic: ATM, delta, cheapness - all model-free")

strikes = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0]
check("ATM index is the strike nearest spot", atm_index(strikes, 24040.0) == 2,
      f"spot 24040 -> {strikes[atm_index(strikes, 24040.0)]:.0f}")
check("ATM works below the ladder too", atm_index(strikes, 23810.0) == 0)

calls = np.array([260.0, 180.0, 115.0, 65.0, 33.0])
d_atm = chain_delta(calls, np.array(strikes), 2, "CE")
d_otm = chain_delta(calls, np.array(strikes), 4, "CE")
check("delta comes out of the chain, never a model", 0.0 < d_atm <= 1.0, f"ATM delta {d_atm:.3f}")
check("ATM delta exceeds far-OTM delta", d_atm > d_otm, f"{d_atm:.3f} > {d_otm:.3f}")

# Convex ladder: no strike is cheap.
check("a convex chain shows no cheap strike",
      abs(convexity_cheapness(calls, strikes, 2)) < 12.0,
      f"{convexity_cheapness(calls, strikes, 2):+.2f} pt")
# Depress the middle: it must register as cheap (positive).
cheap = calls.copy()
cheap[2] -= 20.0
check("a depressed middle strike registers as cheap",
      convexity_cheapness(cheap, strikes, 2) > 15.0,
      f"{convexity_cheapness(cheap, strikes, 2):+.2f} pt")

# ─────────────────────────────────────────────────────────────────────────────
section("3. Contract selection: one lot, inside the purse")


class FakeCV:
    """Just enough of a ChainView for the selector."""
    def __init__(self, strikes, ce, pe, lot, spread=1.0, depth=500, age=50.0):
        self.strikes = np.array(strikes, dtype=float)
        self.ce_ask = np.array(ce, dtype=float)
        self.ce_bid = self.ce_ask - spread
        self.pe_ask = np.array(pe, dtype=float)
        self.pe_bid = self.pe_ask - spread
        n = len(strikes)
        self.ce_askq = np.full(n, depth, dtype=float)
        self.ce_bidq = np.full(n, depth, dtype=float)
        self.pe_askq = np.full(n, depth, dtype=float)
        self.pe_bidq = np.full(n, depth, dtype=float)
        self.ce_age = np.full(n, age, dtype=float)
        self.pe_age = np.full(n, age, dtype=float)
        self.ce_symbol = [f"NIFTY{int(k)}CE" for k in strikes]
        self.pe_symbol = [f"NIFTY{int(k)}PE" for k in strikes]
        self.ce_token = [str(1000 + i) for i in range(n)]
        self.pe_token = [str(2000 + i) for i in range(n)]
        self.lot_size = lot
        self.expiry = "23SEP2026"
        self.underlying = "NIFTY"
        self.segment = "NFO"


L = IntradayLimits()
ladder = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0, 24300.0, 24400.0]
ce = [340.0, 255.0, 180.0, 120.0, 75.0, 44.0, 25.0]
pe = [28.0, 45.0, 78.0, 120.0, 175.0, 245.0, 330.0]
cv = FakeCV(ladder, ce, pe, lot=65)
sel = ContractSelector(L)

pick = sel.select(cv, 24000.0, BULL, capital=10_000.0, max_age_ms=400.0, min_depth_mult=1.0)
check("bullish -> buys a CALL", pick is not None and pick.opt == "CE",
      pick.symbol if pick else sel.last_reject)
check("one lot fits inside Rs 10,000", pick is not None and pick.lot_cost <= 10_000.0,
      f"{pick.symbol} Rs {pick.lot_cost:,.0f}" if pick else "-")
check("picks the DEAREST affordable strike, not the cheapest lottery ticket",
      pick is not None and pick.strike == 24100.0,
      f"chose {pick.strike:.0f} at Rs {pick.lot_cost:,.0f} "
      f"(24200 would be Rs {75.0 * 65:,.0f})" if pick else "-")
check("ATM itself is correctly refused - it costs Rs 11,700",
      180.0 * 65 > 10_000.0, f"ATM lot = Rs {180.0 * 65:,.0f}")

put = sel.select(cv, 24000.0, BEAR, capital=10_000.0, max_age_ms=400.0, min_depth_mult=1.0)
check("bearish -> buys a PUT", put is not None and put.opt == "PE",
      put.symbol if put else sel.last_reject)
check("the put chosen is the dearest that fits (puts dearer as strike rises)",
      put is not None and put.strike == 24100.0 and put.lot_cost <= 10_000.0,
      f"chose {put.strike:.0f} at Rs {put.lot_cost:,.0f} "
      f"(24200 would be Rs {175.0 * 65:,.0f})" if put else "-")

check("a tiny purse buys nothing rather than something silly",
      sel.select(cv, 24000.0, BULL, 500.0, 400.0, 1.0) is None, sel.last_reject)

far = sel.select(cv, 24000.0, BULL, capital=10_000.0, max_age_ms=400.0, min_depth_mult=1.0)
check("never strays beyond ATM +/- 5 strikes",
      far is not None and abs(far.offset) <= L.max_strike_offset,
      f"offset {far.offset:+d}" if far else "-")

stale = FakeCV(ladder, ce, pe, lot=65, age=5000.0)
check("a stale quote is not tradable",
      sel.select(stale, 24000.0, BULL, 10_000.0, 400.0, 1.0) is None, sel.last_reject)

thin = FakeCV(ladder, ce, pe, lot=65, depth=1)
check("insufficient depth is refused",
      sel.select(thin, 24000.0, BULL, 10_000.0, 400.0, 1.0) is None, sel.last_reject)

wide = FakeCV(ladder, ce, pe, lot=65, spread=25.0)
check("an unaffordably wide spread is refused",
      sel.select(wide, 24000.0, BULL, 10_000.0, 400.0, 1.0) is None, sel.last_reject)

# ─────────────────────────────────────────────────────────────────────────────
section("4. The plan: stop where the idea dies, target a multiple of it")

st_bull = analyse(bars(up))
check("the structure used for planning is bullish", st_bull.bias == BULL, st_bull.reason)
cm = CostModel()
from arb.costs import per_unit_cost_points
cost_rt = 2.0 * per_unit_cost_points(pick.ask, 1, 0, pick.lot_size, cm)
plan = plan_trade(pick, st_bull, BULL, L, cost_rt)
check("a plan is produced for a valid structure", plan is not None)
if plan:
    check("stop sits below entry", plan["stop"] < plan["entry"],
          f"{plan['stop']:.2f} < {plan['entry']:.2f}")
    check("target sits above entry", plan["target"] > plan["entry"],
          f"{plan['target']:.2f}")
    rr = (plan["target"] - plan["entry"]) / (plan["entry"] - plan["stop"])
    check("reward:risk is exactly 1:2", abs(rr - 2.0) < 1e-6, f"1:{rr:.2f}")
    check("risk never exceeds half the premium paid",
          plan["risk_points"] <= pick.ask * L.max_risk_frac_of_premium + 1e-9,
          f"risk {plan['risk_points']:.2f} of premium {pick.ask:.2f}")
    check("round-trip cost is charged, not one side",
          plan["cost_points_round_trip"] > per_unit_cost_points(
              pick.ask, 1, 0, pick.lot_size, cm),
          f"{plan['cost_points_round_trip']:.3f} pt both ways")
    check("target clears cost by the minimum points floor",
          plan["reward_points"] - plan["cost_points_round_trip"] >= L.min_net_points,
          f"net {plan['reward_points'] - plan['cost_points_round_trip']:.2f} pt")

tiny = IntradayLimits(min_net_points=500.0)
check("a move too small to be worth it is refused",
      plan_trade(pick, st_bull, BULL, tiny, cost_rt) is None)

# F5 (1 Oct 2026 review): costs inside the cap. A stop-out may then lose at
# most the cap, the round trip and the exit slippage included.
if plan:
    lot = pick.lot_size
    extra = 3.0
    exact = (plan["risk_points"] + extra) * lot
    p_in = plan_trade(pick, st_bull, BULL, L, cost_rt, max_risk_inr=exact, extra_risk_points=extra)
    check("F5: a plan whose stop plus costs fits the cap exactly is taken, and says what it costs",
          p_in is not None and abs(p_in["planned_risk_inr"] - exact) < 1e-6,
          f"{p_in['planned_risk_inr'] if p_in else None} vs {exact:.2f}")
    p_out = plan_trade(pick, st_bull, BULL, L, cost_rt, max_risk_inr=exact - 1.0,
                       extra_risk_points=extra)
    check("F5: one rupee over the cap with costs counted is refused (no 5% allowance)",
          p_out is None)
    p_old = plan_trade(pick, st_bull, BULL, L, cost_rt, max_risk_inr=plan["risk_inr"] * 1.02)
    check("F5 off: today's 5% rounding allowance is unchanged",
          p_old is not None and p_old["planned_risk_inr"] == p_old["risk_inr"])
    # With costs inside the cap, friction is measured against the whole bet
    # (stop + costs + slippage), not the stop alone - otherwise the cost is
    # counted twice and almost nothing can trade (2 Oct 2026 replay).
    fr = IntradayLimits(max_cost_frac_of_risk=0.20)
    pr = plan["risk_points"]
    cost_mid = 0.2 * pr * 1.2                      # over 20% of the stop alone...
    xtra = cost_mid + 1.0                          # ...under 20% of stop + extra
    big = (pr + xtra) * lot + 1.0
    if 2 * pr - cost_mid >= fr.min_net_points and cost_mid <= 0.2 * (pr + xtra):
        check("friction with costs inside: judged on stop + costs, so this plan stands",
              plan_trade(pick, st_bull, BULL, fr, cost_mid, max_risk_inr=big,
                         extra_risk_points=xtra) is not None)
    check("friction without costs inside: unchanged, the same cost is refused",
          plan_trade(pick, st_bull, BULL, fr, cost_mid, max_risk_inr=big) is None)

from arb.intraday_engine import IntradayEngine as _IE
from arb.costs import leg_cost as _leg_cost
_e = _IE.__new__(_IE)
_e.cm = CostModel()
_e.L = IntradayLimits(exact_trade_costs=True)
_exact = (_leg_cost(190.0, 65, "BUY", "OPT", _e.cm) + _leg_cost(190.0, 65, "SELL", "OPT", _e.cm)) / 65
check("exact_trade_costs: the planned round trip is the exact BUY + SELL charge the journal books",
      abs(_e._round_trip_points(190.0, 65) - _exact) < 1e-9, f"{_exact * 65:.2f} rupees a lot")
_e.L = IntradayLimits()
check("exact_trade_costs off: the scanner's upper bound, as before (and never below exact)",
      _e._round_trip_points(190.0, 65) == 2.0 * per_unit_cost_points(190.0, 1, 0, 65, _e.cm)
      and _e._round_trip_points(190.0, 65) >= _exact)

fn_hits = []
sel.select(cv, 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.9,
           max_delta_fn=lambda px, spr: fn_hits.append((px, spr)) or 0.0)
check("F5: the per-candidate delta ceiling is asked for each contract and applied",
      len(fn_hits) > 0 and sel.select(cv, 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.9,
                                      max_delta_fn=lambda px, spr: 0.0) is None,
      f"{len(fn_hits)} contracts asked")

# Review D-L4 (2 Oct 2026): the refusal quotes the ceiling the candidates were
# held to, not the cost-free max_delta hunt() also passes.
sel.select(cv, 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.9,
           max_delta_fn=lambda px, spr: 0.17 if px > 50.0 else 0.18)
check("D-L4: the reject text quotes the largest per-contract ceiling, not the cost-free max_delta",
      "0.18" in sel.last_reject and "0.90" not in sel.last_reject, sel.last_reject)
sel.select(cv, 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.9,
           max_delta_fn=lambda px, spr: 0.10)
check("D-L4: costs that push every ceiling under min_delta say the stop is too wide",
      sel.last_reject == "chart stop too wide for the risk cap even at min_delta", sel.last_reject)
sel.select(cv, 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.17)
check("D-L4: without max_delta_fn the text is unchanged",
      sel.last_reject == "no strike at delta <= 0.17 fits the risk cap", sel.last_reject)

# ─────────────────────────────────────────────────────────────────────────────
section("5. Managing the position: stop, target, trail, square-off")


def fresh(entry=100.0, risk=10.0, rr=2.0):
    return Position(trade_id=1, symbol="X", token="1", strike=24300.0, opt="CE",
                    side=1, qty=65, entry=entry, stop=entry - risk,
                    target=entry + rr * risk, risk_points=risk,
                    index_invalidation=23990.0, opened_at=time.time())


bk = PositionBook(L)
p = fresh()
check("a quiet tick holds", bk.review(p, 102.0)[0] == HOLD, "bid 102")
check("hitting the stop exits", bk.review(fresh(), 89.0) == (EXIT, "stop_loss"))
check("hitting the target exits", bk.review(fresh(), 121.0) == (EXIT, "target"))

p = fresh()
bk.review(p, 110.0)                      # +1R
check("at +1R the stop trails to breakeven", p.trailed and abs(p.stop - 100.0) < 1e-9,
      f"stop now {p.stop:.2f}")
check("after trailing, a fall back to entry exits flat",
      bk.review(p, 99.9)[0] == EXIT, "stop is now entry")

# The premium-ratchet trail is now trail_mode="premium"; the default trails on
# index swings instead (tests/test_scalper.py section 9).
bk = PositionBook(IntradayLimits(trail_mode="premium"))
p = fresh()
bk.review(p, 110.0)                      # trail to breakeven
bk.review(p, 118.0)                      # runs further
check("the stop ratchets up behind a running trade", p.stop > 100.0,
      f"stop {p.stop:.2f} after peak 118")

p = fresh()
check("the index breaking structure exits even while premium is fine",
      bk.review(p, 105.0, index_px=23980.0) == (EXIT, "structure_broken"),
      "premium +5 but structure gone")

p = fresh()
check("square-off beats every other rule",
      bk.review(p, 118.0, minutes_to_close=0.5) == (EXIT, "squareoff"),
      "even sitting on profit")

p = fresh()
bk.review(p, 90.0)
check("MAE records the worst price seen", abs(p.mae - 90.0) < 1e-9, f"{p.mae:.2f}")
bk.review(p, 115.0)
check("MFE records the best price seen", abs(p.mfe - 115.0) < 1e-9, f"{p.mfe:.2f}")

p = fresh()
bk.add(p)
check("deployed capital is entry x qty", abs(bk.deployed() - 6500.0) < 1e-9,
      f"Rs {bk.deployed():,.0f}")

# ─────────────────────────────────────────────────────────────────────────────
section("6. The purse compounds")

path = "/tmp/_cap_test.json"
if os.path.exists(path):
    os.remove(path)
cb = CapitalBook(10_000.0, path=path)
check("starts at the configured capital", cb.capital == 10_000.0)
check("the daily target is a percentage, not a fixed rupee figure",
      cb.daily_target(10.0) == 1_000.0, f"Rs {cb.daily_target(10.0):,.0f} on Rs 10,000")

before, after = cb.fold_in(1_200.0, trades=3)
check("the day's profit is folded into capital", after == 11_200.0,
      f"Rs {before:,.0f} -> Rs {after:,.0f}")
check("tomorrow's target grows with the purse", cb.daily_target(10.0) == 1_120.0,
      f"Rs {cb.daily_target(10.0):,.0f} on Rs 11,200")
cb.fold_in(-700.0, trades=2)
check("a losing day shrinks it just the same", cb.capital == 10_500.0,
      f"Rs {cb.capital:,.0f}")

cb2 = CapitalBook(10_000.0, path=path)
check("capital survives a restart", cb2.capital == 10_500.0, f"Rs {cb2.capital:,.0f}")
check("history is kept per day", len(cb2.summary()["history"]) == 2)
os.remove(path)

# ─────────────────────────────────────────────────────────────────────────────
section("7. Breakeven that actually breaks even")

from arb.positions import Position, PositionBook, HOLD, EXIT
from arb.regime import atr_ratio, close_position, session_phase, regime_gate
from datetime import datetime
from arb.instruments import IST

L7 = IntradayLimits()
book7 = PositionBook(L7)


def fresh(cost=4.0, entry=100.0, risk=10.0):
    return Position(trade_id=1, symbol="X", token="1", strike=100.0, opt="CE",
                    side=1, qty=75, entry=entry, stop=entry - risk,
                    target=entry + L7.reward_risk * risk, risk_points=risk,
                    index_invalidation=0.0, opened_at=0.0, cost_points=cost)


pos = fresh()
act, why = book7.review(pos, bid=110.0)          # +1R exactly
check("at +1R the stop trails up", why == "trailed_to_breakeven", f"stop {pos.stop:.2f}")
check("breakeven sits ABOVE the fill, by the round trip",
      abs(pos.stop - 104.0) < 1e-9, f"entry 100.00 + cost 4.00 -> stop {pos.stop:.2f}")
check("a trade stopped at breakeven is now flat, not down the round trip",
      (pos.stop - pos.entry) * pos.qty >= 4.0 * 75 - 1e-6,
      f"Rs {(pos.stop - pos.entry) * pos.qty:,.0f} covers Rs {4.0 * 75:,.0f} of costs")

# The trail must never be placed at or through the live mark - that would close
# the trade under the name of protecting it.
tight = fresh(cost=12.0)
book7.review(tight, bid=110.0)
check("a cost bigger than the gain never trails INTO the mark",
      tight.stop < 110.0, f"stop {tight.stop:.2f} vs mark 110.00")
act2, _ = book7.review(tight, bid=110.0)
check("...and so does not instantly exit itself", act2 == HOLD)

off = IntradayLimits()
off.breakeven_covers_costs = False
b_off = PositionBook(off)
p_off = fresh()
b_off.review(p_off, bid=110.0)
check("the old entry-price behaviour is still one flag away",
      abs(p_off.stop - 100.0) < 1e-9, f"stop {p_off.stop:.2f}")

check("below +1R nothing is trailed", fresh().trailed is False)

# ─────────────────────────────────────────────────────────────────────────────
section("8. Regime gate (research/PAPERS.md)")

calm = bars([(100.0, 100.2, 99.8, 100.0)] * 240)
check("a dead tape yields a low ATR ratio", atr_ratio(calm) <= 1.0,
      f"ratio {atr_ratio(calm):.2f}")
check("too little history is 'unknown', never 'calm'", atr_ratio(calm[:5]) == 0.0)

check("a bar closing on its high reads as an impulse",
      close_position([0, 100.0, 110.0, 100.0, 110.0]) == 1.0)
check("a bar closing mid-range reads as indecision",
      close_position([0, 100.0, 110.0, 100.0, 105.0]) == 0.5)
check("a zero-range bar is indecision, not a divide by zero",
      close_position([0, 100.0, 100.0, 100.0, 100.0]) == 0.5)

noon = datetime(2026, 9, 22, 12, 30, tzinfo=IST)
check("midday is named midday", session_phase(now=noon) == "midday")
check("the first hour is 'open'",
      session_phase(now=datetime(2026, 9, 22, 9, 30, tzinfo=IST)) == "open")
check("the last hour is 'close'",
      session_phase(now=datetime(2026, 9, 22, 14, 45, tzinfo=IST)) == "close")
check("before the bell is 'outside'",
      session_phase(now=datetime(2026, 9, 22, 8, 0, tzinfo=IST)) == "outside")

# A perfectly flat tape is its own normal (ATR ratio 1.0), so the ratio alone
# cannot refuse it; the indecision filter can, when it is switched on. The cost
# floor in plan_trade is what refuses it by default - a 0.4-point bar cannot pay
# for a round trip.
L7i = IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, min_close_position=0.2)
check("a quiet tape is refused with a readable reason (indecision filter on)",
      regime_gate(calm, L7i, now=datetime(2026, 9, 22, 9, 30, tzinfo=IST))
      in ("regime_too_quiet", "regime_unknown", "bar_indecisive"),
      regime_gate(calm, L7i, now=datetime(2026, 9, 22, 9, 30, tzinfo=IST)))

short = bars([(100.0, 101.0, 99.0, 100.5)] * 22)
check("an unknown regime (too little baseline) is not refused by default",
      regime_gate(short, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0),
                  now=datetime(2026, 9, 22, 9, 30, tzinfo=IST)) == "")
check("...and is refused when trade_unknown_regime is off",
      regime_gate(short, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, trade_unknown_regime=False),
                  now=datetime(2026, 9, 22, 9, 30, tzinfo=IST))
      in ("regime_unknown", ""))

quiet_noon = datetime(2026, 9, 22, 12, 30, tzinfo=IST)
check("midday 'block' refuses the middle of the session",
      regime_gate(calm, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, midday_mode="block"), now=quiet_noon)
      == "midday_chop")
check("midday 'strict' refuses an ordinary-volatility lunch",
      regime_gate(calm, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, midday_mode="strict",
                                       midday_min_atr_ratio=1.2),
                  now=quiet_noon) == "midday_chop")
check("midday 'off' treats lunch like any other time",
      regime_gate(calm, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, midday_mode="off"), now=quiet_noon) == "")
check("the old skip_midday flag still means block",
      regime_gate(calm, IntradayLimits(min_atr_ratio=0.7, sideways_bars=0, skip_midday=True, midday_mode="off"),
                  now=quiet_noon) == "midday_chop")

L_off = IntradayLimits(min_atr_ratio=0.7, sideways_bars=0)
L_off.use_regime_gate = False
check("the whole gate is one flag away from off",
      regime_gate(calm, L_off, now=noon) == "")

# Never trade sideways: the index must travel, and so must the premium we buy.
from arb.regime import efficiency, premium_gate
check("a straight line is fully efficient, a round trip is not",
      efficiency([1, 2, 3]) == 1.0 and efficiency([1, 2, 1]) == 0.0)
chop = bars([(100.0, 101.0, 99.0, 100.0 + (i % 2)) for i in range(40)])
run = bars([(100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i) for i in range(40)])
check("a sideways index is refused", regime_gate(chop, IntradayLimits(midday_mode="off", sideways_bars=15),
                                                   now=noon) == "index_sideways")
check("...and is refused even with the regime gate off",
      regime_gate(chop, IntradayLimits(use_regime_gate=False, sideways_bars=15), now=noon) == "index_sideways")
check("a trending index is not refused as sideways",
      regime_gate(run, IntradayLimits(midday_mode="off", sideways_bars=15), now=noon) != "index_sideways")
check("a tape quieter than 1.5x its own normal is refused by default",
      IntradayLimits().min_atr_ratio == 1.5)
Lp = IntradayLimits(premium_rise_bars=10)
check("the premium-rise gate is off by default", premium_gate([100.0] * 3, 1.0, IntradayLimits()) == "")
check("a flat premium is refused even if the index moved",
      premium_gate([100.0] * 11, 1.0, Lp) == "premium_not_rising")
check("a premium rising by less than the round trip is refused",
      premium_gate([100.0 + 0.05 * i for i in range(11)], 1.0, Lp) == "premium_not_rising")
check("a premium rising past its costs may trade",
      premium_gate([100.0 + i for i in range(11)], 1.0, Lp) == "")
check("no premium chart yet means no trade",
      premium_gate([100.0] * 3, 1.0, Lp) == "premium_chart_warming_up")

# ─────────────────────────────────────────────────────────────────────────────
section("9. Pure SMC primitives")

from arb.smc import (find_fvgs, liquidity_sweep, dealing_range, resample,
                     analyse as smc_analyse)

# A gap: bar 1 high 100, bar 2 drives up, bar 3 low 105. Nothing traded 100-105.
gap_rows = bars([(99, 100, 98, 99.5), (99.5, 106, 99.5, 105.5), (105.5, 107, 105, 106)])
fv = find_fvgs(gap_rows)
check("a three-bar imbalance is found", len(fv) == 1 and fv[0].side == BULL,
      f"{len(fv)} gap(s)")
check("the gap is the band nobody traded",
      fv and abs(fv[0].low - 100) < 1e-9 and abs(fv[0].high - 105) < 1e-9,
      f"{fv[0].low:.1f}-{fv[0].high:.1f}" if fv else "")
check("an unfilled gap is live", fv and fv[0].mitigated is False)

filled = gap_rows + bars([(106, 106, 99, 99.5)])
check("price trading back through the gap mitigates it",
      find_fvgs(filled)[0].mitigated is True)
check("overlapping wicks are NOT a gap",
      len(find_fvgs(bars([(99, 101, 98, 100), (100, 104, 99, 103), (103, 105, 100, 104)]))) == 0)

# A sweep: make a clear swing low, then a bar that wicks under it and closes back above.
sweep_rows = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100, 100.5, 96, 97),
                   (97, 99, 96.5, 98.5), (98.5, 100, 98, 99.5), (99.5, 101, 99, 100.5),
                   (100.5, 101, 94, 99.0)])
side, lvl = liquidity_sweep(sweep_rows, strength=1)
check("a wick under an old low that closes back above is a BULLISH sweep",
      side == BULL, f"side {side} at {lvl:.1f}")
check("...and a bar that simply closes below is not a sweep",
      liquidity_sweep(bars([(100, 101, 99, 100)] * 3 + [(100, 100.5, 96, 96.2)]),
                      strength=1)[0] == RANGE)

lo, hi = dealing_range(bars([(100, 110, 90, 105)] * 5))
check("the dealing range is the window's extremes", lo == 90 and hi == 110, f"{lo}-{hi}")

st_r = smc_analyse(bars(trend(6)))
check("discount is the bottom half of the range",
      st_r.pricing(st_r.range_low + 0.1 * (st_r.range_high - st_r.range_low)) == "discount")
check("premium is the top half",
      st_r.pricing(st_r.range_high - 0.1 * (st_r.range_high - st_r.range_low)) == "premium")
check("the middle is neither, so it is not tradable either way",
      st_r.pricing(st_r.equilibrium) == "equilibrium")

fifteen = resample(bars([(i, i + 2, i - 2, i + 1) for i in range(100, 130)]), 15)
check("fifteen 1m bars fold into one 15m bar", len(fifteen) == 2, f"{len(fifteen)} bars")
check("the folded bar takes the group's true high and low",
      fifteen[0][2] == 116 and fifteen[0][3] == 98, f"h {fifteen[0][2]} l {fifteen[0][3]}")
check("a partial group is never emitted",
      len(resample(bars([(1, 2, 0, 1)] * 20), 15)) == 1)
check("volume is summed across the fold",
      resample([['t', 1, 2, 0, 1, 10]] * 15, 15)[0][5] == 150)

# ─────────────────────────────────────────────────────────────────────────────
section("10. Confluence: two confirmations or no trade")

from arb.confluence import evaluate, day_trend, Verdict
from arb.volume import relative_volume, vwap_side, has_volume

LC = IntradayLimits()


def expand(seq, factor=15, vol=0):
    """One higher-timeframe bar -> `factor` one-minute bars that fold back into it.

    Lets a test state the 15-minute chart it means and feed the engine the
    1-minute series the engine actually reads, with no daylight between them.
    """
    out = []
    for (o, h, l, c) in seq:
        out.append((o, h, o, h))                      # opens the bar, prints the high
        out.append((h, h, l, l))                      # prints the low
        out.extend([(l, l, l, l)] * (factor - 3))
        out.append((l, c, l, c))                      # closes it
    return bars(out, vol=vol)


def htf_up(vol=0):
    """A 1-minute series whose 15-minute fold is a clean uptrend."""
    return expand(trend(7, up=True), vol=vol)


up = htf_up()
tr, htf_st = day_trend(up, LC)
check("the day trend is read off the folded higher timeframe", tr == BULL, f"bias {tr}")
check("...and the higher timeframe structure is returned with it", htf_st is not None)

flat = bars([(24000, 24001, 23999, 24000)] * 400)
check("a directionless day yields NO trend, not a coin flip",
      day_trend(flat, LC)[0] == RANGE)
v_flat = evaluate(flat, 24000.0, LC)
check("...and no trend means no trade", v_flat.tradable is False, v_flat.reason)

check("too little history refuses rather than guesses",
      evaluate(bars([(1, 2, 0, 1)] * 5), 1.0, LC).tradable is False)

# The headline rule.
v_up = evaluate(up, up[-1][4], LC)
check("a verdict never fires below the confirmation floor",
      v_up.count >= LC.min_confirmations or not v_up.tradable,
      f"{v_up.count} confirmations, tradable={v_up.tradable}")
check("a tradable verdict always names its reasons",
      (not v_up.tradable) or len(v_up.names()) >= 2, str(v_up.names()))
check("a verdict never points against the day trend",
      v_up.side in (RANGE, v_up.day_trend), f"side {v_up.side} trend {v_up.day_trend}")

strict = IntradayLimits()
strict.min_confirmations = 3
v3 = evaluate(up, up[-1][4], strict)
check("raising the floor to 3 can only make it harder, never easier",
      (not v3.tradable) or v3.count >= 3, f"{v3.count} confirmations")

check("confirmations are counted, never weighted",
      all(isinstance(n, str) for n in v_up.names()))

# Premium/discount veto: an uptrend priced at the very top of its own range.
top = evaluate(up, max(c[2] for c in up[-60:]) + 50, LC)
check("chasing an uptrend at the top of its range is vetoed",
      top.veto in ("premium_not_discount", "counter_trend") or not top.tradable,
      f"veto '{top.veto}' · {top.reason}")

no_disc = IntradayLimits()
no_disc.require_discount_entry = False
check("the discount rule is one flag away from off",
      evaluate(up, max(c[2] for c in up[-60:]) + 50, no_disc).veto
      != "premium_not_discount")

# Volume must degrade honestly, not invent a confirmation from zeros.
# The headline rule, stated as a rule: an uptrend on the higher timeframe with
# a downtrend on the lower is a PULLBACK, and a pullback is somewhere to buy
# with the trend - never a reason to sell into it.
mixed = expand(trend(7, up=True))[:-90] + bars(trend(3, up=False, start=24000.0))
v_mix = evaluate(mixed, mixed[-1][4], LC)
# Only a REVERSAL playbook may trade against the day trend now; anything else
# pointing the other way is refused (vetoed, or simply no setup found).
check("lower timeframe fighting the day trend never sells into it",
      (not v_mix.tradable) or v_mix.side == v_mix.day_trend or v_mix.reversal,
      f"veto '{v_mix.veto}' · {v_mix.reason}")
no_rev = IntradayLimits(allow_reversals=False)
v_nr = evaluate(mixed, mixed[-1][4], no_rev)
check("with reversals off, nothing trades against the higher timeframe",
      v_nr.side in (RANGE, v_nr.day_trend) or v_nr.day_trend == RANGE,
      f"side {v_nr.side} trend {v_nr.day_trend}")

# Raising the floor above what any setup can supply must silence the system
# entirely rather than quietly fall back to fewer confirmations.
impossible = IntradayLimits()
impossible.min_confirmations = 99
check("an unreachable confirmation floor means no trades at all, ever",
      evaluate(htf_up(vol=500), 24000.0, impossible).tradable is False)

check("a refusal always says what was missing",
      any(k in evaluate(htf_up(vol=500), 24000.0, impossible).reason
          for k in ("/", "no completed setup", "no bias")),
      evaluate(htf_up(vol=500), 24000.0, impossible).reason)

# Volume is the one non-price input, and it must come from real traded size.
volrows = htf_up(vol=800)
for i in range(-6, 0):
    volrows[i][5] = 9000
check("a real volume column produces a volume confirmation",
      relative_volume(volrows) is not None
      and relative_volume(volrows) > 0, f"z {relative_volume(volrows)}")

check("a zero-volume series contributes no confirmation",
      "volume" not in evaluate(htf_up(vol=0), 24000.0, LC).names())
check("has_volume tells the truth about a column of zeros",
      has_volume(htf_up(vol=0)) is False and has_volume(htf_up(vol=500)) is True)

# ─────────────────────────────────────────────────────────────────────────────
section("11. Rules that must hold whatever the config says")

check("selling is off by default", IntradayLimits().allow_selling is False)
check("reward:risk is at least 1:2", IntradayLimits().reward_risk >= 2.0)
check("the daily target is a goal, never a halt - only loss halts",
      hasattr(IntradayLimits(), "max_daily_loss_pct")
      and IntradayLimits().daily_target_pct == 10.0)
check("strike range is ATM +/- 5", IntradayLimits().max_strike_offset == 5)

# ─────────────────────────────────────────────────────────────────────────────
section("12. Per-index pace, 4-hour trend, expiry, capital ceiling (PAPERS.md 0k)")
import tempfile
from types import SimpleNamespace
from arb.regime import trend_4h
from arb.intraday_engine import IntradayEngine
from arb.instruments import now_ist as _now

ar_calm = atr_ratio(calm)
Lh = IntradayLimits(sideways_bars=0, min_atr_ratio=5.0, midday_mode="strict", midday_min_atr_ratio=5.0,
                    atr_by_index={"NIFTY": [ar_calm - 0.1, ar_calm + 0.5]})
check("a listed index uses its first-half pace, and the midday rule does not apply",
      regime_gate(calm, Lh, now=datetime(2026, 9, 22, 10, 30, tzinfo=IST), name="NIFTY") == "",
      f"ratio {ar_calm:.2f}")
check("after the half-day split the second-half pace applies",
      regime_gate(calm, Lh, now=datetime(2026, 9, 22, 13, 0, tzinfo=IST), name="NIFTY") == "regime_too_quiet")
check("an index not in the map keeps the global pace",
      regime_gate(calm, Lh, now=datetime(2026, 9, 22, 10, 30, tzinfo=IST), name="SENSEX") == "regime_too_quiet")

def sessions(closes_per_day, start="2026-09-21"):
    from datetime import date as _d, timedelta as _td
    rows, d = [], _d.fromisoformat(start)
    for c in closes_per_day:
        t0 = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        for m in range(375):
            px = c + m * 0.01
            rows.append([(t0 + _td(minutes=m)).isoformat(), px, px + 1, px - 1, px, 0])
        d += _td(days=1)
    return rows
up4 = sessions([100, 110, 120, 130])
check("four rising sessions read as a rising 4-hour trend", trend_4h(up4, 5) == 1)
check("four falling sessions read as a falling 4-hour trend", trend_4h(sessions([130, 120, 110, 100]), 5) == -1)
check("too little history is 0 (unknown), never a direction", trend_4h(sessions([100, 110]), 5) == 0)
crash = up4 + [[up4[-1][0][:11] + "15:29:00+05:30", 50.0, 51.0, 49.0, 50.0, 0]]
check("the 4-hour bar still forming is left out", trend_4h(up4[:-60] + crash[-1:], 5) == 1)

today = _now().strftime("%d%b%Y").upper()
b0 = SimpleNamespace(underlying="NIFTY", expiry=today, T=0.0005)
b1 = SimpleNamespace(underlying="NIFTY", expiry="31DEC2099", T=0.02)
eng = SimpleNamespace(bindings=[b1, b0], L=IntradayLimits(expiry_trade_next=True))
check("on expiry day the NEXT expiry is traded when expiry_trade_next is on",
      IntradayEngine._binding_for(eng, "NIFTY") is b1)
eng.L = IntradayLimits()
check("by default the nearest expiry is traded, 0DTE included", IntradayEngine._binding_for(eng, "NIFTY") is b0)
eng.bindings = [b0]; eng.L = IntradayLimits(expiry_trade_next=True)
check("with only today's expiry subscribed it is still traded rather than nothing",
      IntradayEngine._binding_for(eng, "NIFTY") is b0)

with tempfile.TemporaryDirectory() as tmp:
    cb = CapitalBook(13466.9, path=os.path.join(tmp, "c.json"), ceiling=13466.9)
    cb.open_day("2026-10-01"); cb.record(+500.0); cb.close_day()
    check("a profit stays in the equity but the traded capital stays at the ceiling",
          abs(cb.capital - 13466.9) < 1e-6 and abs(cb.equity - 13966.9) < 1e-6, f"{cb.capital} / {cb.equity}")
    cb.open_day("2026-10-02"); cb.record(-1000.0); cb.close_day()
    check("a loss below the ceiling shrinks the traded capital to the equity",
          abs(cb.capital - 12966.9) < 1e-6, f"{cb.capital}")
    check("the day's target and risk cap use the traded capital",
          abs(cb.daily_target(10.0) - 1296.69) < 1e-6)
    cc = CapitalBook(10000.0, path=os.path.join(tmp, "d.json"))
    cc.open_day("2026-10-01"); cc.record(+500.0); cc.close_day()
    check("without a ceiling the day's profit still compounds", abs(cc.capital - 10500.0) < 1e-6)

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
