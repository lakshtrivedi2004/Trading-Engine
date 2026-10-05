#!/usr/bin/env python3
"""The noise-area breakout shadow (arb/noise_breakout.py, arb/noise_sigma.py,
IntradayEngine._shadow_noise).

What these prove, in order:

  1. The pure rule: sigma by bar END minute from the previous sessions only,
     the band, the signal's sides, the stop clamp, the grid and the window,
     the ATR from PRIOR bars, the pace INCLUDING the bar, the trail that only
     tightens and only on the grid. research/noise_parity.py checks the same
     functions bar for bar against the year's backtest.
  2. The sigma history: fetched once, off the hunt() thread, today excluded,
     cached so a restart spends no call; a failure is logged once and retried
     no sooner than every 10 minutes.
  3. The engine: a shadow trade is noted and journalled and followed to its
     stop - and no order method is ever called; "on" is off; an exception
     inside never leaves hunt(); a restart resumes the open shadow trade.

Arithmetic on constructed inputs only: no network, no market, no login.
"""
import json
import math
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta

import numpy as np

from arb import noise_breakout as nb
from arb.config import CostModel, IntradayLimits
from arb.instruments import IST
from arb.noise_sigma import SigmaHistory

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


def bars(day, closes, start="09:15", first_open=None, wick=1.0):
    """1-minute rows: each bar opens at the previous close."""
    t0 = datetime.fromisoformat(f"{day}T{start}:00+05:30")
    out, prev = [], (closes[0] if first_open is None else first_open)
    for i, c in enumerate(closes):
        o = prev
        out.append([(t0 + timedelta(minutes=i)).isoformat(), o, max(o, c) + wick, min(o, c) - wick, c, 0.0])
        prev = c
    return out


def sessions_before(day, n):
    """The n weekday non-holiday sessions before `day`, oldest first."""
    from arb.instruments import holidays
    d, out = date.fromisoformat(day), []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays():
            out.append(d.isoformat())
    return out[::-1]


def quiet_day(day, base=24000.0, amp=0.0008):
    return bars(day, [base * (1 + amp * math.sin(i / 10.0)) for i in range(375)], first_open=base)


# ═════════════════════════════════════════════════════════════════════════════
section("1a. sigma(t): the previous sessions only, by bar END minute")

d1 = bars("2026-09-28", [100.0, 101.0, 99.0], first_open=100.0)
d2 = bars("2026-09-29", [200.0, 196.0, 204.0], first_open=200.0)
d3 = bars("2026-09-30", [50.0, 50.5, 49.0], first_open=50.0)
sig = nb.sigma_profile({"2026-09-28": d1, "2026-09-29": d2, "2026-09-30": d3}, ndays=2, min_days=2)
check("keyed by the bar's END minute (09:15 bar -> 09:16)", sorted(sig) == [556, 557, 558], str(sorted(sig)))
check("the mean of |close / that day's first open - 1| over the LAST ndays sessions",
      abs(sig[557] - np.mean([0.02, 0.01])) < 1e-12 and abs(sig[558] - np.mean([0.02, 0.02])) < 1e-12,
      f"{sig[557]:.4f} {sig[558]:.4f}")
check("fewer sessions than min_days -> no profile",
      nb.sigma_profile({"2026-09-29": d2, "2026-09-30": d3}, ndays=14, min_days=3) == {})
check("the end minute of a bar starting 14:44 is 14:45 (885)", nb.end_minute("2026-10-01T14:44:00+05:30") == 885)
check("the 30-minute grid runs from 09:15: 09:45, 10:15 ... on it, 10:00 off it",
      nb.on_grid(nb.hhmm("09:45")) and nb.on_grid(nb.hhmm("10:15")) and not nb.on_grid(nb.hhmm("10:00")))
check("in_session keeps 09:15 - 15:29 bar starts only",
      len(nb.in_session(bars("2026-10-01", [1.0] * 3, start="15:28"))) == 2)

# ═════════════════════════════════════════════════════════════════════════════
section("1b. ATR from PRIOR bars; pace including the bar")

flat = bars("2026-10-01", [100.0] * 260, wick=1.0)          # TR 2 everywhere
spike = [r[:] for r in flat]
spike[-1][2], spike[-1][3] = 130.0, 70.0                       # the decision bar: TR 60
check("prior_atr ignores the decision bar itself (screen.prep atr[i])",
      abs(nb.prior_atr(spike, len(spike) - 1) - 2.0) < 1e-12, f"{nb.prior_atr(spike, len(spike) - 1)}")
check("prior_atr needs period + 1 prior bars", nb.prior_atr(flat, 20) == 0.0 and nb.prior_atr(flat, 21) > 0)
check("pace on a flat tape is 1.0", abs(nb.pace_ratio(flat) - 1.0) < 1e-12)
check("pace INCLUDES the decision bar (capped at 2.0)", nb.pace_ratio(spike) == 2.0)
check("pace unknown (0) with fewer than 222 bars", nb.pace_ratio(flat[:221]) == 0.0 and nb.pace_ratio(flat[:222]) > 0)
check("pace gate by half: before 12:15 the first floor, after it the second",
      nb.pace_ok(1.4, nb.hhmm("12:14"), (1.3, 1.5)) and not nb.pace_ok(1.4, nb.hhmm("12:15"), (1.3, 1.5)))
check("pace gate: unknown pace and a zero floor pass", nb.pace_ok(0.0, 600, (1.3, 1.5)) and nb.pace_ok(0.9, 600, (0, 0)))

# ═════════════════════════════════════════════════════════════════════════════
section("1c. The band and the signal")

