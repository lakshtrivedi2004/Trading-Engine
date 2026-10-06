#!/usr/bin/env python3
"""Every stock watched every minute, and the stock's own option (shadow).

arb/stockspike.py (spike, pick_stock_option), the desk's spike watch and NSE
movers (arb/stockdesk.py), the scan's filter funnel (arb/scanner.py), NSE's
option chain parsing (arb/nse.py) and the engine's stock-option shadow, which
never sends an order. No network: fakes only.
"""
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb.config import CostModel, IntradayLimits
from arb.instruments import IST
from arb.scanner import DailyStat, rank
from arb.stockspike import pick_stock_option, spike
import arb.stockdesk as sd

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


T0 = datetime(2026, 10, 6, 10, 0, tzinfo=IST)


def bars(n, start=T0, px=1000.0, rng=1.0, last=None):
    """n quiet one-minute bars of range `rng`; `last` = list of (range) for the final bars."""
    out = []
    for k in range(n):
        t = (start + timedelta(minutes=k)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        r = rng
        if last and k >= n - len(last):
            r = last[k - (n - len(last))]
        out.append([t, px, px + r / 2, px - r / 2, px + (r / 4 if last and k >= n - len(last) else 0), 100.0])
    return out


CM = CostModel()
# ═════════════════════════════════════════════════════════════════════════════
section("1. spike(): the last 5 closed bars against the stock's own recent normal")
quiet = bars(60)
check("a quiet tape is ~1x", abs(spike(quiet)[0] - 1.0) < 0.05, f"{spike(quiet)[0]:.2f}")
hot = bars(60, last=[4.0] * 5)
r = spike(hot)
check("five bars at 4x the usual range read ~4x", r is not None and 3.5 < r[0] < 4.5, f"{r and r[0]:.2f}")
check("too little history (under 5 + 30 bars) is no reading, not a spike", spike(bars(30)) is None)
flat = [[b[0], 1000.0, 1000.0, 1000.0, 1000.0, 0.0] for b in bars(60)]
check("a dead (flat) tape has no normal to compare with: None", spike(flat) is None)

# ═════════════════════════════════════════════════════════════════════════════
section("2. pick_stock_option(): nearest-ATM strike that fits capital and the risk cap")


def chain(und, strikes, ce, pe, spread=0.1):
    rows = []
    for k, c, p in zip(strikes, ce, pe):
        rows.append({"strikePrice": k,
                     "CE": {"buyPrice1": c - spread / 2, "sellPrice1": c + spread / 2, "lastPrice": c},
                     "PE": {"buyPrice1": p - spread / 2, "sellPrice1": p + spread / 2, "lastPrice": p}})
    return {"underlying": und, "expiry": "27-Oct-2026", "rows": rows}


K = [480, 490, 500, 510, 520, 530]
CE = [24.0, 17.0, 11.0, 6.5, 3.5, 1.8]
PE = [1.5, 3.0, 6.0, 10.5, 16.5, 23.5]
ch = chain(500.0, K, CE, PE)
pick, why = pick_stock_option(ch, +1, risk_points=2.0, capital=10_000.0, cap_inr=800.0, lot=500, cm=CM)
check("long -> a CE at or above the money", pick is not None and pick["opt"] == "CE" and pick["strike"] >= 500,
      f"{pick and (pick['strike'], pick['opt'])} skipped {why}")
check("ATM 500CE (~Rs 5,525 a lot) is chosen when it fits both limits",
      pick is not None and pick["strike"] == 500, f"lot cost {pick and pick['lot_cost']:.0f}")
check("its delta is read off the chain (about 0.5 at the money)",
      pick is not None and 0.35 < pick["delta"] < 0.65, f"{pick and pick['delta']:.2f}")
pick2, why2 = pick_stock_option(ch, +1, risk_points=2.0, capital=4_000.0, cap_inr=800.0, lot=500, cm=CM)
check("capital Rs 4,000: ATM (~Rs 5,525) is skipped, the cheaper 510CE (~Rs 3,275) taken",
      pick2 is not None and pick2["strike"] == 510 and "one lot" in why2[0], f"{why2}")
pick3, why3 = pick_stock_option(ch, +1, risk_points=10.0, capital=10_000.0, cap_inr=800.0, lot=500, cm=CM)
check("a wide stop whose loss beats the cap on every strike: nothing, each reason given",
      pick3 is None and all("loss at stop" in w or "one lot" in w for w in why3) and why3, f"{why3}")
pick4, _ = pick_stock_option(ch, -1, risk_points=2.0, capital=10_000.0, cap_inr=800.0, lot=500, cm=CM)
check("short -> a PE at or below the money", pick4 is not None and pick4["opt"] == "PE" and pick4["strike"] <= 500,
      f"{pick4 and (pick4['strike'], pick4['opt'])}")
check("no chain: no pick, a reason", pick_stock_option({}, 1, 2.0, 1e4, 600, 500, CM)[0] is None)

# ═════════════════════════════════════════════════════════════════════════════
section("3. The desk's spike watch: every stock, once a minute")


class Candles:
    def __init__(self, data):
        self.data = data
        self.seeded = {}

    def get(self, sym, limit=120):
        return list(self.data.get(sym, []))[-limit:]

    def seed(self, sym, rows):
        self.seeded[sym] = rows


class Nse:
    def movers(self, side):
        return [{"symbol": "HOT2", "perChange": 4.2}] if side == "gainers" else []


def stat(sym, px=1000.0):
    return DailyStat(sym, px, 0.02, 1e6, 200.0)


now = T0 + timedelta(minutes=70, seconds=5)       # 11:10:05; bars up to 11:09 closed
data = {"QUIET": bars(70), "HOT1": bars(70, last=[5.0] * 5), "HOT2": bars(70, last=[3.0] * 5),
        "THIN": bars(70, last=[6.0] * 5)}
data["HOT1"].append([now.strftime("%Y-%m-%dT%H:%M:00+05:30"), 1000, 1010, 990, 1005, 1])   # forming bar
L = IntradayLimits()
L.stocks_spike_watch, L.stocks_spike_ratio, L.stocks_spike_k, L.stocks_spike_hold_min = "on", 2.5, 3, 15  # test bars: 3x and 5x
L.stocks_movers_min = 5.0
desk = sd.StockDesk({s: {} for s in data}, {s: i for i, s in enumerate(data)}, Candles(data), L,
                    api=None, nse=Nse(), log=lambda *_: None)
desk.stats = {s: stat(s) for s in data}
desk.stats["THIN"] = DailyStat("THIN", 1000.0, 0.02, 1e6, 5.0)       # Rs 5 cr a day: illiquid
desk._ready.set()
desk.refresh_movers()
for _ in range(50):
    if not desk._movers_busy:
        break
    time.sleep(0.02)
new = desk.spike_tick(now=now)
check("the NSE gainer HOT2 (3x) is listed before the bigger spike HOT1 (5x)",
      new[:2] == ["HOT2", "HOT1"], str(new))
check("the illiquid THIN and the quiet stock are not picked", "THIN" not in new and "QUIET" not in new)
check("mode on: the spikes join the active list", {r.symbol for r in desk.active} == {"HOT1", "HOT2"},
      str([r.symbol for r in desk.active]))
check("the forming bar is never read (HOT1's live 20-point bar is ignored)",
      abs(desk.last_spikes[1][1] - 5.0) < 0.6 if len(desk.last_spikes) > 1 else False, str(desk.last_spikes))
check("once a minute: a second call in the same minute does nothing", desk.spike_tick(now=now) == [])
desk._picks = [SimpleNamespace(symbol="PICK", score=9.0)]
desk._merge_spikes()
check("a rescan's picks and the live spikes are merged, picks first",
      [r.symbol for r in desk.active][:1] == ["PICK"] and len(desk.active) == 3)
desk._merge_spikes(now=time.time() + 16 * 60)
check("after stocks_spike_hold_min a spike drops out; the picks stay",
      [r.symbol for r in desk.active] == ["PICK"], str([r.symbol for r in desk.active]))
L2 = IntradayLimits()
L2.stocks_spike_watch = "shadow"
d2 = sd.StockDesk({s: {} for s in data}, {s: i for i, s in enumerate(data)}, Candles(data), L2,
                  nse=None, log=lambda *_: None)
d2.stats = {s: stat(s) for s in data}
d2._ready.set()
got = d2.spike_tick(now=now)
check("mode shadow: the spikes are found and logged, the active list is untouched",
      "HOT1" in got and d2.active == [], str(got))
d3 = sd.StockDesk({s: {} for s in data}, {s: i for i, s in enumerate(data)}, Candles(data), L,
                  nse=None, log=lambda *_: None)
d3.stats = {s: stat(s) for s in data}
d3._ready.set()
check("before stocks_pick_at (09:30) and from 15:00: no spike watch",
      d3.spike_tick(now=T0.replace(hour=9, minute=20, second=5)) == []
      and d3.spike_tick(now=T0.replace(hour=15, minute=1, second=5)) == [])
check("a spike whose costs alone would fail the entry is dropped",
      d3.spike_tick(now=now, cost_ratio=lambda s: 2.0) == [])
L4 = IntradayLimits()
d4 = sd.StockDesk({s: {} for s in data}, {s: i for i, s in enumerate(data)}, Candles(data), L4,
                  nse=None, log=lambda *_: None)
d4.stats = {s: stat(s) for s in data}
d4._ready.set()
check("the code default is off", L4.stocks_spike_watch == "off" and d4.spike_tick(now=now) == [])

# ═════════════════════════════════════════════════════════════════════════════
section("4. The scan says which filter refused what")
stats = {"A": DailyStat("A", 50.0, 0.02, 1e6, 200.0), "B": DailyStat("B", 1000.0, 0.02, 1e6, 5.0),
         "C": DailyStat("C", 1000.0, 0.005, 1e6, 200.0), "D": DailyStat("D", 1000.0, 0.02, 1e6, 200.0)}
live = {s: {"open": st.prev_close, "high": st.prev_close * 1.01, "low": st.prev_close * 0.99,
            "ltp": st.prev_close, "volume": 1.0} for s, st in stats.items()}
out = rank(stats, live, 11 * 60, IntradayLimits())
f = rank.last_funnel
check("price / turnover / ATR / relative volume drops are counted",
      f["price"] == 1 and f["turnover"] == 1 and f["atr"] == 1 and f["rvol"] == 1 and f["passed"] == 0,
      str(f))

# ═════════════════════════════════════════════════════════════════════════════
section("5. NSE option chain: parsed, nearest expiry, {} when NSE refuses")
from arb.nse import NseClient
n = NseClient(min_gap_sec=0.0)
answers = {"contract-info": {"expiryDates": ["27-Oct-2026", "23-Nov-2026"]},
           "v3": {"records": {"underlyingValue": 500.0, "data": ch["rows"]}}}
n._oc_get = lambda url, warm_if_empty=True: answers["contract-info"] if "contract-info" in url else answers["v3"]
oc = n.option_chain("ABC")
check("underlying, nearest expiry and rows", oc.get("underlying") == 500.0 and oc.get("expiry") == "27-Oct-2026"
      and len(oc.get("rows", [])) == 6, str({k: oc.get(k) for k in ("underlying", "expiry")}))
n._oc_get = lambda url, warm_if_empty=True: {}
check("NSE refuses: an empty dict, never an exception", n.option_chain("ABC") == {})

# ═════════════════════════════════════════════════════════════════════════════
section("6. The engine's stock-option shadow: logged and journalled, never an order")
from arb.intraday_engine import IntradayEngine


class Broker:
    def __getattr__(self, name):
        raise AssertionError(f"the shadow touched the broker: {name}")


jd = tempfile.mkdtemp(prefix="sso_")
logs = []
L6 = IntradayLimits()
L6.stocks_options = "shadow"
nse6 = SimpleNamespace(option_chain=lambda sym: ch, last_error="")
e = SimpleNamespace(L=L6, stocks=SimpleNamespace(nse=nse6), capital=SimpleNamespace(capital=10_000.0),
                    cm=CM, log=logs.append, journal=SimpleNamespace(dir=jd), broker=Broker())
f6 = IntradayEngine._shadow_stock_option
f6(e, "ABC", {"lot": 500}, +1, 500.0, 2.0, "ob_fvg_retest")
for _ in range(100):
    if not e._sso_busy:
        break
    time.sleep(0.02)
check("first pass only fetches the chain off-thread", logs == [] and "ABC" in e._sso_chain)
f6(e, "ABC", {"lot": 500}, +1, 500.0, 2.0, "ob_fvg_retest")
check("next pass logs WOULD BUY ... NO ORDER SENT", any("WOULD BUY ABC 500CE" in x and "NO ORDER SENT" in x
                                                        for x in logs), str(logs[-1:]))
jf = os.path.join(jd, f"shadow_{datetime.now(IST).strftime('%Y-%m-%d')}.jsonl")
check("journalled to shadow_<date>.jsonl", os.path.exists(jf) and '"stock_option"' in open(jf).read())
f6(e, "ABC", {"lot": 500}, +1, 500.0, 2.0, "ob_fvg_retest")
check("the same stock is not logged again within 10 minutes", sum("ABC" in x for x in logs) == 1)
f6(e, "XYZ", {"lot": 5000}, +1, 500.0, 2.0, "")
for _ in range(100):
    if not e._sso_busy:
        break
    time.sleep(0.02)
f6(e, "XYZ", {"lot": 5000}, +1, 500.0, 2.0, "")
check("one lot above capital: 'nothing tradable' with the reason", any("XYZ" in x and "nothing tradable" in x
                                                                      and "one lot" in x for x in logs),
      str(logs[-1:]))
L6.stocks_options = "off"
before = len(logs)
f6(e, "QQQ", {"lot": 500}, +1, 500.0, 2.0, "")
check("off: nothing at all", len(logs) == before and "QQQ" not in e._sso_chain)
check("the code default is off", IntradayLimits().stocks_options == "off")

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
