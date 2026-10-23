#!/usr/bin/env python3
"""End-to-end replay: the REAL intraday engine, minute by minute, on real bars.

    venv/bin/python tools/replay_engine.py                  # last 10 sessions + 1 Oct
    venv/bin/python tools/replay_engine.py --days 2026-09-29 2026-10-01
    venv/bin/python tools/replay_engine.py --live-broker    # the live order path

tools/backtest.py tests the DECISION code in isolation. This runs the whole
engine as run_bot.py wires it - QuoteStore, ChainBinding, CandleStore building
its own live bars from ticks, IntradayEngine.manage()/hunt(), PositionBook,
the broker, Journal, CapitalBook - against a simulated exchange, so the parts
between the decision and the order are exercised too: the closed-bar timing,
the chase guard, cooldowns, sizing with costs inside the cap, stops, trails,
square-off, the journal and the day's ledger.

What is real and what is simulated:
  index     real 1-minute bars: .cache/history (Angel) and, for days past
            it, research/data/yahoo_1m_*.json. Each bar is played as four
            ticks - open at :01, the first extreme at :15, the second at :30,
            the close at :50 - in the order a bar that closed up (down) most
            plausibly took
  options   Black-Scholes mid on trading time with the research model's decay
            speed, IV and spreads (research/lab/realistic.py), ATM +/- 10
            strikes on the current and next expiry, 1,800 lots at the touch
  orders    PaperBroker, or with --live-broker the LiveBroker against a fake
            Angel that fills an IOC limit at the touch when marketable and
            cancels it otherwise
  clock     time.time and every module's now_ist run on the replay clock,
            which advances with real time inside a step, so the broker's own
            waits still elapse

Config is config/intraday.json and config/engine.json as they are, with the
index list cut to NIFTY, BANKNIFTY and SENSEX. Nothing touches the network.
"""
import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "research", "lab")]

import arb.instruments as ins                       # noqa: E402
from arb.instruments import IST                     # noqa: E402

_real_time, _real_mono = time.time, time.monotonic


class Clock:
    """The replay's wall clock: set per step, advancing with real time."""

    def __init__(self):
        self.base, self.mono0 = _real_time(), _real_mono()

    def set(self, dt):
        self.base, self.mono0 = dt.timestamp(), _real_mono()

    def time(self):
        return self.base + (_real_mono() - self.mono0)

    def now_ist(self):
        return datetime.fromtimestamp(self.time(), IST)


CLOCK = Clock()