today_up = bars("2026-10-01", [24000.0 + 2 * i for i in range(60)], first_open=24000.0)   # 09:15 .. 10:14
prev = quiet_day("2026-09-30")
rows = prev + today_up
sig14 = {t: 0.001 for t in range(556, 931)}
o, pc = 24000.0, prev[-1][4]
ub, lb = nb.bounds(o, pc, 0.001)
check("bounds: max(o, pc) x (1 + vm s) and min(o, pc) x (1 - vm s)",
      ub == max(o, pc) * 1.001 and lb == min(o, pc) * 0.999)
tw = nb.twap(today_up)
check("band long = max(ub, twap), short = min(lb, twap)",
      nb.band(today_up, 1, o, pc, sig14) == max(ub, tw) and nb.band(today_up, -1, o, pc, sig14) == min(lb, tw))
s = nb.signal(rows, pc, sig14)
check("a close above ub on the 10:15 grid bar is a long", s["side"] == 1 and s["t"] == nb.hhmm("10:15"), str(s.get("why")))
check("the stop is the band when the band is further than minr ATRs",
      s["side"] == 1 and s["stop"] == min(s["band"], s["close"] - 1.5 * s["atr"]) and s["stop"] == s["band"])
tight = nb.signal(rows, pc, sig14, minr=40.0)
check("...and minr x ATR when that is further (the clamp)",
      tight["side"] == 1 and abs(tight["stop"] - (tight["close"] - 40.0 * tight["atr"])) < 1e-9
      and tight["stop"] < tight["band"])
today_dn = bars("2026-10-01", [24000.0 - 2 * i for i in range(60)], first_open=24000.0)
sd = nb.signal(prev + today_dn, pc, sig14)
check("a close below lb is a short, its stop above the close",
      sd["side"] == -1 and sd["stop"] > sd["close"] and sd["stop"] == max(sd["band"], sd["close"] + 1.5 * sd["atr"]))
flat_today = bars("2026-10-01", [pc] * 60, first_open=pc)
check("inside the noise area -> no side", nb.signal(prev + flat_today, pc, sig14)["why"] == "inside_noise_area")
check("off the grid (10:14 end) -> no decision", nb.signal(rows[:-1], pc, sig14)["why"] == "off_grid")
early = bars("2026-10-01", [24000.0 + 5 * i for i in range(30)], first_open=24000.0)   # ends 09:45
check("09:45 is the first grid point in the window",
      nb.signal(prev + early, pc, sig14)["side"] == 1)
check("09:15 + 30 = 09:45 with lo 10:15 is outside the window",
      nb.signal(prev + early, pc, sig14, lo="10:15")["why"] == "outside_window")
check("no sigma at that minute -> no decision", nb.signal(rows, pc, {})["why"] == "no_sigma")
late_open = bars("2026-10-01", [24000.0 + 2 * i for i in range(59)], start="09:16", first_open=24000.0)
check("require_open: today's 09:15 bar missing -> refused",
      nb.signal(prev + late_open, pc, sig14, require_open="09:15")["why"] == "open_bar_missing")

# ═════════════════════════════════════════════════════════════════════════════
section("1d. The trail")

DAY_1D = "2026-10-01"

st0 = {"side": 1, "stop": 24050.0}
nxt = bars("2026-10-01", [24130.0], start="10:15", first_open=24118.0)
day_ = today_up + nxt
st1 = nb.trail_step(st0, day_, o, pc, sig14)
check("an off-grid bar above the stop changes nothing", "exit" not in st1 and st1["stop"] == 24050.0)
hit = bars("2026-10-01", [24040.0], start="10:15", first_open=24060.0)
st2 = nb.trail_step(st0, today_up + hit, o, pc, sig14)
check("a bar whose low reaches the stop exits at min(open, stop)",
      st2.get("exit", {}).get("reason") == "stop" and st2["exit"]["px"] == 24050.0)
gap = bars("2026-10-01", [24030.0], start="10:15", first_open=24040.0)
st3 = nb.trail_step(st0, today_up + gap, o, pc, sig14)
check("...and at the open when it gaps through", st3["exit"]["px"] == 24040.0)
# Climb to the 10:45 grid bar; the band (twap) is now above the 24050 stop.
climb = bars("2026-10-01", [24120.0 + 3 * i for i in range(30)], start="10:15", first_open=24118.0)
state, path = nb.trail(climb, today_up, 1, 24050.0, o, pc, sig14)
check("on the grid the stop ratchets up to the band", len(path) == 1 and path[0][0][11:16] == "10:44"
      and abs(path[0][1] - nb.band(today_up + climb, 1, o, pc, sig14)) < 1e-9, str(path))
state_hi, path_hi = nb.trail(climb, today_up, 1, 24110.0, o, pc, sig14)
check("a band below the current stop leaves the stop where it is (only tighter)",
      "exit" not in state_hi and not path_hi and state_hi["stop"] == 24110.0, f"{state_hi['stop']}")
# On a GRID bar the stop is checked against the OLD stop before the ratchet
# (screen.exit_walk): a 10:44 bar (ends 10:45) whose low lies between the old
# stop and the band exits at the old stop, the stop unmoved.
pre44 = today_up + climb[:29]                                       # 09:15 .. 10:43
g44 = [[f"{DAY_1D}T10:44:00+05:30", 24210.0, 24215.0, 24045.0, 24212.0, 0.0]]
band44 = nb.band(pre44 + g44, 1, o, pc, sig14)
st_g = nb.trail_step({"side": 1, "stop": 24050.0}, pre44 + g44, o, pc, sig14)
check("grid bar, long: the stop is hit on the OLD stop before any ratchet",
      band44 > 24050.0 > 24045.0 and st_g.get("exit", {}).get("reason") == "stop"
      and st_g["exit"]["px"] == min(24210.0, 24050.0) and st_g["stop"] == 24050.0 and "moved" not in st_g,
      f"band {band44:.1f} exit {st_g.get('exit')}")
