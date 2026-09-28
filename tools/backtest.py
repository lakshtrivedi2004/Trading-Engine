#!/usr/bin/env python3
"""A year of 1-minute index history through the engine's own decision and exit code.

    python tools/fetch_history.py index 370          # once: .cache/history/*.json
    python tools/backtest.py                         # current config/intraday.json
    python tools/backtest.py --set min_atr_ratio=0.7 --set playbooks=[]

Each bar runs arb/regime.py:regime_gate and arb/confluence.py:evaluate exactly as
the live hunt does; each entry is managed with arb/trailing.py on the index. Trades
are then taken one at a time with the discipline rules. Rupees use a delta sized to
the risk cap and Rs 75 fees a lot; option decay is NOT charged, so treat results as
an upper bound (research/PAPERS.md 0f). In-sample / held-out split by date."""
import json, os, sys
from datetime import date, datetime, timedelta
from multiprocessing import Pool
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.config import IntradayLimits
from arb.confluence import evaluate
from arb.intraday import structural_invalidation
from arb.positions import Position
from arb.regime import atr, atr_ratio, efficiency, regime_gate
from arb.smc import analyse
from arb import trailing
LOT = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}
STEP = {"NIFTY": 50.0, "BANKNIFTY": 100.0, "SENSEX": 100.0}
FEE = 75.0
L = IntradayLimits.load()
def mins(iso): return int(iso[11:13]) * 60 + int(iso[14:16])
def work(args):
    name, rows, lo, hi = args
    out = []; lot = LOT[name]; cap = 10000 * L.max_risk_pct_per_trade / 100
    # Per-day context: open, previous close, initial balance (09:15-09:44), 10-day ADR.
    D = {}
    for k, r in enumerate(rows):
        d = D.setdefault(r[0][:10], dict(o=r[1], hi=r[2], lo=r[3], ibh=r[2], ibl=r[3], first=k))
        d["hi"], d["lo"] = max(d["hi"], r[2]), min(d["lo"], r[3])
        if r[0][11:16] < "09:45":
            d["ibh"], d["ibl"] = max(d["ibh"], r[2]), min(d["ibl"], r[3])
        d["c"] = r[4]
    keys = sorted(D)
    for n_, k in enumerate(keys):
        prev = [D[x] for x in keys[max(0, n_ - 10):n_]]
        D[k]["pc"] = prev[-1]["c"] if prev else D[k]["o"]
        D[k]["adr"] = sum(p["hi"] - p["lo"] for p in prev) / len(prev) if prev else 0.0
    for i in range(lo, hi):
        iso = rows[i][0]; m = mins(iso)
        if m < 9 * 60 + 30 or m > 14 * 60 + 45:
            continue
        seg = rows[i - 399:i + 1]
        spot = seg[-1][4]
        if regime_gate(seg, L, now=datetime.fromisoformat(iso)):
            continue
        v = evaluate(seg, spot, L, strike_step=STEP[name])
        if not v.tradable:
            continue
        st = analyse(seg, strength=L.swing_strength, min_bars=L.min_structure_bars)
        lead = v.setups[0] if v.setups else None
        inv = structural_invalidation(st, v.side, lead.invalidation if lead else None)
        if not inv:
            continue
        risk = abs(spot - inv)
        a = atr(seg[:-1], period=L.atr_period)
        if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
            continue
        delta = min(0.55, cap / (risk * lot))
        if delta < L.min_delta:
            continue
        cost_pts = FEE / lot
        prem_risk = risk * delta
        if cost_pts > L.max_cost_frac_of_risk * prem_risk:
            continue
        tgt = trailing.initial_target(seg, v.side, spot, risk, L, STEP[name])
        reach = abs(tgt - spot) if tgt else L.reward_risk * risk
        if reach < L.min_target_r * risk or reach * delta - cost_pts < L.min_net_points:
            continue
        pos = Position(trade_id=0, symbol="", token="", strike=0, opt="CE" if v.side > 0 else "PE",
                       side=v.side, qty=lot, entry=100.0, stop=100 - prem_risk, target=1e9,
                       risk_points=prem_risk, index_invalidation=inv, opened_at=0,
                       cost_points=cost_pts)
        pos.index_entry, pos.index_risk, pos.index_target = spot, risk, tgt
        pos.delta, pos.entry_bar = delta, iso
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
                win = rows[j - 399:j + 1] + [rows[j]]
                if not trailing.try_extend(pos, win, pos.index_target, L, STEP[name]):
                    ex, why = pos.index_target, "target"; break
            r_now = (c - spot) * sd / risk
            if j - i >= L.time_stop_min and r_now < L.time_stop_min_r:
                ex, why = c, "time_stop"; break
            trailing.update(pos, rows[j - 199:j + 1] + [rows[j]], L, index_px=c)
        if ex is None:
            continue
        move = (ex - spot) * v.side
        net = move * delta * lot - FEE
        d = datetime.fromisoformat(iso)
        dd = D[iso[:10]]; adr = dd["adr"] or 1.0
        sofar = rows[dd["first"]:i + 1]
        out.append(dict(dtrend=v.day_trend, bsrc=v.bias_source, gap=(dd["o"] - dd["pc"]) / adr,
                        ibr=(dd["ibh"] - dd["ibl"]) / adr,
                        ibpos=(1 if spot > dd["ibh"] else -1 if spot < dd["ibl"] else 0) * v.side,
                        withday=(spot - dd["o"]) * v.side / adr,
                        dayrng=(max(r[2] for r in sofar) - min(r[3] for r in sofar)) / adr,
                        mfeR=0.0,u=name, iso=iso, day=iso[:10], wd=d.weekday(), m=m, i=i, j=j, side=v.side,
                        grade=v.grade, plays=[s.name for s in v.setups], risk=risk, delta=delta,
                        R=(move - cost_pts / delta) / risk, net=net, why=why, held=j - i,
                        er=efficiency([r[4] for r in seg[-17:-1]]) * 15 ** 0.5,
                        ar=atr_ratio(seg, period=L.atr_period, baseline_bars=L.atr_baseline_bars),
                        rev=v.reversal))
    return out