def install_clock():
    import importlib
    time.time = CLOCK.time
    for name in ("arb.instruments", "arb.intraday_engine", "arb.regime", "arb.positions",
                 "arb.capital_book", "arb.journal", "arb.candles", "arb.execution",
                 "arb.stockdesk", "arb.risk", "arb.recorder", "arb.settlement", "arb.history"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(mod, "now_ist"):
            mod.now_ist = CLOCK.now_ist


install_clock()

from bsmodel import bs, T_years, expiry_calendar     # noqa: E402
from arb.book import QuoteStore                     # noqa: E402
from arb.candles import CandleStore                 # noqa: E402
from arb.capital_book import CapitalBook            # noqa: E402
from arb.config import CostModel, EngineConfig, IntradayLimits  # noqa: E402
from arb.engine import ChainBinding                 # noqa: E402
from arb.execution import LiveBroker, PaperBroker   # noqa: E402
from arb.intraday_engine import IntradayEngine      # noqa: E402
from arb.journal import Journal                     # noqa: E402

SPEC = {
    "NIFTY": dict(lot=65, step=50.0, rule="weekly_tue", seg="NFO", spread=0.25, iv=0.118,
                  yahoo="NSEI"),
    "BANKNIFTY": dict(lot=30, step=100.0, rule="monthly_tue", seg="NFO", spread=0.9, iv=0.150,
                      yahoo="NSEBANK"),
    "SENSEX": dict(lot=20, step=100.0, rule="weekly_thu", seg="BFO", spread=1.0, iv=0.122,
                   yahoo="BSESN"),
}
THETA_F, EXPIRY_IV_MULT, DEPTH = 0.55, 1.15, 1800.0


def tick(x):
    return max(0.05, round(round(x / 0.05) * 0.05, 2))


def yahoo_rows(sym):
    p = os.path.join(ROOT, "research", "data", f"yahoo_1m_%5E{sym}.json")
    if not os.path.exists(p):
        return []
    r = json.load(open(p))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for t, o, h, l, c in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        if None in (o, h, l, c):
            continue
        iso = datetime.fromtimestamp(t, IST).strftime("%Y-%m-%dT%H:%M:00+05:30")
        if "09:15" <= iso[11:16] <= "15:29":
            out.append([iso, float(o), float(h), float(l), float(c), 0.0])
    return out


def load_bars(name):
    base = os.environ.get("HISTORY_DIR") or os.path.join(ROOT, ".cache", "history")
    rows = [r for r in json.load(open(os.path.join(base, f"{name}.json")))
            if "09:15" <= r[0][11:16] <= "15:29"]
    last = rows[-1][0][:10] if rows else ""
    rows += [r for r in yahoo_rows(SPEC[name]["yahoo"]) if r[0][:10] > last]
    return rows


def next_expiry(cur, rule):
    if rule.startswith("weekly"):
        return cur + timedelta(days=7)
    y, m = cur.year + (cur.month == 12), cur.month % 12 + 1
    x = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while x.weekday() != 1:
        x -= timedelta(days=1)
    return x


class FakeAngel:
    """The exchange side of LiveBroker: IOC limits fill at the touch or cancel."""

    def __init__(self, store):
        self.store, self.orders, self.net, self.n = store, {}, {}, 0
        self.placed = []

    def placeOrderFullResponse(self, p):
        self.n += 1
        oid, uid = str(self.n), f"u{self.n}"
        slot = self.store.slot_of_token.get(str(p["symboltoken"]))
        qty, side = int(p["quantity"]), p["transactiontype"]
        px = float(p.get("price") or 0.0)
        bid = float(self.store.bid[slot]) if slot is not None else 0.0
        ask = float(self.store.ask[slot]) if slot is not None else 0.0
        fill = 0.0
        if side == "BUY" and ask > 0 and (p["ordertype"] == "MARKET" or px >= ask):
            fill = ask
        elif side == "SELL" and bid > 0 and (p["ordertype"] == "MARKET" or px <= bid):
            fill = bid
        filled = qty if fill > 0 else 0
        sym = p["tradingsymbol"]
        if filled:
            self.net[sym] = self.net.get(sym, 0) + (filled if side == "BUY" else -filled)
        st = "complete" if filled else "cancelled"
        self.orders[uid] = {"orderid": oid, "uniqueorderid": uid, "status": st, "orderstatus": st,
                            "filledshares": str(filled), "averageprice": str(fill),
                            "tradingsymbol": sym, "transactiontype": side,
                            "variety": p.get("variety", "NORMAL"), "ordertype": p["ordertype"],
                            "quantity": str(qty), "price": str(px), "triggerprice": "0",
                            "text": "" if filled else "IOC not marketable"}
        self.placed.append(dict(p))
        return {"status": True, "data": {"orderid": oid, "uniqueorderid": uid}}

    def individual_order_details(self, uid):
        o = self.orders.get(uid)
        return {"status": True, "data": dict(o)} if o else None

    def orderBook(self):
        return {"status": True, "data": [dict(o) for o in self.orders.values()]}

    def cancelOrder(self, oid, variety):
        return {"status": True}

    def position(self):
        return {"status": True, "data": [{"tradingsymbol": s, "netqty": str(q)}
                                         for s, q in self.net.items()]}


class Feed:
    def __init__(self, store, spot_slots):
        self.store, self.spot_slots = store, spot_slots
        self.future_slots, self.stock_slots = {}, {}
        self.tick_event = threading.Event()
        self.ticks, self.last_tick_ns, self.connected = 0, 0, threading.Event()


class Market:
    """Index ticks and a Black-Scholes option book for one replay day."""

    def __init__(self, day, bars, trading_days):
        self.day = day
        self.store = QuoteStore(2048)
        self.spot_slots, self.bindings, self.chains = {}, [], []
        self.bars = {}
        for name, spec in SPEC.items():
            today = [r for r in bars[name] if r[0][:10] == day.isoformat()]
            if not today:
                continue
            self.bars[name] = today
            self.spot_slots[name] = self.store.assign(f"IDX_{name}")
            days = sorted({date.fromisoformat(r[0][:10]) for r in bars[name]})
            cal = expiry_calendar(days, spec["rule"])
            cur = cal.get(day)
            if cur is None:
                continue
            open_px = today[0][1]
            atm = round(open_px / spec["step"]) * spec["step"]
            for exp in (cur, next_expiry(cur, spec["rule"])):
                ks = [atm + i * spec["step"] for i in range(-10, 11)]
                es = exp.strftime("%d%b%Y").upper()
                ce_t = [f"{name}{es}{int(k)}CE" for k in ks]
                pe_t = [f"{name}{es}{int(k)}PE" for k in ks]
                ce_s = [self.store.assign(t) for t in ce_t]
                pe_s = [self.store.assign(t) for t in pe_t]
                b = ChainBinding(es, ks, ce_s, pe_s, ce_t, pe_t, ce_t, pe_t, spec["lot"],
                                 underlying=name, segment=spec["seg"])
                self.bindings.append(b)
                t0 = T_years(datetime.combine(day, datetime.min.time()).replace(
                    hour=9, minute=15, tzinfo=IST), exp, days)
                sig = spec["iv"] * (EXPIRY_IV_MULT if exp == day else 1.0)
                self.chains.append((b, name, exp, ks, ce_s, pe_s, t0, sig, days))

    def quote(self, name, px, now):
        ns = time.perf_counter_ns()
        self.store.update_from_tick(self.spot_slots[name], 0.0, 0.0, 0.0, 0.0, px, now_ns=ns)
        sp = SPEC[name]["spread"]
        for b, und, exp, ks, ce_s, pe_s, t0, sig, days in self.chains:
            if und != name:
                continue
            tk = max(0.0, t0 - THETA_F * (t0 - T_years(now, exp, days)))
            b.T = max(tk, 1e-6)
            for k, cs_, ps_ in zip(ks, ce_s, pe_s):
                for slot, opt in ((cs_, "CE"), (ps_, "PE")):
                    mid = bs(px, k, tk, sig, opt)
                    bid, ask = tick(mid - sp / 2), tick(mid + sp / 2)
                    if ask <= bid:
                        ask = bid + 0.05
                    self.store.update_from_tick(slot, bid, ask, DEPTH, DEPTH, mid, now_ns=ns)


def play_day(day, bars, workdir, capital, args, log_lines):
    CLOCK.set(datetime.combine(day, datetime.min.time()).replace(hour=9, minute=10, tzinfo=IST))
    m = Market(day, bars, None)
    if not m.bars:
        return None
    cfg = EngineConfig.load()
    L = IntradayLimits.load()
    cm = CostModel.load()
    names = set(m.bars)
    unds = [u for u in cfg.active_underlyings() if u.name in names]
    cs = CandleStore(None, unds, interval="1m", lookback_days=7,
                     cache_dir=os.path.join(workdir, "candles"), log=log_lines.append)
    for name in names:
        hist = [r for r in bars[name] if r[0][:10] < day.isoformat()]
        cs.data[name] = [list(r) for r in hist[-2999:]]
    feed = Feed(m.store, m.spot_slots)
    cs.attach_feed(m.store, m.spot_slots, {}, {})
    if args.live_broker:
        api = FakeAngel(m.store)
        broker = LiveBroker(api, cm, product="INTRADAY", duration=str(L.order_duration).upper(),
                            confirm_fills=True, log=log_lines.append)
        broker.protection_pct = L.market_protection_pct
        broker.order_rate_per_sec = L.order_rate_per_sec
        broker.exit_safety_fixes = L.exit_safety_fixes
        broker.order_tags = L.order_tags
    else:
        api, broker = None, PaperBroker(cm)
        broker.protection_pct = L.market_protection_pct
    journal = Journal(directory=os.path.join(workdir, "journal"), day=day.isoformat())
    eng = IntradayEngine(feed, m.bindings, cfg, cm, L, broker, journal, capital, cs,
                         notifier=None, log=lambda s: log_lines.append(f"{day} {s}"))
    errors = []
    for k, bar in enumerate(m.bars[next(iter(m.bars))]):
        t0 = datetime.fromisoformat(bar[0])
        for sec, idx in ((1, 0), (15, 1), (30, 2), (50, 3)):
            CLOCK.set(t0 + timedelta(seconds=sec))
            now = CLOCK.now_ist()
            for name, rows in m.bars.items():
                if k >= len(rows) or rows[k][0] != bar[0]:
                    continue
                o, h, l, c = rows[k][1:5]
                path = (o, l, h, c) if c >= o else (o, h, l, c)
                m.quote(name, path[idx], now)
            cs._sample_live()
            # Quote ages are measured on the real clock; re-stamp them as fresh
            # just before the engine reads them, so a busy machine cannot make a
            # strike look stale and change the replay (system test, 5 Oct 2026).
            m.store.recv_ns[:] = time.perf_counter_ns()
            try:
                eng.manage()
                eng.hunt()
            except Exception as e:                       # the loop catches these live too
                errors.append(f"{now:%H:%M:%S} {e!r}")
    CLOCK.set(datetime.combine(day, datetime.min.time()).replace(hour=15, minute=31, tzinfo=IST))
    for pos in eng.book.snapshot():
        eng.close(pos, "squareoff")
    if hasattr(capital, "close_day"):
        try:
            capital.close_day()
        except Exception:
            pass
    sells = [p for p in (api.placed if api else []) if p["transactiontype"] == "SELL"]
    net_left = {s: q for s, q in (api.net.items() if api else []) if q}
    return dict(day=day.isoformat(), records=list(eng.records), rejects=dict(eng.rejects),
                errors=errors, halted=eng.halted, halt_reason=eng.halt_reason,
                orders=len(api.placed) if api else None, sells=len(sells), net_left=net_left,
                open_left=eng.book.count())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--live-broker", action="store_true")
    ap.add_argument("--capital", type=float, default=None,
                    help="size from this purse, as live sizes from the account's funds "
                         "(default: capital_ceiling)")
    ap.add_argument("--workdir", default=os.path.join(ROOT, "research_out", "replay"))
    args = ap.parse_args()
    bars = {n: load_bars(n) for n in SPEC}
    all_days = sorted({r[0][:10] for r in bars["NIFTY"]})
    days = args.days or all_days[-(args.last + 1):]
    os.makedirs(args.workdir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="run_", dir=args.workdir)
    L = IntradayLimits.load()
    CLOCK.set(datetime.fromisoformat(days[0] + "T09:00:00+05:30"))
    capital = CapitalBook(float(args.capital or L.capital_ceiling or L.starting_capital),
                          path=os.path.join(work, "capital.json"), ceiling=L.capital_ceiling)
    mode = "LIVE broker vs fake Angel" if args.live_broker else "paper broker"
    print(f"replay {len(days)} sessions {days[0]}..{days[-1]} | {mode} | capital Rs {capital.capital:,.0f} | "
          f"playbooks {list(L.playbooks)} | closed bars {L.decide_on_closed_bars} | "
          f"costs inside {L.risk_includes_costs} | same-side {L.same_side_cooldown_min:g} min | "
          f"workdir {work}\n")
    total, n, wins, problems = 0.0, 0, 0, []
    for d in days:
        logs = []
        r = play_day(date.fromisoformat(d), bars, work, capital, args, logs)
        if r is None:
            print(f"{d}: no bars")
            continue
        with open(os.path.join(work, f"log_{d}.txt"), "w") as fh:
            fh.write("\n".join(map(str, logs)))
        day_net = sum(float(x.get("net_inr") or 0) for x in r["records"])
        total += day_net
        n += len(r["records"])
        wins += sum(1 for x in r["records"] if float(x.get("net_inr") or 0) >= 0)
        top = sorted(r["rejects"].items(), key=lambda kv: -kv[1])[:4]
        print(f"{d}: {len(r['records'])} trades, net Rs {day_net:+,.0f}"
              + (f", orders {r['orders']} (sells {r['sells']})" if r["orders"] is not None else "")
              + (f", HALTED: {r['halt_reason']}" if r["halted"] else "")
              + f" | top rejects {top}")
        for x in r["records"]:
            print(f"    {x.get('ts_ist', '')[11:19]} {x['symbol']:24s} {x['opt']} "
                  f"entry {x['entry']:8.2f} exit {x['exit']:8.2f} {x['exit_reason']:16s} "
                  f"R {x.get('r_multiple', 0):+.2f} net Rs {x['net_inr']:+8,.0f} "
                  f"(risk cap check: loss {'OK' if x['net_inr'] >= -abs(x.get('capital', 1e9) * L.max_risk_pct_per_trade / 100) - 1 else 'OVER'})")
        if r["errors"]:
            problems.append(f"{d}: {len(r['errors'])} loop errors, first {r['errors'][0]}")
        if r["net_left"]:
            problems.append(f"{d}: broker still holds {r['net_left']}")
        if r["open_left"]:
            problems.append(f"{d}: engine still holds {r['open_left']} position(s)")
    print(f"\nTOTAL {n} trades, win {100 * wins / n if n else 0:.0f}%, net Rs {total:+,.0f}; "
          f"capital now Rs {capital.capital:,.0f} (equity {capital.equity:,.0f})")
    print("PROBLEMS:\n  " + "\n  ".join(problems) if problems else "PROBLEMS: none")


if __name__ == "__main__":
    main()