fall = bars(DAY_1D, [23880.0 - 3 * i for i in range(29)], start="10:15", first_open=today_dn[-1][4])
pre44s = today_dn + fall
g44s = [[f"{DAY_1D}T10:44:00+05:30", 23790.0, 23955.0, 23785.0, 23788.0, 0.0]]
band44s = nb.band(pre44s + g44s, -1, o, pc, sig14)
st_gs = nb.trail_step({"side": -1, "stop": 23950.0}, pre44s + g44s, o, pc, sig14)
check("grid bar, short: the mirror - exit at max(open, old stop), stop unmoved",
      band44s < 23950.0 <= 23955.0 and st_gs.get("exit", {}).get("reason") == "stop"
      and st_gs["exit"]["px"] == max(23790.0, 23950.0) and st_gs["stop"] == 23950.0 and "moved" not in st_gs,
      f"band {band44s:.1f} exit {st_gs.get('exit')}")
st_e = nb.trail_step({"side": 1, "stop": 1.0}, today_up + nxt, o, pc, sig14, last=nb.hhmm("10:16"))
check("with `last` the bar ending there is an eod exit at its close",
      st_e.get("exit", {}).get("reason") == "eod" and st_e["exit"]["px"] == 24130.0)
sh_state = nb.trail_step({"side": -1, "stop": 24100.0}, today_up + nxt, o, pc, sig14)
check("a short is stopped by the bar's high", sh_state.get("exit", {}).get("reason") == "stop")

# ═════════════════════════════════════════════════════════════════════════════
section("2. The sigma history: off-thread, once, cached, today excluded, failures rationed")

DAY = "2026-10-01"
HIST_DAYS = sessions_before(DAY, 16)
HISTORY = [r for d in HIST_DAYS for r in quiet_day(d)] + bars(DAY, [99999.0] * 5)   # + today: must be dropped


class _U:
    spot_exchange, spot_token = "NSE", "99926000"


calls = []


def fake_fetch(api, params):
    calls.append((threading.current_thread().name, dict(params)))
    return {"status": True, "data": [list(r) for r in HISTORY]}


_cache = tempfile.mkdtemp(prefix="nb_sigma_")
h = SigmaHistory(lambda: "API", {"NIFTY": _U()}, 14, 10, 28, cache_dir=_cache, log=lambda *_: None,
                 fetch=fake_fetch)
check("ensure() returns at once and starts a worker", h.ensure(DAY, ["NIFTY"]) is True)
h._thread.join(5)
check("the history call ran off the calling thread",
      len(calls) == 1 and calls[0][0] != threading.current_thread().name, str([c[0] for c in calls]))
p = calls[0][1]
check("ONE_MINUTE on the spot token, ending yesterday",
      p["interval"] == "ONE_MINUTE" and p["symboltoken"] == "99926000" and p["exchange"] == "NSE"
      and p["todate"].startswith("2026-09-30") and p["fromdate"].startswith("2026-09-03"), str(p))
prof = h.get(DAY, "NIFTY")
check("profile from the last 14 sessions, today never in it",
      prof is not None and prof["days"] == HIST_DAYS[-14:] and DAY not in prof["days"]
      and prof["prev_day"] == HIST_DAYS[-1], str(prof and prof["days"][-2:]))
exp = nb.sigma_profile(nb.by_day([r for d in HIST_DAYS[-14:] for r in quiet_day(d)]), 14, 10)
check("...equal to sigma_profile of those sessions", prof["sigma"] == exp)
check("cached on disk under <date>_<index>.json", os.path.exists(os.path.join(_cache, f"{DAY}_NIFTY.json")))
check("a second ensure() spends nothing", h.ensure(DAY, ["NIFTY"]) is False and len(calls) == 1)


def boom(api, params):
    raise RuntimeError("must not be called")


h2 = SigmaHistory(lambda: "API", {"NIFTY": _U()}, 14, 10, 28, cache_dir=_cache, log=lambda *_: None, fetch=boom)
h2.ensure(DAY, ["NIFTY"]); h2._thread.join(5)
check("a restart reads the cache: no call, same profile",
      h2.calls == 0 and h2.get(DAY, "NIFTY") and h2.get(DAY, "NIFTY")["sigma"] == prof["sigma"]
      and h2.get(DAY, "NIFTY")["source"] == "cache")

lg_f = []
h3 = SigmaHistory(lambda: "API", {"NIFTY": _U()}, 14, 10, 28, cache_dir=tempfile.mkdtemp(), log=lg_f.append,
                  fetch=lambda a, p_: {"status": False, "message": "Access denied", "errorCode": "AB1004"})
h3.ensure(DAY, ["NIFTY"], now=1000.0); h3._thread.join(5)
check("a refused fetch: not ready, said once", h3.get(DAY, "NIFTY") is None
      and sum("sigma history unavailable" in x for x in lg_f) == 1, str(lg_f))
check("...not retried inside 10 minutes", h3.ensure(DAY, ["NIFTY"], now=time.time() + 300) is False)
started = h3.ensure(DAY, ["NIFTY"], now=time.time() + 601)
h3._thread.join(5)
check("...retried after 10 minutes, and the same error is not logged again",
      started is True and h3.calls == 2 and sum("sigma history unavailable" in x for x in lg_f) == 1)
h4 = SigmaHistory(lambda: "API", {"NIFTY": _U()}, 14, 10, 28, cache_dir=tempfile.mkdtemp(), log=lambda *_: None,
                  fetch=lambda a, p_: {"status": True, "data": [list(r) for r in HISTORY[-375 * 5:]]})
