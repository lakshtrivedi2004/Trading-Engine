#!/usr/bin/env python3
"""Stocks in play, replayed through the engine's own decision and exit code (research/PAPERS.md 0i).

    python tools/fetch_stocks.py daily 370            # NSE daily bars (any time)
    python tools/fetch_stocks.py minute 120           # Angel 1-minute bars (after 15:30)
    python tools/stock_backtest.py                    # current config/intraday.json
    python tools/stock_backtest.py --k 5 --at 09:45 --set min_atr_ratio=1.2

Each session, at --at, arb/scanner.py:rank picks the top --k stocks from what is
known by then (daily ATR%, liquidity, the morning's gap, move, range and
relative volume). Those stocks are then traded exactly as the indices are:
arb/regime.py gates, arb/confluence.py setups, arb/trailing.py exits, one
position at a time across everything, with the discipline rules of
tools/backtest.py:run. Cash-equity intraday sizing: shares = the fewest of
risk cap / stop and capital x leverage / price; both directions (MIS allows
an intraday short). Costs: the full equity-intraday stack (arb/costs.py EQ)
plus one tick of slippage each way and a spread allowance.

The same sessions are replayed with the scanner's picks replaced by K random
liquid stocks and by the K LEAST in play, so the scanner is what is being
tested, not the playbooks alone. A 5-minute opening-range breakout on the same
picks (Zarattini, Barbon & Aziz 2024, "stocks in play") is reported alongside.
"""
import argparse, glob, importlib.util, json, os, random, sys
from datetime import datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
from arb.config import CostModel, IntradayLimits
from arb.confluence import evaluate
from arb.costs import leg_cost
from arb.intraday import structural_invalidation
from arb.positions import Position
from arb.regime import atr, regime_gate
from arb.scanner import daily_stat, rank
from arb.smc import analyse
from arb import trailing

spec = importlib.util.spec_from_file_location("bt", os.path.join(ROOT, "tools", "backtest.py"))
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
H = os.environ.get("STOCK_HIST") or os.path.join(ROOT, ".cache", "history", "stocks")
CM = CostModel.load()
L = IntradayLimits.load()


def mins(iso):
    return int(iso[11:13]) * 60 + int(iso[14:16])


def cost(price, qty):
    """Round trip in rupees: fees both legs, a tick each way, the spread allowance."""
    fees = leg_cost(price, qty, "BUY", "EQ", CM) + leg_cost(price, qty, "SELL", "EQ", CM)
    spread = price * float(getattr(L, "stocks_spread_frac", 0.0003)) * qty
    return fees + 2 * CM.slippage_points() * qty + spread


