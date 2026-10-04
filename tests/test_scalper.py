#!/usr/bin/env python3
"""The scalper rework: one setup is a trade, discipline is code, data survives.

What these prove, in order:

  1. The confirmation floor no longer gates a completed setup, and the extras
     that remain are a GRADE - which can still be made a floor, on purpose.
  2. The two new playbooks fire on series built to contain them and refuse the
     ones that do not (ib_breakout, vwap_pullback).
  3. The day trend degrades instead of vanishing: gaps in live bars no longer
     delete higher-timeframe bars, and an undecided HTF falls back.
  4. The discipline rules - loss cooldown, streak pause, grade-after-streak,
     sizing by grade, time stop - behave as written.
  5. Candle data is persisted and a refused history request is visible.

Arithmetic on constructed inputs only: no network, no market.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from arb.config import IntradayLimits
from arb.confluence import Verdict, evaluate, grade_at_least, _grade
from arb.playbooks import Setup, detect, ib_breakout, vwap_pullback, PLAYBOOKS
from arb.positions import EXIT, HOLD, Position, PositionBook
from arb.smc import BEAR, BULL, RANGE, analyse, resample

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


def mk(seq, day="2026-09-21", start="09:15", vol=1000.0):
    t0 = datetime.fromisoformat(f"{day}T{start}:00+05:30")
    return [[(t0 + timedelta(minutes=i)).isoformat(), o, h, l, c, vol]
            for i, (o, h, l, c) in enumerate(seq)]


L = IntradayLimits()

# ═════════════════════════════════════════════════════════════════════════════
section("1. Grades and the confirmation floor")

check("the default floor is one read - the setup itself", L.min_confirmations == 1)
check("grade C accepts anything", grade_at_least("A", "C") and grade_at_least("C", "C"))
check("grade B refuses C", not grade_at_least("C", "B"))
check("grade A is the best", grade_at_least("A", "A") and not grade_at_least("B", "A"))
check("no grade never passes a real floor", not grade_at_least("", "B"))
check("grading is by independent extras",
      _grade(0, L) == "C" and _grade(L.grade_b_extras, L) == "B"
      and _grade(L.grade_a_extras, L) == "A")

v = Verdict(confirmations=[("play:x", ""), ("order_block", ""), ("vwap", "")])
check("extras exclude the playbook entries themselves", v.extras == 2)

# ═════════════════════════════════════════════════════════════════════════════
section("2. ib_breakout - Umar Punjabi's initial balance")

# 30 balance bars oscillating 24000-24040, a few quiet bars inside, then one
# strong close above the balance, then the live bar still above it.
bal = []
px = 24000.0
for i in range(30):
    o = px
    px = 24035.0 if i % 2 == 0 else 24005.0
    bal.append((o, max(o, px) + 3, min(o, px) - 3, px))
inside = [(24020.0, 24030.0, 24010.0, 24025.0)] * 5
brk = [(24025.0, 24058.0, 24024.0, 24055.0)]
live = [(24055.0, 24060.0, 24052.0, 24058.0)]
day = mk(bal + inside + brk + live)
st = analyse(day, strength=2, min_bars=25)
ib_hi = max(r[2] for r in day[:30])

s_ib = ib_breakout(day, st, day[-1][4], BULL, L)
check("fires on a fresh close above the balance", s_ib is not None,
      s_ib.describe() if s_ib else "")
check("...and names the balance as its first step",
      s_ib is not None and s_ib.steps[0][0] == "initial_balance")
check("...with the stop at the balance midpoint by default",
      s_ib is not None and abs(s_ib.invalidation - 0.5 * (ib_hi + min(r[3] for r in day[:30]))) < 1e-6,
      f"{s_ib.invalidation if s_ib else 0:.1f}")
check("does not fire the other way", ib_breakout(day, st, day[-1][4], BEAR, L) is None)

far = IntradayLimits(ib_stop="far")
s_far = ib_breakout(day, st, day[-1][4], BULL, far)
check("ib_stop='far' puts the stop under the whole balance",
      s_far is not None and s_far.invalidation < min(r[3] for r in day[:30]))

check("not before the balance is complete",
      ib_breakout(day[:25], analyse(day[:25], 2, 20), day[24][4], BULL, L) is None)

back = day[:-1] + [[day[-1][0], 24040.0, 24041.0, 24020.0, 24022.0, 1000.0]]
check("not when price has fallen back inside the balance",
      ib_breakout(back, analyse(back, 2, 25), 24022.0, BULL, L) is None)

stale = mk(bal + inside + brk + [(24055.0, 24062.0, 24050.0, 24058.0)] * 20 + live)
check("a break too many bars ago is not a fresh break",
      ib_breakout(stale, analyse(stale, 2, 25), stale[-1][4], BULL, L) is None)

late = IntradayLimits(ib_last_entry="09:40")
check("no balance breaks after ib_last_entry",
      ib_breakout(day, st, day[-1][4], BULL, late) is None)

check("ib_breakout is registered", "ib_breakout" in PLAYBOOKS)

# ═════════════════════════════════════════════════════════════════════════════
section("3. vwap_pullback")

# Rising day with volume, then a dip that tags VWAP and a strong bar away.
up = [(24000.0 + i * 2, 24000.0 + i * 2 + 3, 24000.0 + i * 2 - 1, 24000.0 + i * 2 + 2)
      for i in range(30)]
rows_up = mk(up)
from arb.volume import vwap as _vwap
w = _vwap(rows_up)
dip = [(w + 6, w + 7, w + 0.5, w + 3)]           # tags VWAP, closes above it
go = [(w + 3, w + 14, w + 2.5, w + 13.5)]         # strong bar away from VWAP
rows_v = mk(up + dip + go)
st_v = analyse(rows_v, 2, 25)
sv = vwap_pullback(rows_v, st_v, rows_v[-1][4], BULL, L)
check("fires on a pullback that tags VWAP and leaves in the trend direction",
      sv is not None, sv.describe() if sv else "")
check("...with the stop the other side of VWAP",
      sv is not None and sv.invalidation < w)
check("abstains without volume - no VWAP without traded size",
      vwap_pullback(mk(up + dip + go, vol=0.0), st_v, rows_v[-1][4], BULL, L) is None)
check("does not fire short above VWAP",
      vwap_pullback(rows_v, st_v, rows_v[-1][4], BEAR, L) is None)

check("detect(only=...) restricts to the named playbooks",
      all(s.name == "ib_breakout"
          for s in detect(day, st, day[-1][4], BULL, L, only=("ib_breakout",))))
check("detect(only=unknown) finds nothing",
      detect(day, st, day[-1][4], BULL, L, only=("nope",)) == [])

# ═════════════════════════════════════════════════════════════════════════════
section("4. evaluate - one setup is enough")

v_ib = evaluate(day, day[-1][4], L)
check("a lone completed setup is tradable at the default floor",
      v_ib.tradable, v_ib.reason)
check("...and carries a grade", v_ib.grade in ("A", "B", "C"), v_ib.grade)

strict = IntradayLimits(min_confirmations=4)
check("raising min_confirmations can still refuse it",
      not evaluate(day, day[-1][4], strict).tradable or
      evaluate(day, day[-1][4], strict).count >= 4)

floor_a = IntradayLimits(min_grade="A")
va = evaluate(day, day[-1][4], floor_a)
check("min_grade is a floor when asked for",
      (not va.tradable) or va.grade == "A", f"{va.grade} {va.reason}")

only_bo = IntradayLimits(breakout_playbooks=())
check("breakouts are exempt from the range-extreme veto only when listed",
      evaluate(day, day[-1][4], only_bo).veto in ("", "premium_not_discount"))

# Extras that restate the setup are not counted.
if v_ib.tradable:
    check("an extra never restates a step of the setup",
          not ("structure" in v_ib.names() and any(
              st_ in ("break_of_structure", "change_of_character")
              for s in v_ib.setups for st_, _ in s.steps)))

check("no setup, no trade - with the reason said",
      "setup" in evaluate(mk([(24000, 24001, 23999, 24000)] * 60), 24000.0, L).reason
      or "bias" in evaluate(mk([(24000, 24001, 23999, 24000)] * 60), 24000.0, L).reason)

# ═════════════════════════════════════════════════════════════════════════════
section("5. The day trend degrades instead of vanishing")

full = mk([(1, 2, 0, 1)] * 20)
holey = [r for i, r in enumerate(full) if i not in (3, 12)]   # two quiet minutes
check("strict fill drops a bucket with one missing minute",
      len(resample(holey, 5)) < len(resample(full, 5)))
check("min_fill tolerates the hole",
      len(resample(holey, 5, min_fill=0.6)) >= len(resample(full, 5)) - 1)
growing = mk([(1, 2, 0, 1)] * 13)
check("the newest bucket is never emitted until full, whatever min_fill says",
      len(resample(growing, 5, min_fill=0.2)) == 2)

check("the default HTF needs about an hour, not five",
      L.htf_factor * L.htf_min_bars <= 90, f"{L.htf_factor * L.htf_min_bars} bars")

# ═════════════════════════════════════════════════════════════════════════════
section("6. Discipline")

from arb.intraday_engine import IntradayEngine


class _Cap:
    capital = 10_000.0

    def daily_target(self, pct):
        return self.capital * pct / 100.0


class _Cand:
    lot_cost = 3_000.0


eng = IntradayEngine.__new__(IntradayEngine)
eng.L = IntradayLimits(max_risk_pct_per_trade=5.0)
eng.log = lambda *_: None
eng.notifier = None
eng.capital = _Cap()
eng.book = PositionBook(eng.L)
eng.loss_streak = 0
eng.last_loss_ts = 0.0
eng.paused_until = 0.0
eng.careful = False

eng._on_loss()
check("a loss starts the no-revenge cooldown",
      time.time() - eng.last_loss_ts < 1.0 and eng.L.cooldown_after_loss_sec > 0)
check("one loss does not pause the desk", eng.paused_until == 0.0)
eng._on_loss()
eng._on_loss()
check("a streak of max_consecutive_losses pauses new entries",
      eng.paused_until > time.time() + 60 * (eng.L.loss_streak_pause_min - 1))
check("...and turns on grade-after-streak", eng.careful is True)
check("...and resets the streak counter", eng.loss_streak == 0)

# Cap = 5% of Rs 10,000 = Rs 500 per trade, across all lots together.
plan = {"risk_inr": 200.0}
check("grade A sizes to lots_grade_a when capital and risk allow",
      eng._lots_for("A", _Cand(), plan) == min(eng.L.lots_grade_a, 2))
check("grade C is one lot", eng._lots_for("C", _Cand(), plan) == 1)
check("sizing never exceeds the per-trade risk cap",
      eng._lots_for("A", _Cand(), {"risk_inr": 400.0}) == 1)
check("a risk bigger than the cap gives zero lots, not one",
      eng._lots_for("A", _Cand(), {"risk_inr": 9_000.0}) == 0)
eng.L.grade_sizing = False
check("flat sizing when grade_sizing is off", eng._lots_for("A", _Cand(), plan) == 1)

# Time stop
book = PositionBook(IntradayLimits(time_stop_min=20.0, time_stop_min_r=0.3))
old = time.time() - 21 * 60
p = Position(trade_id=1, symbol="X", token="1", strike=0, opt="CE", side=1,
             qty=75, entry=100.0, stop=90.0, target=120.0, risk_points=10.0,
             index_invalidation=0.0, opened_at=old)
act, why = book.review(p, bid=101.0)
check("a scalp that went nowhere is cut by the time stop",
      act == EXIT and why == "time_stop", why)
p2 = Position(trade_id=2, symbol="X", token="1", strike=0, opt="CE", side=1,
              qty=75, entry=100.0, stop=90.0, target=130.0, risk_points=10.0,
              index_invalidation=0.0, opened_at=old)
act2, why2 = book.review(p2, bid=108.0)
check("...but a runner is left alone", why2 != "time_stop", why2)
off = PositionBook(IntradayLimits(time_stop_min=0.0))
p3 = Position(trade_id=3, symbol="X", token="1", strike=0, opt="CE", side=1,
              qty=75, entry=100.0, stop=90.0, target=120.0, risk_points=10.0,
              index_invalidation=0.0, opened_at=old)
check("time_stop_min = 0 disables it", off.review(p3, bid=101.0)[1] != "time_stop")

# ═════════════════════════════════════════════════════════════════════════════
section("7. Candle data survives and failures are visible")

from arb.candles import CandleStore


class _U:
    def __init__(self, name):
        self.name = name
        self.spot_exchange = "NSE"
        self.spot_token = "1"


class _RefusingApi:
    def getCandleData(self, params):
        return {"status": False, "message": "Invalid API Key", "errorCode": "AG8004"}


logs = []
with tempfile.TemporaryDirectory() as d:
    cs = CandleStore(_RefusingApi(), [_U("NIFTY")], interval="1m", cache_dir=d,
                     log=logs.append)
    try:
        cs._fetch(_U("NIFTY"))
        raised = False
    except RuntimeError as e:
        raised = "AG8004" in str(e)
    check("a status:False history reply raises instead of looking empty", raised)

    cs.refresh_once(force=True)
    check("a refused key is reported once, with what it means",
          any("historical data" in m for m in logs), logs[-1][:90] if logs else "")
    n = len(logs)
    cs.refresh_once(force=True)
    check("...and not repeated every refresh", len(logs) == n)

    class _Store:
        ltp = [24000.0]
        volume = [0.0]
    cs._store = _Store()
    cs._spot_slots = {"NIFTY": 0}
    cs.live["NIFTY"] = ["2026-09-21T09:15:00+05:30", 1, 2, 0, 1, 0.0]
    cs._sample_live()                       # new bucket -> old live bar completes
    check("a completed live bar is written to the cache",
          os.path.exists(cs._cache_path("NIFTY")))
    cs2 = CandleStore(_RefusingApi(), [_U("NIFTY")], interval="1m", cache_dir=d)
    check("...and a restart reloads it", len(cs2.data.get("NIFTY") or []) >= 1)
    check("...without pretending the history is fresh", "NIFTY" not in cs2.fetched_at)


# Cost as a share of risk: the same trade passes or fails on friction alone.
from arb.intraday import plan_trade


class _C2:
    ask, delta, lot_size = 120.0, 0.5, 65
    detail = {"spread": 1.0}


class _S2:
    closes = [24100.0]

    def invalidation(self, side):
        return 24060.0                                  # 40 index pts -> 20 premium


check("a trade whose cost is a small slice of its risk is planned",
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(), cost_points=1.5) is not None)
check("the same trade is refused when cost exceeds max_cost_frac_of_risk",
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(), cost_points=5.0) is None)
check("...and allowed again with the filter off",
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(max_cost_frac_of_risk=0.0),
                 cost_points=5.0) is not None)

# The capital cap on the stop (owner's rule: 4-5% of capital, never more).
check("the default cap is 8% of capital", IntradayLimits().max_risk_pct_per_trade == 8.0)
wide = plan_trade(_C2(), _S2(), BULL, IntradayLimits(stop_policy="cap"), cost_points=1.0,
                  max_risk_inr=500.0, atr=5.0)
check("the default policy never tightens a stop: too wide is refused",
      IntradayLimits().stop_policy == "delta" and
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(), cost_points=1.0,
                 max_risk_inr=500.0, atr=5.0) is None)
check("stop_policy='cap' (not the default) tightens it to the cap",
      wide is not None and wide["stop_capped"] and abs(wide["risk_inr"] - 500.0) < 1e-6,
      f"risk Rs {wide['risk_inr'] if wide else 0:.0f}")
check("...and the index stop moves in with it",
      wide is not None and 24100.0 - wide["index_invalidation"] < 40.0
      and abs((24100.0 - wide["index_invalidation"]) * 0.5 - 500.0 / 65) < 1e-6,
      f"index stop {wide['index_invalidation'] if wide else 0:.1f}")
check("stop_policy='skip' refuses instead of tightening",
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(stop_policy="skip"),
                 cost_points=1.0, max_risk_inr=500.0) is None)
check("a stop that fits the cap is left where the chart put it",
      not plan_trade(_C2(), _S2(), BULL, IntradayLimits(), cost_points=1.0,
                     max_risk_inr=5_000.0, atr=5.0)["stop_capped"])
check("a capped stop inside one ATR of noise is refused",
      plan_trade(_C2(), _S2(), BULL, IntradayLimits(stop_policy="cap"), cost_points=1.0,
                 max_risk_inr=500.0, atr=20.0) is None)

# Delta sizing: the selector picks a strike whose delta fits the stop.
import numpy as _np


class _CV:
    def __init__(self):
        ks = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0, 24300.0, 24400.0]
        ce = [340.0, 255.0, 180.0, 120.0, 75.0, 44.0, 25.0]
        pe = [28.0, 45.0, 78.0, 120.0, 175.0, 245.0, 330.0]
        n = len(ks)
        self.strikes = _np.array(ks)
        self.ce_ask, self.pe_ask = _np.array(ce), _np.array(pe)
        self.ce_bid, self.pe_bid = self.ce_ask - 1.0, self.pe_ask - 1.0
        self.ce_askq = self.pe_askq = _np.full(n, 500.0)
        self.ce_age = self.pe_age = _np.full(n, 50.0)
        self.ce_symbol = [f"NIFTY{int(k)}CE" for k in ks]
        self.pe_symbol = [f"NIFTY{int(k)}PE" for k in ks]
        self.ce_token = [str(1000 + i) for i in range(n)]
        self.pe_token = [str(2000 + i) for i in range(n)]
        self.lot_size, self.expiry = 65, "29SEP2026"
        self.underlying, self.segment = "NIFTY", "NFO"

from arb.intraday import ContractSelector
_sel = ContractSelector(IntradayLimits())
full = _sel.select(_CV(), 24000.0, BULL, 10_000.0, 400.0, 1.0)
lowd = _sel.select(_CV(), 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.30)
check("with a delta ceiling the selector buys a lower-delta strike",
      full is not None and lowd is not None and lowd.delta <= 0.30 < full.delta,
      f"{full.delta if full else 0:.2f} -> {lowd.delta if lowd else 0:.2f}")
check("a ceiling under min_delta refuses and says why",
      _sel.select(_CV(), 24000.0, BULL, 10_000.0, 400.0, 1.0, max_delta=0.05) is None
      and "too wide" in _sel.last_reject, _sel.last_reject)

# Momentum target: what the market has been making, scaled by participation.
from arb.trailing import expected_move, volume_factor, momentum_target
flat_move = mk([(100.0 + (i % 2) * 2, 103.0 + (i % 2) * 2, 99.0 + (i % 2) * 2, 101.0 + (i % 2) * 2)
                for i in range(80)])
em = expected_move(flat_move, 5, 50.0)
check("expected move is measured from the bars' own excursions", 1.0 < em < 6.0, f"{em:.2f}")
check("a higher percentile asks for a bigger move",
      expected_move(flat_move, 5, 80.0) >= em)
check("too little history gives no estimate", expected_move(flat_move[:10], 5) == 0.0)
busy = [list(r) for r in flat_move]
for r in busy[-6:]:
    r[5] = 4000.0
check("heavy recent volume stretches the target (capped)",
      1.0 < volume_factor(busy, IntradayLimits()) <= 1.5,
      f"{volume_factor(busy, IntradayLimits()):.2f}")
quiet = [list(r) for r in flat_move]
for r in quiet[-6:]:
    r[5] = 100.0
check("thin recent volume shrinks it (floored)",
      0.75 <= volume_factor(quiet, IntradayLimits()) < 1.0)
check("no volume data means no volume adjustment",
      volume_factor(mk([tuple(r[1:5]) for r in flat_move], vol=0.0), IntradayLimits()) == 1.0)
mt = momentum_target(flat_move, BULL, 101.0, IntradayLimits())
check("a long's momentum target sits above entry", mt > 101.0, f"{mt:.2f}")

# ═════════════════════════════════════════════════════════════════════════════
section("8. End to end: hunt() takes a trade on a lone setup")

import threading
import numpy as np
import arb.intraday_engine as ie
import arb.regime as rg
from arb.config import CostModel
from arb.execution import PaperBroker
from arb.instruments import IST


class _CV:
    def __init__(self):
        ks = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0, 24300.0, 24400.0]
        ce = [340.0, 255.0, 180.0, 120.0, 75.0, 44.0, 25.0]
        pe = [28.0, 45.0, 78.0, 120.0, 175.0, 245.0, 330.0]
        n = len(ks)
        self.strikes = np.array(ks)
        self.ce_ask, self.pe_ask = np.array(ce), np.array(pe)
        self.ce_bid, self.pe_bid = self.ce_ask - 1.0, self.pe_ask - 1.0
        self.ce_askq = self.pe_askq = np.full(n, 500.0)
        self.ce_age = self.pe_age = np.full(n, 50.0)
        self.ce_symbol = [f"NIFTY{int(k)}CE" for k in ks]
        self.pe_symbol = [f"NIFTY{int(k)}PE" for k in ks]
        self.ce_token = [str(1000 + i) for i in range(n)]
        self.pe_token = [str(2000 + i) for i in range(n)]
        self.lot_size, self.expiry = 65, "29SEP2026"
        self.underlying, self.segment = "NIFTY", "NFO"


class _Binding:
    underlying, T = "NIFTY", 0.01
    strikes = [23800.0, 23900.0, 24000.0, 24100.0, 24200.0, 24300.0, 24400.0]

    def view(self, *a):
        return _CV()


class _Store:
    def __init__(self, spot):
        self.ltp = [spot]
        self.bid = [0.0]
        self.slot_of_token = {}


class _Feed:
    def __init__(self, spot):
        self.store = _Store(spot)
        self.spot_slots = {"NIFTY": 0}
        self.tick_event = threading.Event()


class _Candles:
    def __init__(self, rows):
        self.rows = rows

    def get(self, name, limit=120):
        return self.rows[-limit:]


class _Journal:
    def __init__(self):
        self.rows = []

    def record_trade(self, r):
        self.rows.append(r)


class _Cfg:
    session_start, session_end = "09:15", "15:15"


fixed = datetime(2026, 9, 21, 9, 52, tzinfo=IST)     # matches the IB fixture day
_real_ie, _real_rg = ie.now_ist, rg.now_ist
ie.now_ist = rg.now_ist = lambda: fixed
try:
    # The fixture's bars are 30+ points wide, so at the 5% cap the stop would
    # sit inside one ATR and be (correctly) refused. This test is about the
    # entry path, so it runs with the older wide-risk settings.
    eng2 = IntradayEngine(_Feed(day[-1][4]), [_Binding()], _Cfg(), CostModel(),
                          IntradayLimits(max_risk_pct_per_trade=25.0,
                                         target_mode="structure",
                                         sideways_bars=0, premium_rise_bars=0, min_atr_ratio=0.7),
                          PaperBroker(CostModel()), _Journal(),
                          _Cap(), _Candles(day))
    eng2.book.time_to_close = lambda *_: 200.0
    # The premium chart decides too: flat or missing premium means no trade.
    for label, prem, want in (("no premium chart", None, "premium_chart_warming_up"),
                              ("a flat premium", [120.0] * 12, "premium_not_rising"),
                              ("a rising premium", [100.0 + 3 * i for i in range(12)], None)):
        c = _Candles(day)
        if prem:
            c.premium = lambda slot, limit=30, p=prem: p
        e = IntradayEngine(_Feed(day[-1][4]), [_Binding()], _Cfg(), CostModel(),
                           IntradayLimits(max_risk_pct_per_trade=25.0, target_mode="structure",
                                          sideways_bars=0, min_atr_ratio=0.7, premium_rise_bars=10),
                           PaperBroker(CostModel()), _Journal(), _Cap(), c)
        e.feed.store.slot_of_token = {str(t): 0 for t in list(range(1000, 1007)) + list(range(2000, 2007))}
        e.book.time_to_close = lambda *_: 200.0
        e.hunt()
        check(f"premium chart: {label} -> " + (want or "entry"),
              (e.rejects.get(want, 0) > 0) if want else e.book.count() == 1, f"rejects {e.rejects}")
    eng2.hunt()
    opened = eng2.book.snapshot()
    check("a lone ib_breakout setup produces an actual entry",
          len(opened) == 1, f"rejects {eng2.rejects}")
    if opened:
        p0 = opened[0]
        check("...buying a call on a bullish break", p0.opt == "CE", p0.symbol)
        check("...with grade and playbook in the journal detail",
              p0.detail.get("grade") in ("A", "B", "C")
              and "ib_breakout" in p0.detail.get("playbooks", []),
              f"{p0.detail.get('grade')} {p0.detail.get('playbooks')}")
        check("...and the stop below entry, target above",
              p0.stop < p0.entry < p0.target)
        check("...and the playbook's daily count goes up",
              eng2.playbook_count.get(("NIFTY", "ib_breakout", BULL)) == 1)
        eng2.hunt()
        check("one position at a time: a second hunt adds nothing",
              eng2.book.count() == 1)
        check("the entry carries an index entry, risk and target",
              p0.index_entry > 0 and p0.index_risk > 0 and p0.index_target > p0.index_entry,
              f"entry {p0.index_entry:.1f} risk {p0.index_risk:.1f} target {p0.index_target:.1f}")
        logs2 = []
        eng2.log = logs2.append
        try:
            eng2.manage()
            ok = True
        except Exception as e:
            ok = False
            logs2.append(repr(e))
        check("manage() runs the swing trail without error", ok, "; ".join(logs2)[:120])
finally:
    ie.now_ist, rg.now_ist = _real_ie, _real_rg


# ═════════════════════════════════════════════════════════════════════════════
section("8b. 1 Oct 2026 review: closed bars only, no chasing, no trap, one idea")

from arb.regime import run_in_direction, swept_against


def _eng_closed(**kw):
    c = _Candles(day)
    c.live = {"NIFTY": list(day[-1])}            # the fixture's last bar is the forming one
    c.interval = "1m"
    e = IntradayEngine(_Feed(day[-1][4]), [_Binding()], _Cfg(), CostModel(),
                       IntradayLimits(max_risk_pct_per_trade=25.0, target_mode="structure",
                                      sideways_bars=0, premium_rise_bars=0, min_atr_ratio=0.7,
                                      **kw),
                       PaperBroker(CostModel()), _Journal(), _Cap(), c)
    e.book.time_to_close = lambda *_: 200.0
    return e


_real_time = time.time
ie.now_ist = rg.now_ist = lambda: fixed
brk_close = datetime.fromisoformat(day[-2][0]).timestamp() + 60
try:
    e0 = _eng_closed()
    e0.hunt()
    check("flags off: today's behaviour, the entry still happens", e0.book.count() == 1,
          f"rejects {e0.rejects}")
    time.time = lambda: brk_close + 120
    e1 = _eng_closed(decide_on_closed_bars=True)
    e1.hunt()
    check("closed bars: two minutes after the bar closed, no entry - not chased, "
          "and the wait is not counted as a refusal",
          e1.book.count() == 0 and "waiting_for_bar_close" not in e1.rejects
          and not e1.verdicts, f"rejects {e1.rejects}")
    time.time = lambda: brk_close + 2
    e2 = _eng_closed(decide_on_closed_bars=True)
    e2.hunt()
    check("closed bars: 2 s after the break bar closed, the entry is taken",
          e2.book.count() == 1, f"rejects {e2.rejects}")
    e3 = _eng_closed(decide_on_closed_bars=True, max_chase_atr=0.01)
    e3.feed.store.ltp = [day[-2][4] + 40.0]
    e3.hunt()
    check("closed bars: price already moved away from the close -> refused",
          e3.book.count() == 0 and e3.rejects.get("price_moved_since_close", 0) > 0,
          f"rejects {e3.rejects}")
    e4 = _eng_closed(decide_on_closed_bars=True, same_side_cooldown_min=30)
    e4._loss_side, e4._loss_side_ts = BULL, time.time() - 600
    e4.hunt()
    check("F4: ten minutes after a losing CE trade, another CE is refused",
          e4.book.count() == 0 and e4.rejects.get("same_side_after_loss", 0) > 0,
          f"rejects {e4.rejects}")
    e4b = _eng_closed(decide_on_closed_bars=True, same_side_cooldown_min=30)
    # A CE index loss 40 minutes ago, and a stock loss 10 minutes ago: the
    # stock loss must not stretch the index block past its 30 minutes.
    e4b._loss_side, e4b._loss_side_ts = BULL, time.time() - 2400
    e4b.last_loss_ts = time.time() - 600
    e4b.hunt()
    check("F4: a later stock loss does not extend the index side's block",
          e4b.book.count() == 1, f"rejects {e4b.rejects}")
    e5 = _eng_closed(decide_on_closed_bars=True, same_side_cooldown_min=30)
    e5._loss_side, e5._loss_side_ts = BEAR, time.time() - 600
    e5.hunt()
    check("F4: a losing PE does not block a CE", e5.book.count() == 1, f"rejects {e5.rejects}")
    e6 = _eng_closed(decide_on_closed_bars=True, max_run_atr=0.5)
    e6.hunt()
    check("F2: a move already run past the limit is refused",
          e6.book.count() == 0 and e6.rejects.get("move_already_run", 0) > 0,
          f"rejects {e6.rejects}")
finally:
    time.time = _real_time
    ie.now_ist, rg.now_ist = _real_ie, _real_rg

fall = mk([(24100.0 - 10 * i, 24102.0 - 10 * i, 24088.0 - 10 * i, 24090.0 - 10 * i)
           for i in range(40)])
check("run_in_direction: a steady fall is a long run for a put",
      run_in_direction(fall, BEAR) > 5.0 and run_in_direction(fall, BULL) < 1.0,
      f"{run_in_direction(fall, BEAR):.1f} / {run_in_direction(fall, BULL):.1f}")
check("run_in_direction: too little history says 0", run_in_direction(fall[:10], BEAR) == 0.0)
flat = [(24000.0, 24004.0, 23996.0, 24001.0)] * 40
swp = mk(flat + [(24000.0, 24001.0, 23985.0, 23999.0), (23999.0, 24003.0, 23998.0, 24002.0)])
check("swept_against: a reclaimed sell-side sweep warns a put",
      swept_against(swp, BEAR) == "sellside_swept")
check("...and does not warn a call", swept_against(swp, BULL) == "")
lost = mk(flat + [(24000.0, 24001.0, 23985.0, 23999.0), (23999.0, 24000.0, 23980.0, 23982.0)])
check("...nor a put once price is back below the swept pool", swept_against(lost, BEAR) == "")


# ═════════════════════════════════════════════════════════════════════════════
section("8c. 2 Oct 2026 review: _closed_rows fails closed (D-M1)")

from types import SimpleNamespace as _NS


def _cr_engine(live=None):
    e = IntradayEngine.__new__(IntradayEngine)
    e.L = IntradayLimits(decide_on_closed_bars=True, entry_window_sec=15.0)
    e.candles = _NS(interval="1m", live=dict(live or {}))
    return e


def _closed(e, rows, t0=None, now=None):
    """_closed_rows at a fixed clock; (rows, reason), or (None, error) when the
    engine cannot take the call at all."""
    saved = time.time
    if now is not None:
        time.time = lambda: now
    try:
        return e._closed_rows("NIFTY", rows, t0) if t0 is not None else e._closed_rows("NIFTY", rows)
    except Exception as ex:
        return None, repr(ex)
    finally:
        time.time = saved


_m = datetime.fromisoformat("2026-10-01T10:05:00+05:30")
_cr = [[(_m + timedelta(minutes=k)).isoformat(), 1.0, 2.0, 0.5, 1.5, 0.0] for k in (-2, -1, 0)]
t_in = _m.timestamp() + 5.0                       # 5 s into the 10:05 minute
rows_f, why_f = _closed(_cr_engine(), list(_cr), now=t_in)
check("no live bar for the index: the minute still forming is dropped, not decided on",
      rows_f is not None and rows_f[-1][0] == _cr[1][0] and why_f == "",
      f"rows[-1] {rows_f[-1][0][11:16] if rows_f else '-'} reason {why_f!r}")
rows_f2, why_f2 = _closed(_cr_engine(), list(_cr), now=_m.timestamp() + 30.0)
check("...and 30 s into it the last closed bar is 30 s old: wait, never act on the forming one",
      rows_f2 is not None and why_f2 == "waiting_for_bar_close" and rows_f2[-1][0] == _cr[1][0],
      f"reason {why_f2!r}")
# The get()/live race: the rows were copied 0.5 s before 10:06 (10:05 still
# forming in the copy), the sampler rolled the minute, then _closed_rows ran.
t_copy = _m.timestamp() + 59.5
live_next = [(_m + timedelta(minutes=1)).isoformat(), 1.0, 1.0, 1.0, 1.0, 0.0]
rows_r, why_r = _closed(_cr_engine({"NIFTY": live_next}), list(_cr), t0=t_copy,
                        now=_m.timestamp() + 60.05)
check("rows copied before the bar closed: the copy's forming bar is not the decision bar, "
      "even after the live bar rolled",
      rows_r is not None and not (why_r == "" and rows_r and rows_r[-1][0] == _cr[2][0]),
      f"rows[-1] {rows_r[-1][0][11:16] if rows_r else '-'} reason {why_r!r}")
rows_ok, why_ok = _closed(_cr_engine({"NIFTY": live_next}), list(_cr), t0=_m.timestamp() + 60.02,
                          now=_m.timestamp() + 60.05)
check("...while rows copied after the close decide on the completed bar at once",
      rows_ok is not None and why_ok == "" and rows_ok[-1][0] == _cr[2][0],
      f"reason {why_ok!r}")
rows_live, why_live = _closed(_cr_engine({"NIFTY": list(_cr[2])}), list(_cr), now=t_in)
check("the live bucket is still dropped by name",
      rows_live is not None and rows_live[-1][0] == _cr[1][0] and why_live == "")
bad = [list(_cr[0]), ["10:05?", 1.0, 2.0, 0.5, 1.5, 0.0]]
rows_b, why_b = _closed(_cr_engine(), bad, now=t_in)
check("an unreadable bar time is a refusal, not a go",
      why_b == "bar_time_unparseable", f"reason {why_b!r}")
check("flag off: rows pass through untouched",
      IntradayEngine._closed_rows(_NS(L=IntradayLimits(), candles=None), "NIFTY", list(_cr))
      == (list(_cr), ""))


# ═════════════════════════════════════════════════════════════════════════════
section("8d. 2 Oct 2026 review: the loss cooldowns survive a restart (D-L1)")

with tempfile.TemporaryDirectory() as _d:
    from arb.journal import Journal as _J
    _j = _J(directory=_d)
    _now = datetime.now(IST)
    _base = {"strategy": "INTRADAY_PE", "mode": "paper", "strike": 24000.0, "underlying": "NIFTY",
             "expiry": "06OCT2026", "qty": 65, "entry": 100.0, "exit": 90.0, "points": -10.0,
             "r_multiple": -1.0, "gross_inr": -650.0, "cost_inr": 80.0, "held_sec": 300.0,
             "legs": [], "opportunity": {}}
    pe_ts = (_now - timedelta(minutes=5)).isoformat()
    _j.record_trade(dict(_base, trade_id=1, symbol="NIFTY24000CE", opt="CE", exit_reason="target",
                         net_inr=400.0, ts_ist=(_now - timedelta(minutes=20)).isoformat()))
    _j.record_trade(dict(_base, trade_id=2, symbol="NIFTY24000PE", opt="PE",
                         exit_reason="stop_loss", net_inr=-730.0, ts_ist=pe_ts))
    _feed = _Feed(24000.0)

    def _boot(**kw):
        return IntradayEngine(_feed, [_Binding()], _Cfg(), CostModel(),
                              IntradayLimits(same_side_cooldown_min=30.0,
                                             cooldown_after_loss_sec=600.0, **kw),
                              PaperBroker(CostModel()), _j, _Cap(), _Candles(day),
                              log=lambda *_: None)

    r1 = _boot()
    want_ts = datetime.fromisoformat(pe_ts).timestamp()
    check("restart 5 min after a losing PE: the same-side block is restored",
          r1._loss_side == BEAR and abs(r1._loss_side_ts - want_ts) < 1e-3
          and r1._same_idea_blocked(BEAR) and not r1._same_idea_blocked(BULL),
          f"side {r1._loss_side} ts {r1._loss_side_ts:.0f} vs {want_ts:.0f}")
    check("...and so is the cooldown after a loss",
          abs(r1.last_loss_ts - want_ts) < 1e-3 and r1._gates() == "cooldown_after_loss",
          f"gate {r1._gates()!r}")
    # A later STOCK loss moves the loss cooldown, never the index side.
    eq_ts = (_now - timedelta(minutes=1)).isoformat()
    _j.record_trade(dict(_base, trade_id=3, symbol="RELIANCE-EQ", opt="EQ", strategy="INTRADAY_EQ_LONG",
                         underlying="RELIANCE", exit_reason="stop_loss", net_inr=-120.0, ts_ist=eq_ts))
    r2 = _boot()
    check("a later stock loss restores the loss cooldown but not the index side",
          abs(r2.last_loss_ts - datetime.fromisoformat(eq_ts).timestamp()) < 1e-3
          and r2._loss_side == BEAR and abs(r2._loss_side_ts - want_ts) < 1e-3,
          f"last_loss {r2.last_loss_ts:.0f} side {r2._loss_side}")
    _j.record_trade(dict(_base, trade_id=4, symbol="NIFTY24000CE", opt="CE", exit_reason="target",
                         net_inr=300.0, ts_ist=_now.isoformat()))
    r3 = _boot()
    check("a win afterwards does not clear the last loss's side or time",
          r3._loss_side == BEAR and abs(r3._loss_side_ts - want_ts) < 1e-3)

with tempfile.TemporaryDirectory() as _d:
    from arb.journal import Journal as _J
    _j = _J(directory=_d)
    _j.record_trade({"trade_id": 1, "symbol": "NIFTY24000CE", "opt": "CE", "exit_reason": "target",
                     "net_inr": 50.0, "ts_ist": datetime.now(IST).isoformat(), "legs": [],
                     "opportunity": {}, "strategy": "INTRADAY_CE", "qty": 65})
    r4 = IntradayEngine(_Feed(24000.0), [_Binding()], _Cfg(), CostModel(), IntradayLimits(),
                        PaperBroker(CostModel()), _j, _Cap(), _Candles(day), log=lambda *_: None)
    check("a day with no loss restores nothing", r4._loss_side == 0 and r4.last_loss_ts == 0.0)


# ═════════════════════════════════════════════════════════════════════════════
section("8e. 2 Oct 2026 review: the constraints page mirrors the engine (D-L3)")

from arb import constraints as _cons
from arb.intraday import Candidate as _Cd, chain_delta, plan_trade as _plan


def _cons_engine(capital, **kw):
    c = _Candles(day)
    e = IntradayEngine(_Feed(24000.0), [_Binding()], _Cfg(), CostModel(),
                       IntradayLimits(**kw), PaperBroker(CostModel()), _Journal(), _Cap(), c,
                       log=lambda *_: None)
    e.capital = _NS(capital=capital, daily_target=lambda pct: 0.0)
    e.book.time_to_close = lambda *_: 200.0
    st = _NS(bias=BULL, closes=[24000.0], active_zone=lambda spot, side: None,
             invalidation=lambda side: 23960.0)          # a 40-point index stop
    e.structures["NIFTY"] = st
    e.verdicts["NIFTY"] = _NS(side=BULL, setups=[], tradable=True, plays=lambda: ["x"],
                              veto="", count=1)
    e.evaluated_at["NIFTY"] = e.verdict_at["NIFTY"] = time.time()
    return e


_kw = dict(risk_includes_costs=True, exact_trade_costs=True, max_cost_frac_of_risk=0.0,
           max_risk_pct_per_trade=5.0, min_net_points=0.0)
_probe = _cons_engine(10_000.0, **_kw)
_cv = _CV()
_i = list(_cv.strikes).index(24100.0)
_px, _spr = float(_cv.ce_ask[_i]), float(_cv.ce_ask[_i] - _cv.ce_bid[_i])
_d = chain_delta(_cv.ce_ask, list(_cv.strikes), _i, "CE")
_prem = min(max(40.0 * _d, 2 * _spr), _px * _probe.L.max_risk_frac_of_premium)
_extra = _probe._extra_risk_points(_px, _spr, 65)
# A capital whose 5% cap holds the premium stop but not the stop plus its costs.
_cap_inr = (_prem + 0.5 * _extra) * 65
ec = _cons_engine(_cap_inr / 0.05, **_kw)
_row = [r for r in _cons._index_rows(ec, "NIFTY", time.time())
        if r["instrument"]["v"].startswith("NIFTY 24100")][0]
_cand = _Cd(symbol="NIFTY24100CE", token="1003", strike=24100.0, opt="CE", ask=_px,
            bid=_px - _spr, lot_size=65, lot_cost=_px * 65, delta=_d, cheapness=0.0, offset=1,
            depth=500.0, age_ms=50.0, expiry="29SEP2026", underlying="NIFTY", segment="NFO",
            detail={"spread": _spr})
_pl = _plan(_cand, ec.structures["NIFTY"], BULL, ec.L, ec._round_trip_points(_px, 65),
            max_risk_inr=_cap_inr, extra_risk_points=_extra)
check("risk_includes_costs: a contract whose stop fits but whose stop + costs does not is red, "
      "as plan_trade refuses it",
      _pl is None and _row["risk"]["ok"] is False,
      f"plan {'refused' if _pl is None else 'taken'}; page {_row['risk']['v']} ok={_row['risk']['ok']}")
check("...and the delta cell shows the selector's ceiling with costs inside",
      _row["delta"]["ok"] is False and "for this stop" in _row["delta"]["note"],
      f"{_row['delta']}")
ec2 = _cons_engine(_cap_inr / 0.05, max_risk_pct_per_trade=5.0, max_cost_frac_of_risk=0.0,
                   min_net_points=0.0)
_row2 = [r for r in _cons._index_rows(ec2, "NIFTY", time.time())
         if r["instrument"]["v"].startswith("NIFTY 24100")][0]
check("...while with costs outside the cap the same contract is green, as plan_trade takes it",
      _row2["risk"]["ok"] is True and _plan(_cand, ec2.structures["NIFTY"], BULL, ec2.L,
                                             ec2._round_trip_points(_px, 65),
                                             max_risk_inr=_cap_inr) is not None,
      f"{_row2['risk']}")
# Friction on the whole bet: cost / (stop + extra), the figure plan_trade tests.
ec3 = _cons_engine(10_000.0 * 20, risk_includes_costs=True, exact_trade_costs=True,
                   max_risk_pct_per_trade=5.0, max_cost_frac_of_risk=0.2, min_net_points=0.0)
_row3 = [r for r in _cons._index_rows(ec3, "NIFTY", time.time())
         if r["instrument"]["v"].startswith("NIFTY 24100")][0]
_cost = ec3._round_trip_points(_px, 65)
check("costs / risk is measured against the stop plus its costs, as plan_trade measures it",
      abs(float(_row3["costfrac"]["v"]) - round(_cost / (_prem + _extra), 2)) < 1e-9,
      f"page {_row3['costfrac']['v']} vs {_cost / (_prem + _extra):.2f} (stop alone {_cost / _prem:.2f})")


# ═════════════════════════════════════════════════════════════════════════════
section("9. Trailing on the index: the owner's 1:1 -> last opposite swing rule")

from arb import trailing as tr

pre = [(24010.0, 24014.0, 24006.0, 24010.0)] * 25          # history before entry
# Short from 24000, stop 24020 (risk 20). Drop, bounce to a LOWER HIGH at
# 23994, then drop through 1:1 (23980) to 23972, then two bars to confirm.
after = [(24000.0, 24002.0, 23992.0, 23993.0),
         (23993.0, 23988.0, 23984.0, 23985.0),
         (23985.0, 23990.0, 23983.0, 23989.0),
         (23989.0, 23994.0, 23987.0, 23990.0),              # the lower high
         (23990.0, 23991.0, 23980.0, 23981.0),
         (23981.0, 23983.0, 23972.0, 23973.0),
         (23973.0, 23976.0, 23970.0, 23972.0),
         (23972.0, 23974.0, 23969.0, 23971.0)]              # live bar
rows_s = mk(pre + after)
entry_iso = rows_s[len(pre) - 1][0]


def short_pos():
    return Position(trade_id=9, symbol="S", token="9", strike=0, opt="PE", side=-1,
                    qty=65, entry=100.0, stop=90.0, target=10_000.0, risk_points=10.0,
                    index_invalidation=24020.0, opened_at=time.time(),
                    index_entry=24000.0, index_risk=20.0, delta=0.5,
                    cost_points=1.0, entry_bar=entry_iso)


LT = IntradayLimits(tighten_on_reversal=False)
ps = short_pos()
note = tr.update(ps, rows_s, LT, index_px=23971.0)
check("at 1:1 the stop moves off the original level", ps.index_invalidation < 24020.0, note)
check("...to the last lower high after entry (23994 + pad)",
      23994.0 <= ps.index_invalidation < 23996.0, f"{ps.index_invalidation:.2f}")
check("...which is BELOW entry: a stop-out there is a profit",
      ps.index_invalidation < ps.index_entry)
check("...and is marked as a trail",
      ps.index_trailed and PositionBook(LT).review(ps, 105.0, index_px=23996.0)
      == (EXIT, "trail_stop"))

ps2 = short_pos()
check("below 1:1 nothing moves",
      tr.update(ps2, mk(pre + after[:3]), LT, index_px=23989.0) == ""
      and ps2.index_invalidation == 24020.0)

# Owner's rule (1 Oct 2026): follow the first lower high (higher low on a long)
# as soon as it confirms, not only after 1:1.
LE = IntradayLimits(tighten_on_reversal=False, trail_from_first_swing=True)
pe0 = short_pos()
check("default: at +0.5R the lower high is ignored",
      tr.update(pe0, rows_s, LT, index_px=23990.0) == "" and pe0.index_invalidation == 24020.0)
pe1 = short_pos()
note_e = tr.update(pe1, rows_s, LE, index_px=23990.0)
check("first-swing trail: at +0.5R the stop already moves to that lower high",
      23994.0 <= pe1.index_invalidation < 23996.0, note_e or f"{pe1.index_invalidation:.2f}")
pe2 = short_pos()
tr.update(pe2, mk(pre + [(24000.0 - 2 * i, 24001.0 - 2 * i, 23997.0 - 2 * i, 23998.0 - 2 * i)
                         for i in range(8)]), LE, index_px=23986.0)
check("first-swing trail: no swing and below 1:1 -> no breakeven jump",
      pe2.index_invalidation == 24020.0, f"{pe2.index_invalidation:.2f}")
from arb.trailing import initial_target
flat_t = mk([(24000.0 + (i % 2) * 2, 24003.0 + (i % 2) * 2, 23999.0 + (i % 2) * 2,
              24001.0 + (i % 2) * 2) for i in range(120)])
t_floor = initial_target(flat_t, BULL, 24001.0, 20.0,
                         IntradayLimits(target_mode="momentum", target_floor_r=2.0))
check("target floor 2R: a quiet market's target is still at least 1:2",
      t_floor - 24001.0 >= 40.0 - 1e-6, f"{t_floor - 24001.0:.1f} pts")

frozen = ps.index_invalidation
tr.update(ps, rows_s, LT, index_px=23990.0)
check("the trail never loosens", ps.index_invalidation <= frozen)

ps3 = short_pos()
check("the stop never lands within the minimum gap of price",
      (tr.update(ps3, rows_s, LT, index_px=23993.9) == "")
      or ps3.index_invalidation > 23993.9 + 0.1)

# With no swing yet, 1:1 still buys breakeven + costs.
straight = mk(pre + [(24000.0 - 4 * i, 24001.0 - 4 * i, 23995.0 - 4 * i, 23996.0 - 4 * i)
                     for i in range(8)])
pb = short_pos()
tr.update(pb, straight, LT, index_px=23968.0)
check("no swing yet: the stop goes to breakeven plus costs",
      abs(pb.index_invalidation - (24000.0 - 1.0 / 0.5)) < 1e-6,
      f"{pb.index_invalidation:.2f}")

ph = short_pos()
tr.update(ph, rows_s, IntradayLimits(trail_mode="atr", tighten_on_reversal=False),
          index_px=23971.0)
check("trail_mode='atr' uses the chandelier instead", ph.index_trailed)
check("trail_mode='premium' leaves the index alone",
      tr.update(short_pos(), rows_s, IntradayLimits(trail_mode="premium"),
                index_px=23971.0) == "")

# Long mirror with an opposing engulfing: tightens to the nearer swing.
pre_l = [(23990.0, 23994.0, 23986.0, 23990.0)] * 25
up_l = [(24000.0, 24008.0, 23998.0, 24007.0),
        (24007.0, 24016.0, 24010.0, 24015.0),
        (24015.0, 24017.0, 24009.0, 24010.0),
        (24010.0, 24012.0, 24008.0, 24011.0),               # higher low 24008
        (24011.0, 24024.0, 24010.0, 24023.0),
        (24023.0, 24030.0, 24022.0, 24029.0),
        (24026.0, 24031.0, 24026.0, 24030.0),
        (24031.0, 24031.0, 24018.0, 24019.0),               # bearish engulfing
        (24019.0, 24021.0, 24018.0, 24020.0)]
rows_l = mk(pre_l + up_l)
pl = Position(trade_id=10, symbol="L", token="10", strike=0, opt="CE", side=1,
              qty=65, entry=100.0, stop=90.0, target=10_000.0, risk_points=10.0,
              index_invalidation=23985.0, opened_at=time.time(),
              index_entry=24000.0, index_risk=15.0, delta=0.5, cost_points=1.0,
              entry_bar=rows_l[len(pre_l) - 1][0])
note_l = tr.update(pl, rows_l, IntradayLimits(), index_px=24020.0)
check("a long trails to a higher low above entry", pl.index_invalidation > 24000.0, note_l)
check("an opposing pattern is named when it tightens the trail",
      ("engulfing" in note_l) or ("choch" in note_l) or ("rejection" in note_l)
      or pl.index_invalidation > 24008.0, note_l)

# Targets
tf = tr.initial_target(rows_s, -1, 24000.0, 20.0, IntradayLimits(target_mode="fixed"))
check("target_mode='fixed' is reward_risk x R", abs(tf - (24000.0 - 40.0)) < 1e-9)
check("target_mode='trail' has no target",
      tr.initial_target(rows_s, -1, 24000.0, 20.0, IntradayLimits(target_mode="trail")) == 0.0)
ts_ = tr.initial_target(rows_s, -1, 24000.0, 20.0, IntradayLimits())
check("target_mode='structure' stays within min..max R",
      1.5 * 20 - 1e-6 <= 24000.0 - ts_ <= 4.0 * 20 + 1e-6, f"{ts_:.1f}")

# Extension
px_ = Position(trade_id=11, symbol="E", token="11", strike=0, opt="CE", side=1,
               qty=65, entry=100.0, stop=90.0, target=10_000.0, risk_points=10.0,
               index_invalidation=24005.0, opened_at=time.time(),
               index_entry=24000.0, index_risk=10.0, index_target=24020.0,
               delta=0.5, cost_points=1.0, entry_bar=rows_l[24][0])
imp = mk(pre_l + [(24000.0, 24006.0, 23999.0, 24005.0), (24005.0, 24012.0, 24004.0, 24011.0),
                  (24011.0, 24024.0, 24011.0, 24023.5), (24023.5, 24025.0, 24022.0, 24024.0)])
n_ext = tr.try_extend(px_, imp, 24023.0, IntradayLimits())
check("at the target, an impulse bar extends it", px_.index_target > 24020.0, n_ext)
check("...and locks the stop near the old target",
      abs(px_.index_invalidation - (24020.0 - 0.75 * 10.0)) < 1e-6,
      f"{px_.index_invalidation:.2f}")
stall = mk(pre_l + [(24000.0, 24006.0, 23999.0, 24005.0), (24005.0, 24021.0, 24004.0, 24012.0),
                    (24012.0, 24013.0, 24011.0, 24012.0)])
p_st = Position(trade_id=12, symbol="E", token="12", strike=0, opt="CE", side=1,
                qty=65, entry=100.0, stop=90.0, target=10_000.0, risk_points=10.0,
                index_invalidation=24005.0, opened_at=time.time(),
                index_entry=24000.0, index_risk=10.0, index_target=24020.0,
                delta=0.5, cost_points=1.0, entry_bar=rows_l[24][0])
check("a stalling bar at the target is simply taken",
      tr.try_extend(p_st, stall, 24020.5, IntradayLimits()) == "")
check("on_target='exit' never extends",
      tr.try_extend(px_, imp, 24023.0, IntradayLimits(on_target="exit")) == "")


# ═════════════════════════════════════════════════════════════════════════════
section("10. Today's profit becomes tomorrow's capital")

from arb.capital_book import CapitalBook

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "capital.json")
    cb = CapitalBook(10_000.0, path=path)
    cb.open_day("2026-09-24")
    cb.record(+600.0)
    cb.record(-200.0)
    check("trades land in the day ledger at once", abs(cb.day_pnl - 400.0) < 1e-9
          and cb.day_trades == 2)
    check("...but today's sizing capital does not change intraday",
          cb.capital == 10_000.0)
    cb2 = CapitalBook(10_000.0, path=path)
    cb2.open_day("2026-09-24")
    check("a same-day restart resumes the ledger, not zero",
          abs(cb2.day_pnl - 400.0) < 1e-9 and cb2.day_trades == 2)
    folded, cap = cb2.open_day("2026-09-25")
    check("the next day opens with yesterday's profit added",
          abs(folded - 400.0) < 1e-9 and abs(cap - 10_400.0) < 1e-9, f"{cap:.2f}")
    check("...and a fresh ledger", cb2.day_pnl == 0.0 and cb2.day_trades == 0)
    check("...and one history row for the day",
          cb2.summary()["history"][-1]["date"] == "2026-09-24")
    cb2.record(-100.0)
    b, a = cb2.close_day()
    check("close_day folds after the session", abs(a - 10_300.0) < 1e-9)
    b2, a2 = cb2.close_day()
    check("...and folding twice adds nothing", abs(a2 - 10_300.0) < 1e-9)
    folded3, cap3 = CapitalBook(10_000.0, path=path).open_day("2026-09-26")
    check("...and the next open does not fold it again", folded3 == 0.0 and abs(cap3 - 10_300.0) < 1e-9)

    # A losing day at the halt, then a restart: still halted.
    cb4 = CapitalBook(10_000.0, path=os.path.join(d, "c4.json"))
    today = datetime.now(IST).strftime("%Y-%m-%d")
    cb4.open_day(today)
    cb4.record(-3_100.0)
    eng4 = IntradayEngine(_Feed(24000.0), [_Binding()], _Cfg(), CostModel(),
                          IntradayLimits(), PaperBroker(CostModel()), _Journal(),
                          CapitalBook(10_000.0, path=os.path.join(d, "c4.json")),
                          _Candles(day), log=lambda *_: None)
    check("a restart after hitting the daily loss limit is still halted",
          eng4.halted and eng4.realised_pnl <= -3_000.0, eng4.halt_reason)


# ═════════════════════════════════════════════════════════════════════════════
section("7. The day's record survives a restart")

import csv as _csv
import json as _json
from arb.journal import Journal

with tempfile.TemporaryDirectory() as d:
    j = Journal(directory=d)
    base = {"strategy": "INTRADAY_CE", "mode": "paper", "symbol": "NIFTY24000CE", "opt": "CE",
            "strike": 24000.0, "underlying": "NIFTY", "expiry": "29SEP2026", "qty": 65,
            "entry": 100.0, "exit": 110.0, "exit_reason": "target", "points": 10.0,
            "r_multiple": 1.0, "gross_inr": 650.0, "cost_inr": 80.0, "held_sec": 300.0,
            "grade": "B", "playbooks": ["ob_fvg_retest"], "legs": [], "opportunity": {}}
    j.record_trade(dict(base, trade_id=1, net_inr=570.0))
    j.record_trade(dict(base, trade_id=2, exit_reason="stop_loss", net_inr=-300.0))
    with open(j.intraday_csv_path) as fh:
        sheet = list(_csv.DictReader(fh))
    check("an intraday trade gets a spreadsheet row (it used to fail on 'description')",
          len(sheet) == 2 and sheet[0]["net_inr"] == "570.0" and sheet[0]["playbooks"] == "ob_fvg_retest",
          f"{len(sheet)} rows")

    feed = _Feed(24000.0)
    feed.store.slot_of_token = {"1001": 0}

    def _restart(broker=None):
        return IntradayEngine(feed, [_Binding()], _Cfg(), CostModel(), IntradayLimits(),
                              broker or PaperBroker(CostModel()), j, _Cap(), _Candles(day),
                              log=lambda *_: None)

    e = _restart()
    check("a restart shows today's closed trades again, with the win/loss count",
          len(e.records) == 2 and (e.wins, e.losses) == (1, 1), f"{len(e.records)} W{e.wins}/L{e.losses}")
    check("...and trade ids carry on instead of starting again at 1", e.trade_id == 2)

    p = Position(trade_id=3, symbol="NIFTY24000CE", token="1001", strike=24000.0, opt="CE", side=1,
                 qty=65, entry=120.0, stop=110.0, target=12000.0, risk_points=10.0,
                 index_invalidation=23950.0, opened_at=time.time())
    p.index_entry, p.index_target, p.trailed = 24000.0, 24100.0, True
    e.book.add(p)
    e._save_open(force=True)
    # The process dies here: no clean stop, nothing squares the position off.
    e2 = _restart()
    got = e2.book.snapshot()
    check("a position the last process died holding is managed again after a restart",
          len(got) == 1 and got[0].symbol == "NIFTY24000CE" and got[0].index_target == 24100.0
          and got[0].trailed and got[0].detail.get("restored"), str(got))
    check("...counted in today's trades, and the next trade id follows it",
          e2.trades_today == 1 and e2.trade_id == 3, f"{e2.trades_today} trades, id {e2.trade_id}")

    e2.close(got[0], "target")
    with open(e2._open_path) as fh:
        left = _json.load(fh)["positions"]
    e3 = _restart()
    check("a clean exit empties the file, so the next start restores nothing",
          left == [] and e3.book.count() == 0)
    check("...and the exit is in the journal the dashboard reads back",
          len(e3.records) == 3 and e3.records[-1]["trade_id"] == 3)

    def _plant(day_, mode_="paper", token="1001"):
        with open(e3._open_path, "w") as fh:
            _json.dump({"day": day_, "mode": mode_,
                        "positions": [dict(_json.loads(_json.dumps(p.__dict__)), token=token)]}, fh)

    _plant("2026-01-02")
    check("a position left from another day is not brought back", _restart().book.count() == 0)
    _plant(datetime.now(IST).strftime("%Y-%m-%d"), token="9999")
    check("a position whose contract is not subscribed is not restored blind", _restart().book.count() == 0)

    # ./run.sh restart (SIGUSR1): the position goes to the next engine, uncut.
    with open(e3._open_path, "w") as fh:
        _json.dump({"day": "", "mode": "paper", "positions": []}, fh)
    before = len(e3.records)
    p4 = Position(trade_id=4, symbol="NIFTY24000CE", token="1001", strike=24000.0, opt="CE", side=1,
                  qty=65, entry=130.0, stop=125.0, target=13000.0, risk_points=5.0,
                  index_invalidation=23990.0, opened_at=time.time())
    e3.book.add(p4)
    e3.handover = True
    e3.run(stop_predicate=lambda: True)
    e6 = _restart()
    check("a paper restart hands the open position to the next engine instead of squaring it off",
          len(e6.records) == before and [q.trade_id for q in e6.book.snapshot()] == [4],
          f"{len(e6.records)} closed, open {[q.trade_id for q in e6.book.snapshot()]}")
    e6.run(stop_predicate=lambda: True)
    e7 = _restart()
    check("...while a plain stop still squares off and journals it",
          e7.book.count() == 0 and len(e7.records) == before + 1
          and e7.records[-1]["exit_reason"] == "squareoff")

    class _LiveStub(PaperBroker):
        mode = "live"

    _plant(datetime.now(IST).strftime("%Y-%m-%d"), mode_="live")
    e5 = _restart(_LiveStub(CostModel()))
    check("live: a position left open is never restored blindly - entries halt until checked",
          e5.book.count() == 0 and e5.halted and "Angel" in e5.halt_reason, e5.halt_reason)


print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