h4.ensure(DAY, ["NIFTY"]); h4._thread.join(5)
check("too few sessions is a failure, not an empty profile",
      h4.get(DAY, "NIFTY") is None and "only" in h4.status(DAY, "NIFTY"), h4.status(DAY, "NIFTY"))

# ═════════════════════════════════════════════════════════════════════════════
section("3. The engine: shadow only - noted, journalled, followed, never an order")

import arb.intraday_engine as ie
import arb.positions as _pos_mod
import arb.regime as rg
from arb.execution import PaperBroker
from arb.intraday_engine import IntradayEngine


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
        self.lot_size, self.expiry = 65, "06OCT2026"
        self.underlying, self.segment = "NIFTY", "NFO"


class _Binding:
    underlying, T, expiry = "NIFTY", 0.01, "06OCT2026"
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
    interval = "1m"

    def __init__(self, rows):
        self.rows = rows
        self.api = "FAKE-API"
        self.underlyings = {"NIFTY": _U()}
        self.live = {}

    def get(self, name, limit=120):
        return self.rows[-limit:]


class _Journal:
    def __init__(self, d):
        self.dir = d
        self.rows = []

    def record_trade(self, r):
        self.rows.append(r)


class _Cfg:
    session_start, session_end = "09:15", "15:15"


class _Cap:
    capital = 10_000.0

    def daily_target(self, pct):
        return self.capital * pct / 100.0


ORDERS = []


class _NoOrders(PaperBroker):
    """Every order path records itself and refuses."""
    def place_structure(self, *a, **k):
        ORDERS.append(("place_structure", a)); raise AssertionError("order sent")

    def cancel_order(self, *a, **k):
        ORDERS.append(("cancel_order", a)); raise AssertionError("cancel sent")

    def cancel_working(self, *a, **k):
        ORDERS.append(("cancel_working", a)); raise AssertionError("cancel sent")


_real = (time.time, ie.now_ist, _pos_mod.now_ist, rg.now_ist)


def at(hh, mm, ss, day=DAY):
    dt = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:{ss:02d}+05:30")
    time.time = lambda: dt.timestamp()
    ie.now_ist = _pos_mod.now_ist = rg.now_ist = lambda: dt


LIVE_PREV = [r for d in HIST_DAYS[-5:] for r in quiet_day(d)]       # the live store's ~5 sessions


def engine(rows, mode="shadow", jd=None, lg=None, fetch=fake_fetch, cache=None, **kw):
    lg = [] if lg is None else lg
    lim = dict(noise_breakout=mode, nb_indices=("NIFTY",), max_risk_pct_per_trade=25.0,
               trend_4h_bars=5, risk_includes_costs=True, exact_trade_costs=True)
    lim.update(kw)
    e = IntradayEngine(_Feed(rows[-1][4]), [_Binding()], _Cfg(), CostModel(),
                       IntradayLimits(**lim),
                       _NoOrders(CostModel()), _Journal(jd or tempfile.mkdtemp(prefix="nb_j_")), _Cap(),
                       _Candles(rows), log=lg.append)
    e.book.time_to_close = lambda *_: 200.0
    if mode == "shadow":
        hist = e._nb_history()
        hist.fetch, hist.cache_dir = fetch, cache or tempfile.mkdtemp(prefix="nb_c_")
    return e, lg