EXP = {"NIFTY": 1, "SENSEX": 3}          # weekly expiry weekday (Tue, Thu)
def last_wd(d, wd):
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)
    while nxt.weekday() != wd: nxt -= timedelta(days=1)
    return nxt
def is_expiry(t):
    d = date.fromisoformat(t["day"])
    if t["u"] == "BANKNIFTY": return d == last_wd(d, 1)
    return d.weekday() == EXP[t["u"]]
def run(cands, ok=lambda t: True, max_trades=15, per_play=2, stop_after_win=False,
        stop_after_loss=False, theta=None, halt_pct=30.0):
    """theta(t) -> Rs decay to subtract (default none)."""
    out = []
    by = {}
    for t in cands: by.setdefault((t["u"], t["day"]), []).append(t)
    # One position at a time ACROSS indices, as the engine does.
    days = {}
    for (u, d), ts in by.items(): days.setdefault(d, []).extend(ts)
    for d in sorted(days):
        ts = sorted(days[d], key=lambda t: (t["m"], t["u"]))
        busy_until, n, pnl, streak, pause, last_loss, plays = -1, 0, 0.0, 0, -1, -10**9, {}
        for t in ts:
            if t["m"] <= busy_until or t["m"] < pause or t["m"] - last_loss < 3: continue
            if n >= max_trades or pnl <= -halt_pct * 100: continue
            key = (t["u"], t["plays"][0] if t["plays"] else "", t["side"])
            if per_play and plays.get(key, 0) >= per_play: continue
            if not ok(t): continue
            net = t["net"] - (theta(t) if theta else 0.0)
            out.append(dict(t, net2=net)); n += 1; pnl += net
            plays[key] = plays.get(key, 0) + 1
            busy_until = t["m"] + t["held"]
            if net < 0:
                streak += 1; last_loss = busy_until
                if streak >= 3: pause, streak = busy_until + 30, 0
                if stop_after_loss: break
            else:
                streak = 0
                if stop_after_win: break
    return out
def stats(tr):
    if not tr: return dict(n=0, win=0, net=0, mean=0, worst=0, R=0)
    p = np.array([t["net2"] for t in tr]); R = np.array([t["R"] for t in tr])
    return dict(n=len(p), win=100 * (p > 0).mean(), net=p.sum(), mean=p.mean(), worst=p.min(), R=R.mean())
def line(label, tr, split):
    a = stats([t for t in tr if t["day"] < split]); b = stats([t for t in tr if t["day"] >= split])
    return (f"{label:44s} IN n={a['n']:4d} win={a['win']:3.0f}% net=Rs{a['net']:9,.0f} R={a['R']:+.2f} | "
            f"OUT n={b['n']:4d} win={b['win']:3.0f}% net=Rs{b['net']:8,.0f} R={b['R']:+.2f}")

def periods(tr):
    """Win rate of trades, and share of profitable days / weeks / months / the year."""
    keys = {"day": lambda d: d, "week": lambda d: date.fromisoformat(d).isocalendar()[:2],
            "month": lambda d: d[:7], "year": lambda d: "all"}
    for name, key in keys.items():
        g = {}
        for t in tr: g.setdefault(key(t["day"]), []).append(t["net2"])
        pnl = [sum(v) for v in g.values()]; w = [np.mean([x > 0 for x in v]) for v in g.values()]
        print(f"{name:5s} n={len(g):3d}  profitable {100*np.mean([p > 0 for p in pnl]):3.0f}%  "
              f"avg trade win rate {100*np.mean(w):3.0f}%  avg net Rs {np.mean(pnl):+8,.0f}")
def init(sets):
    global L
    L = IntradayLimits.load()
    for k, v in sets.items():
        setattr(L, k, tuple(v) if isinstance(v, list) else v)
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="year backtest")
    ap.add_argument("--set", action="append", default=[], help="override a limit, k=v (JSON value)")
    ap.add_argument("--split", default="2026-05-25"); ap.add_argument("--procs", type=int, default=5)
    a = ap.parse_args()
    sets = {k: json.loads(v) for k, _, v in (p.partition("=") for p in a.set)}
    init(sets)
    jobs = []
    for name in LOT:
        p = os.path.join(ROOT, ".cache", "history", f"{name}.json")
        if not os.path.exists(p): continue
        rows = [r for r in json.load(open(p)) if "09:15" <= r[0][11:16] <= "15:29"]
        for lo in range(400, len(rows), 6000):
            jobs.append((name, rows, lo, min(lo + 6000, len(rows))))
    with Pool(a.procs, initializer=init, initargs=(sets,)) as pool:
        C = sorted((t for r in pool.map(work, jobs) for t in r), key=lambda t: (t["day"], t["m"], t["u"]))
    tr = run(C)
    periods(tr)
    print(line("all indices", tr, a.split))
    for u in LOT:
        print(line("  " + u, [t for t in tr if t["u"] == u], a.split))