def size(price, risk, capital):
    cap = capital * L.max_risk_pct_per_trade / 100.0
    by_risk = int(cap // max(risk, 1e-9))
    by_cash = int(capital * float(getattr(L, "stocks_leverage", 5.0)) // price)
    return max(0, min(by_risk, by_cash))


def trades_for(name, rows, lo, hi, capital):
    """The engine's decision layer and exits on one stock's bars [lo, hi)."""
    out = []
    i = lo
    while i < hi:
        iso = rows[i][0]; m = mins(iso)
        if m < 9 * 60 + 30 or m > 14 * 60 + 45:
            i += 1; continue
        seg = rows[max(0, i - 399):i + 1]
        px = seg[-1][4]
        if regime_gate(seg, L, now=datetime.fromisoformat(iso), name="STOCKS"):
            i += 1; continue
        v = evaluate(seg, px, L, strike_step=0.0)
        if not v.tradable:
            i += 1; continue
        st = analyse(seg, strength=L.swing_strength, min_bars=L.min_structure_bars)
        lead = v.setups[0] if v.setups else None
        inv = structural_invalidation(st, v.side, lead.invalidation if lead else None)
        a = atr(seg[:-1], period=L.atr_period)
        risk = abs(px - inv) if inv else 0.0
        if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
            i += 1; continue
        qty = size(px, risk, capital)
        if qty <= 0:
            i += 1; continue
        rt = cost(px, qty)
        if rt > L.max_cost_frac_of_risk * risk * qty:
            i += 1; continue
        tgt = trailing.initial_target(seg, v.side, px, risk, L, 0.0)
        reach = abs(tgt - px) if tgt else L.reward_risk * risk
        if reach < L.min_target_r * risk or reach * qty - rt < float(getattr(L, "stocks_min_net_inr", 100.0)):
            i += 1; continue
        pos = Position(trade_id=0, symbol=name, token="", strike=0, opt="EQ", side=v.side, qty=qty,
                       entry=100.0, stop=0.0, target=1e9, risk_points=risk, index_invalidation=inv,
                       opened_at=0, cost_points=rt / qty)
        pos.index_entry, pos.index_risk, pos.index_target, pos.delta, pos.entry_bar = px, risk, tgt, 1.0, iso
        ex, why, j = None, "", i
        for j in range(i + 1, min(i + 400, len(rows))):
            o, h, l, c = rows[j][1:5]
            if rows[j][0][:10] != iso[:10] or mins(rows[j][0]) >= 15 * 60:
                ex, why = rows[j - 1][4], "squareoff"; break
            sd = pos.side
            if (sd > 0 and l <= pos.index_invalidation) or (sd < 0 and h >= pos.index_invalidation):
                lvl = pos.index_invalidation
                ex = min(o, lvl) if sd > 0 else max(o, lvl)
                why = "trail_stop" if pos.index_trailed else "stop"; break
            if pos.index_target and ((sd > 0 and h >= pos.index_target) or (sd < 0 and l <= pos.index_target)):
                win = rows[max(0, j - 399):j + 1] + [rows[j]]
                if not trailing.try_extend(pos, win, pos.index_target, L, 0.0):
                    ex, why = pos.index_target, "target"; break
            if j - i >= L.time_stop_min and (c - px) * sd / risk < L.time_stop_min_r:
                ex, why = c, "time_stop"; break
            trailing.update(pos, rows[max(0, j - 199):j + 1] + [rows[j]], L, index_px=c)
        if ex is None:
            i += 1; continue
        net = (ex - px) * v.side * qty - rt
        out.append(dict(u=name, iso=iso, day=iso[:10], m=m, held=j - i, side=v.side, why=why,
                        plays=[s.name for s in v.setups], net=net, R=net / (risk * qty),
                        risk_inr=risk * qty, cost=rt, notional=px * qty))
        i = j + 1
    return out


def orb_for(name, rows, lo, hi, capital, a_pct):
    """5-minute opening-range breakout, the Zarattini-Barbon-Aziz entry: trade in
    the direction of the first 5-minute bar, stop 10% of ATR away, out at the close."""
    day = [r for r in rows[lo:hi]]
    if len(day) < 30:
        return []
    first = day[:5]
    o, c = first[0][1], first[-1][4]
    if c == o:
        return []
    side = 1 if c > o else -1
    entry = day[5][1]
    risk = 0.10 * a_pct * entry
    qty = size(entry, risk, capital)
    if qty <= 0:
        return []
    stop = entry - side * risk
    ex, why = day[-1][4], "close"
    for r in day[5:]:
        if mins(r[0]) >= 15 * 60:
            ex = r[1]; break
        if (side > 0 and r[3] <= stop) or (side < 0 and r[2] >= stop):
            ex, why = stop, "stop"; break
    rt = cost(entry, qty)
    net = (ex - entry) * side * qty - rt
    return [dict(u=name, iso=day[5][0], day=day[5][0][:10], m=mins(day[5][0]), held=len(day), side=side,
                 why=why, plays=["orb5"], net=net, R=net / (risk * qty), risk_inr=risk * qty, cost=rt,
                 notional=entry * qty)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--at", default="09:30")
    ap.add_argument("--split", default="2026-08-10")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()
    for p in a.set:
        k, _, v = p.partition("="); setattr(L, k, json.loads(v))
    at = int(a.at[:2]) * 60 + int(a.at[3:])

    minute = {os.path.basename(p)[:-8]: json.load(open(p)) for p in glob.glob(f"{H}/*_1m.json")}
    daily = {os.path.basename(p)[:-8]: json.load(open(p)) for p in glob.glob(f"{H}/*_1d.json")}
    names = sorted(set(minute) & set(daily))
    if not names:
        sys.exit("no stocks with both minute and daily history - run tools/fetch_stocks.py first")
    by_day = {}
    for s in names:
        rows = [r for r in minute[s] if "09:15" <= r[0][11:16] <= "15:29"]
        minute[s] = rows
        start = 0
        for k in range(1, len(rows) + 1):
            if k == len(rows) or rows[k][0][:10] != rows[start][0][:10]:
                by_day.setdefault(rows[start][0][:10], {})[s] = (start, k); start = k
    # Volume from the open to the pick time, per stock per session, for the
    # same-time-of-day relative volume (the paper's definition).
    early = {s: {} for s in names}
    for day, got in by_day.items():
        for s, (lo, hi) in got.items():
            early[s][day] = sum(r[5] for r in minute[s][lo:hi] if mins(r[0]) < at)
    rng = random.Random(a.seed)
    books = {"scanner top-k": [], "random k": [], "least in play k": [], "scanner top-k ORB5": []}
    picks_log = []
    for day in sorted(by_day):
        stats, live = {}, {}
        for s, (lo, hi) in by_day[day].items():
            prior = [r for r in daily[s] if r[0] < day]
            st = daily_stat(s, prior)
            if st is None:
                continue
            upto = [r for r in minute[s][lo:hi] if mins(r[0]) < at]
            if len(upto) < 5:
                continue
            stats[s] = st
            live[s] = {"open": upto[0][1], "high": max(r[2] for r in upto), "low": min(r[3] for r in upto),
                       "ltp": upto[-1][4], "volume": sum(r[5] for r in upto)}
        same = {}
        for s in stats:
            prev = [early[s][d] for d in sorted(early[s]) if d < day][-14:]
            if len(prev) >= 7:
                same[s] = sum(prev) / len(prev)
        ranked = rank(stats, live, at, L, same_time=same)
        if len(ranked) < 3 * a.k:
            continue
        top = [r.symbol for r in ranked[:a.k]]
        bottom = [r.symbol for r in ranked[-a.k:]]
        rand = rng.sample([r.symbol for r in ranked], a.k)
        picks_log.append((day, top))
        for label, pick in (("scanner top-k", top), ("random k", rand), ("least in play k", bottom)):
            for s in pick:
                lo, hi = by_day[day][s]
                # History before the day feeds structure; trading starts at the pick time.
                start = next((i for i in range(lo, hi) if mins(minute[s][i][0]) >= at), hi)
                books[label] += trades_for(s, minute[s], start, hi, a.capital)
        for s in top:
            lo, hi = by_day[day][s]
            books["scanner top-k ORB5"] += orb_for(s, minute[s], lo, hi, a.capital, stats[s].atr_pct)
    print(f"{len(names)} stocks with 1-minute history, {len(picks_log)} sessions; top {a.k} picked at {a.at}; "
          f"capital Rs {a.capital:,.0f}, leverage {getattr(L, 'stocks_leverage', 5.0)}x, "
          f"risk cap {L.max_risk_pct_per_trade}%")
    if picks_log:
        print("   last picks:", "; ".join(f"{d[5:]} {', '.join(p)}" for d, p in picks_log[-5:]))
    for label, cands in books.items():
        cands.sort(key=lambda t: (t["day"], t["m"], t["u"]))
        tr = bt.run(cands) if "ORB" not in label else [dict(t, net2=t["net"]) for t in cands]
        print(bt.line(label, tr, a.split))
        if tr:
            c = np.array([t["cost"] for t in tr]); r_ = np.array([t["risk_inr"] for t in tr])
            print(f"   {'':44s} mean cost Rs {c.mean():,.0f} = {100 * (c / r_).mean():.0f}% of the risk; "
                  f"mean notional Rs {np.mean([t['notional'] for t in tr]):,.0f}")


if __name__ == "__main__":
    main()