try:
    jd = tempfile.mkdtemp(prefix="nb_shadow_")
    cache_e = tempfile.mkdtemp(prefix="nb_cache_e_")
    calls.clear()
    pre = LIVE_PREV + bars(DAY, [24000.0 + 2 * i for i in range(5)], first_open=24000.0)
    e, lg = engine(pre, jd=jd, cache=cache_e)
    at(9, 20, 2)
    e._shadow_noise()
    e._nb_hist._thread.join(5)
    check("the engine's history uses the candle store's spot token and api, off-thread",
          len(calls) == 1 and calls[0][0] == "noise-sigma" and calls[0][1]["symboltoken"] == "99926000",
          str(calls[:1]))
    check("built from the config: 14 sessions, need 10",
          e._nb_hist.ndays == 14 and e._nb_hist.min_days == 10 and e._nb_hist.get(DAY, "NIFTY") is not None)

    # 10:15: the 10:14 bar closed 2 s ago, far above the noise area.
    e.candles.rows = LIVE_PREV + today_up
    e.feed.store.ltp = [today_up[-1][4]]
    at(10, 15, 2)
    e._shadow_noise()
    would = [x for x in lg if "WOULD BUY" in x]
    check("10:15: a breakout is noted as a CE it would buy, NO ORDER SENT",
          len(would) == 1 and "CE" in would[0] and "NO ORDER SENT" in would[0], str(lg[-3:]))
    sh = e._nb["idx"]["NIFTY"]["open"]
    prof_e = e._nb_hist.get(DAY, "NIFTY")
    ref = nb.signal(e.candles.rows, LIVE_PREV[-1][4], prof_e["sigma"], 1.0, 1.5, "09:45", "14:45", 30)
    check("...with the pure rule's side and stop", sh and sh["side"] == ref["side"] == 1
          and abs(sh["index_stop"] - ref["stop"]) < 1e-9, f"{sh and sh['index_stop']} vs {ref.get('stop')}")
    check("...the previous close from the history fetch (the live store stops at 15:15)",
          sh["pc_source"] == "history" and sh["pc"] == prof_e["prev_close"], f"{sh['pc_source']}")
    check("...the gates recorded (4h trend, pace against the per-index half floor)",
          sh["gates"]["pace"] == round(nb.pace_ratio(e.candles.rows), 4) and "trend_ok" in sh["gates"]
          and sh["gates"]["pace_need"] == e.L.min_atr_ratio, str(sh["gates"]))
    check("...whether the live engine holds a position, and F4",
          sh["live_position_open"] is False and sh["f4_blocked"] is False)
    check("...a contract picked at the per-trade cap, its premium stop for the record",
          sh["symbol"] and sh["symbol"].endswith("CE") and sh["cap_inr"] == 2500.0 and sh["prem_stop"] > 0,
          f"{sh['symbol']} {sh['cap_inr']} {sh.get('prem_stop')}")
    jl = os.path.join(jd, f"shadow_{DAY}.jsonl")
    recs = [json.loads(x) for x in open(jl)] if os.path.exists(jl) else []
    check("the open is journalled as a noise_breakout shadow line",
          len(recs) == 1 and recs[0]["playbook"] == "noise_breakout" and recs[0]["event"] == "open"
          and recs[0]["shadow"] is True)
    e._shadow_noise()
    check("the same grid bar is decided once", sum("WOULD BUY" in x for x in lg) == 1)

    # 10:16 - 10:19: drifting, then the 10:19 bar's low goes through the stop.
    stop0 = sh["index_stop"]
    walk = bars(DAY, [24120.0, 24118.0, 24110.0, stop0 - 5.0], start="10:15", first_open=today_up[-1][4])
    e.candles.rows = LIVE_PREV + today_up + walk
    e.feed.store.ltp = [stop0 + 20.0]          # the live index already back above: the BAR decides
    e.feed.store.slot_of_token = {sh["token"]: 0}
    e.feed.store.bid = [sh["entry"] - 12.0]
    at(10, 18, 30)                             # the 10:18 bar is still forming
    e._shadow_noise()
    check("no exit before the stop bar has closed", e._nb["idx"]["NIFTY"]["open"] is not None, str(lg[-2:]))
    at(10, 19, 1)
    e._shadow_noise()
    ex = [x for x in lg if "would EXIT" in x]
    check("the 10:18 bar's low through the stop exits, at the bid, with a net after exact costs",
          len(ex) == 1 and "index_stop" in ex[0] and "net Rs" in ex[0] and "NO ORDER SENT" in ex[0], str(ex))
    recs = [json.loads(x) for x in open(jl)]
    last = recs[-1]
    check("the exit is journalled with its bar, the stop path, best / worst bid",
          last["event"] == "exit" and last["exit_bar"][11:16] == "10:18" and last["exit_seen"] == "bar"
          and last["best_bid"] == last["worst_bid"] == sh["entry"] - 12.0 and last["stop_path"]
          and last["net"] < 0, f"{last.get('exit_bar')} {last.get('net')}")
    check("no order method was ever called, no position, no trade counted",
          ORDERS == [] and e.book.count() == 0 and e.trades_today == 0, str(ORDERS))

    # A restart while a shadow trade is open picks it up from the journal.
    jd2 = tempfile.mkdtemp(prefix="nb_resume_")
    e3, lg3 = engine(LIVE_PREV + today_up, jd=jd2, cache=cache_e)
    at(10, 15, 2)
    e3._shadow_noise(); e3._nb_hist._thread and e3._nb_hist._thread.join(5)
    e3._shadow_noise()
    check("(a second engine opened its own shadow trade)", e3._nb["idx"]["NIFTY"]["open"] is not None, str(lg3[-2:]))
    e4, lg4 = engine(LIVE_PREV + today_up + walk[:2], jd=jd2, cache=cache_e)
    at(10, 17, 5)
    e4._shadow_noise()
    check("a restart resumes the open shadow trade from the journal, without a new entry",
          e4._nb["idx"]["NIFTY"]["open"] is not None and any("resumed" in x for x in lg4)
          and not any("WOULD BUY" in x for x in lg4), str(lg4[-2:]))

    # The live index through the stop mid-bar exits at once (tick), and an
    # open shadow trade is closed at the square-off.
    sh4 = e4._nb["idx"]["NIFTY"]["open"]
    e4._nb_hist._thread and e4._nb_hist._thread.join(5)
    e4.feed.store.slot_of_token = {sh4["token"]: 0}
    e4.feed.store.ltp = [sh4["index_stop"] - 1.0]
    e4.feed.store.bid = [sh4["entry"] - 9.0]
    at(10, 17, 20)
    e4._shadow_noise()
    ex4 = [x for x in lg4 if "would EXIT" in x]
    r4 = [json.loads(x) for x in open(os.path.join(jd2, f"shadow_{DAY}.jsonl"))][-1]
    check("the live index through the stop exits mid-bar, at the bid",
          len(ex4) == 1 and r4["event"] == "exit" and r4["exit_seen"] == "tick"
          and r4["exit_bar"][11:16] == "10:17" and r4["exit"] == sh4["entry"] - 9.0, str(ex4))
    at(10, 45, 2)
    e4.candles.rows = LIVE_PREV + today_up + [
        r for r in bars(DAY, [24118.0 + 4 * i for i in range(30)], start="10:15", first_open=24118.0)]
    e4.feed.store.ltp = [e4.candles.rows[-1][4]]
    e4._shadow_noise()
    sh5 = e4._nb["idx"]["NIFTY"]["open"]
    check("after the exit the next grid bar (10:45) is decided again", sh5 is not None
          and sh5["t"] == "10:45", str(lg4[-1:]))
    e4.feed.store.slot_of_token = {sh5["token"]: 0}
    e4.feed.store.bid = [sh5["entry"] * 1.6]   # past +1R: the premium risk is at most half the premium
    e4.book.time_to_close = lambda *_: 0.8
    at(15, 14, 15)
    e4._shadow_noise()
    r5 = [json.loads(x) for x in open(os.path.join(jd2, f"shadow_{DAY}.jsonl"))][-1]
    check("an open shadow trade is closed at the square-off with a positive net",
          r5["event"] == "exit" and r5["why"] == "squareoff" and r5["net"] > 0, f"{r5.get('why')} {r5.get('net')}")
    check("...the premium rules' +1R breakeven noted for the record, not acted on",
          r5.get("prem_trailed") is True and "prem_exit" not in r5 and r5["exit"] == sh5["entry"] * 1.6
          and r5["net_with_premium_rules"] == r5["net"], f"{r5.get('prem_trailed')} {r5.get('prem_live_stop')}")

    # A stop with no option quote to exit at: recorded after a minute, not held all day.
    e10, lg10 = engine(LIVE_PREV + today_up, cache=cache_e)
    at(10, 15, 2)
    e10._shadow_noise(); e10._nb_hist._thread and e10._nb_hist._thread.join(5)
    e10._shadow_noise()
    sh10 = e10._nb["idx"]["NIFTY"]["open"]
    e10.feed.store.ltp = [sh10["index_stop"] - 2.0]
    e10.feed.store.bid = [0.0]
    e10.feed.store.slot_of_token = {sh10["token"]: 0}
    at(10, 15, 30)
    e10._shadow_noise()
    check("a stop with no bid waits for a quote", e10._nb["idx"]["NIFTY"]["open"] is not None)
    at(10, 16, 40)
    e10._shadow_noise()
    check("...and after a minute is recorded without one",
          e10._nb["idx"]["NIFTY"]["open"] is None
          and any("would EXIT (index_stop_no_quote" in x for x in lg10), str(lg10[-1:]))

    # A stale grid bar (closed 40 s ago) is not decided.
    e5, lg5 = engine(LIVE_PREV + today_up, cache=cache_e)
    at(10, 15, 40)
    e5._shadow_noise(); e5._nb_hist._thread and e5._nb_hist._thread.join(5)
    e5._shadow_noise()
    check("a grid bar seen after entry_window_sec is not decided",
          not any("WOULD BUY" in x for x in lg5) and any("too late" in x for x in lg5), str(lg5[-2:]))

    # "on" is off, said once; "off" is silent.
    for mode in ("on", "off"):
        calls.clear()
        e6, lg6 = engine(LIVE_PREV + today_up, mode=mode)
        at(10, 15, 2)
        e6._shadow_noise(); e6._shadow_noise()
        if mode == "on":
            check("noise_breakout 'on' is treated as off and said once",
                  sum("not supported" in x for x in lg6) == 1 and not any("WOULD BUY" in x for x in lg6)
                  and "_nb_hist" not in e6.__dict__ and calls == [], str(lg6))
        else:
            check("noise_breakout 'off': nothing at all", not any("[shadow]" in x for x in lg6)
                  and "_nb_hist" not in e6.__dict__)
    check("the dataclass default is off", IntradayLimits().noise_breakout == "off")

    # A holiday: no fetch, nothing noted.
    calls.clear()
    e7, lg7 = engine(LIVE_PREV + today_up)
    at(10, 15, 2, day="2026-10-02")
    e7._shadow_noise()
    check("a holiday: no history call, nothing noted", calls == [] and not any("[shadow]" in x for x in lg7))

    # Exceptions inside never leave hunt(), and are logged once.
    e8, lg8 = engine(LIVE_PREV + today_up, cache=cache_e)
    at(10, 15, 2)

    def _raise(*a, **k):
        raise ValueError("boom")

    e8._nb_index = _raise
    e8._shadow_noise()
    e8._shadow_noise()
    check("an error for one index is caught inside, logged once",
          sum("boom" in x for x in lg8) == 1, str(lg8))
    e8._shadow_noise = _raise
    e8._gates = lambda: "halted: test"
    ok = True
    try:
        e8.hunt(); e8.hunt()
    except Exception as ex_:
        ok = False
    check("an error out of _shadow_noise never leaves hunt(), logged once",
          ok and sum("noise_breakout error" in x for x in lg8) == 1, str(lg8[-2:]))
    e9, lg9 = engine(LIVE_PREV + today_up, cache=cache_e)
    e9.candles.get = _raise
    e9._gates = lambda: "halted: test"
    ok = True
    try:
        e9.hunt()
    except Exception:
        ok = False
    check("a candle store that raises does not break hunt()", ok)
    check("still no order anywhere", ORDERS == [])

    # ── review fixes (5 Oct 2026) ───────────────────────────────────────────
    def opened(e, hh=10, mm=15, ss=2):
        at(hh, mm, ss)
        e._shadow_noise(); e._nb_hist._thread and e._nb_hist._thread.join(5)
        e._shadow_noise()
        return e._nb["idx"]["NIFTY"]["open"]

    def jlines(e):
        f = os.path.join(e.journal.dir, f"shadow_{DAY}.jsonl")
        return [json.loads(x) for x in open(f)] if os.path.exists(f) else []

    # The still-forming live bar is dropped even when the clock is past its close.
    e11, lg11 = engine(LIVE_PREV + today_up, cache=cache_e)
    e11.candles.live = {"NIFTY": list(today_up[-1])}
    at(10, 15, 0)
    e11._shadow_noise(); e11._nb_hist._thread and e11._nb_hist._thread.join(5)
    e11._shadow_noise()
    check("a grid bar still in candles.live (not yet rolled) is not decided on",
          not any("WOULD BUY" in x for x in lg11) and e11._nb["idx"]["NIFTY"]["open"] is None, str(lg11[-2:]))
    e11.candles.live = {}
    at(10, 15, 1)
    e11._shadow_noise()
    check("...and once it has rolled it is", sum("WOULD BUY" in x for x in lg11) == 1, str(lg11[-2:]))

    # The candle store's previous close when it holds a later session than the history.
    short_hist = [r for r in HISTORY if r[0][:10] != HIST_DAYS[-1]]
    e12, lg12 = engine(LIVE_PREV + today_up, cache=tempfile.mkdtemp(prefix="nb_c12_"),
                       fetch=lambda a, p_: {"status": True, "data": [list(r) for r in short_hist]})
    sh12 = opened(e12)
    check("history a session behind the store: pc from the candles (pc_source 'candles')",
          sh12 is not None and sh12["pc_source"] == "candles" and sh12["pc"] == LIVE_PREV[-1][4]
          and sh12["prev_day"] == HIST_DAYS[-2], f"{sh12 and (sh12['pc_source'], sh12['prev_day'])}")

    # Per-index pace halves: the first-half floor before 12:15, the second after.
    e13, _ = engine(LIVE_PREV + today_up, cache=cache_e, atr_by_index={"NIFTY": (1.3, 1.5)})
    sh13 = opened(e13)
    check("atr_by_index halves: 10:15 needs the first-half floor 1.3",
          sh13 is not None and sh13["gates"]["pace_need"] == 1.3, str(sh13 and sh13["gates"]))
    up_noon = bars(DAY, [24000.0 + 2 * i for i in range(210)], first_open=24000.0)     # 09:15 .. 12:44
    e14, _ = engine(LIVE_PREV + up_noon, cache=cache_e, atr_by_index={"NIFTY": (1.3, 1.5)})
    sh14 = opened(e14, 12, 45, 2)
    check("...12:45 needs the second-half floor 1.5", sh14 is not None and sh14["t"] == "12:45"
          and sh14["gates"]["pace_need"] == 1.5, str(sh14 and sh14["gates"]))

    # A trade whose gates pass is marked so (4h trend with it, no pace floor).
    _t4 = ie.trend_4h
    ie.trend_4h = lambda rows, n=5: 1
    try:
        e15, lg15 = engine(LIVE_PREV + today_up, cache=cache_e, atr_by_index={"NIFTY": (0.0, 0.0)})
        sh15 = opened(e15)
    finally:
        ie.trend_4h = _t4
    check("4h trend with the side and the pace floor met: GATES PASS, gates.all true",
          sh15 is not None and sh15["gates"]["all"] is True and sh15["gates"]["trend_ok"] is True
          and any("GATES PASS" in x for x in lg15), str(sh15 and sh15["gates"]))

    # A resume replays a grid ratchet from the candles.
    jd16 = tempfile.mkdtemp(prefix="nb_resume16_")
    e16, _ = engine(LIVE_PREV + today_up, jd=jd16, cache=cache_e)
    sh16 = opened(e16)
    e17, lg17 = engine(LIVE_PREV + today_up + climb, jd=jd16, cache=cache_e)     # 10:15 .. 10:44, rising
    at(10, 45, 5)
    e17._shadow_noise(); e17._nb_hist._thread and e17._nb_hist._thread.join(5)
    e17._shadow_noise()
    r17 = e17._nb["idx"]["NIFTY"]["open"]
    want17 = nb.band(today_up + climb, 1, sh16["o"], sh16["pc"], e17._nb_hist.get(DAY, "NIFTY")["sigma"])
    check("a restart rebuilds the trail: the 10:44 grid bar ratchets the resumed stop to the band",
          r17 is not None and r17.get("resumed") and len(r17["stop_path"]) == 2
          and abs(r17["index_stop"] - want17) < 1e-9 and r17["index_stop"] > sh16["index_stop0"],
          f"{r17 and r17['index_stop']} vs {want17}")

    # An exit that waits for a quote past a grid close: that grid bar is still decided.
    e18, lg18 = engine(LIVE_PREV + today_up, cache=cache_e)
    sh18 = opened(e18)
    s0 = sh18["index_stop"]
    up42 = bars(DAY, [24120.0 + 3 * i for i in range(28)], start="10:15", first_open=today_up[-1][4])
    c42 = up42[-1][4]
    b43 = [f"{DAY}T10:43:00+05:30", c42, c42 + 2.0, s0 - 5.0, c42 + 1.0, 0.0]       # low through the stop
    b44 = [f"{DAY}T10:44:00+05:30", c42 + 1.0, c42 + 9.0, c42, c42 + 8.0, 0.0]      # the grid bar, far above
    e18.feed.store.slot_of_token = {sh18["token"]: 0}
    e18.feed.store.bid = [0.0]
    e18.feed.store.ltp = [c42 + 1.0]
    e18.candles.rows = LIVE_PREV + today_up + up42 + [b43]
    at(10, 44, 1)
    e18._shadow_noise()
    check("(the 10:43 bar's stop waits for a quote)", e18._nb["idx"]["NIFTY"]["open"] is not None
          and e18._nb["idx"]["NIFTY"]["open"]["exit_due"]["bar"][11:16] == "10:43")
    e18.candles.rows = LIVE_PREV + today_up + up42 + [b43, b44]
    at(10, 45, 0)
    e18._shadow_noise()
    check("(still waiting when the 10:44 grid bar arrives)", e18._nb["idx"]["NIFTY"]["open"]["id"] == sh18["id"])
    at(10, 45, 2)
    e18._shadow_noise()
    sh18b = e18._nb["idx"]["NIFTY"]["open"]
    ref18 = nb.signal(e18.candles.rows, sh18["pc"], e18._nb_hist.get(DAY, "NIFTY")["sigma"])
    check("the no-quote exit is recorded and the 10:45 grid bar decided on the same pass (screen.gen i > busy)",
          any("index_stop_no_quote" in x for x in lg18) and sh18b is not None and sh18b["t"] == "10:45"
          and ref18["side"] == 1, str(lg18[-2:]))

    # The square-off with no bid: recorded before / at the session end, never lost.
    for label, hh, mm, ss in (("6 s before the end", 15, 14, 55), ("after the end (phase closed)", 15, 15, 2)):
        e19, lg19 = engine(LIVE_PREV + today_up, cache=cache_e)
        sh19 = opened(e19)
        del e19.book.time_to_close                  # the real clock-based one
        e19.feed.store.slot_of_token = {sh19["token"]: 0}
        e19.feed.store.bid = [0.0]
        at(15, 14, 1)
        e19._shadow_noise()
        at(15, 14, 40)
        e19._shadow_noise()
        waiting = e19._nb["idx"]["NIFTY"]["open"] is not None
        at(hh, mm, ss)
        e19._shadow_noise()
        last19 = jlines(e19)[-1]
        check(f"square-off with bid 0: waits for a quote, recorded {label}",
              waiting and e19._nb["idx"]["NIFTY"]["open"] is None and last19["event"] == "exit"
              and last19["why"] == "squareoff_no_quote" and last19["exit_bar"][11:16] == "15:14",
              f"{waiting} {last19.get('event')} {last19.get('why')}")
    # A restart after the close with a shadow trade left open in the journal closes it.
    jd20 = tempfile.mkdtemp(prefix="nb_late_")
    e20, _ = engine(LIVE_PREV + today_up, jd=jd20, cache=cache_e)
    opened(e20)
    e21, lg21 = engine(LIVE_PREV + today_up, jd=jd20, cache=cache_e)
    del e21.book.time_to_close
    at(15, 40, 0)
    e21._shadow_noise()
    last21 = jlines(e21)[-1]
    check("a start after the close resumes the open shadow trade and records its exit",
          last21["event"] == "exit" and last21["why"] == "squareoff_no_quote" and last21.get("resumed") is True,
          f"{last21.get('event')} {last21.get('why')}")
    at(15, 41, 0)
    e21._shadow_noise()
    check("...once", sum(1 for r in jlines(e21) if r["event"] == "exit") == 1)

    # A missing minute: the trail holds, then goes on marked; the late bar is still checked.
    e22, lg22 = engine(LIVE_PREV + today_up, cache=cache_e)
    sh22 = opened(e22)
    s22 = sh22["index_stop"]
    m15 = bars(DAY, [24125.0], start="10:15", first_open=today_up[-1][4])
    m16 = [f"{DAY}T10:16:00+05:30", 24125.0, 24126.0, s22 - 3.0, 24124.0, 0.0]     # through the stop
    m17 = bars(DAY, [24128.0, 24130.0, 24131.0, 24133.0, 24134.0, 24136.0], start="10:17", first_open=24124.0)
    e22.feed.store.ltp = [24130.0]
    e22.candles.rows = LIVE_PREV + today_up + m15 + m17[:1]                       # 10:16 missing
    at(10, 18, 2)
    e22._shadow_noise()
    o22 = e22._nb["idx"]["NIFTY"]["open"]
    check("a missing minute holds the trail (last bar stays before the hole)",
          o22 is not None and o22["last_bar"][11:16] == "10:15" and not o22.get("gaps"), str(o22 and o22["last_bar"]))
    e22.candles.rows = LIVE_PREV + today_up + m15 + m17
    at(10, 23, 2)
    e22._shadow_noise()
    o22 = e22._nb["idx"]["NIFTY"]["open"]
    check("...after nb_gap_hold_sec it goes on, the minute listed in 'gaps'",
          o22 is not None and o22["last_bar"][11:16] == "10:22" and [g[11:16] for g in o22.get("gaps", [])] == ["10:16"]
          and any("missing" in x for x in lg22), str(o22 and (o22["last_bar"], o22.get("gaps"))))
    e22.candles.rows = LIVE_PREV + today_up + m15 + [m16] + m17                   # the refresh fills it
    e22.feed.store.slot_of_token = {o22["token"]: 0}
    e22.feed.store.bid = [o22["entry"] - 7.0]
    at(10, 24, 1)                                                                 # the next minute's copy
    e22._shadow_noise()
    last22 = jlines(e22)[-1]
    check("...and the late-filled bar through the stop is the exit, on its own bar",
          e22._nb["idx"]["NIFTY"]["open"] is None and last22["event"] == "exit"
          and last22["exit_seen"] == "late_bar" and last22["exit_bar"][11:16] == "10:16"
          and last22["gaps"], f"{last22.get('exit_seen')} {last22.get('exit_bar')}")
    e23, _ = engine(LIVE_PREV + today_up, cache=cache_e)
    opened(e23)
    e23.feed.store.ltp = [24130.0]
    e23.candles.rows = LIVE_PREV + today_up + m15 + m17[:1]
    at(10, 18, 2)
    e23._shadow_noise()
    e23.candles.rows = LIVE_PREV + today_up + m15 + [m16[:4] + [24124.0, 0.0]] + m17
    e23.candles.rows[-7][3] = 24120.0                                               # 10:16 filled, no stop
    at(10, 19, 1)
    e23._shadow_noise()
    o23 = e23._nb["idx"]["NIFTY"]["open"]
    check("a minute filled within the hold: walked normally, no 'gaps' mark",
          o23 is not None and o23["last_bar"][11:16] == "10:18" and not o23.get("gaps"), str(o23 and o23["last_bar"]))
    check("still no order anywhere after the review cases", ORDERS == [])
finally:
    time.time, ie.now_ist, _pos_mod.now_ist, rg.now_ist = _real

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
