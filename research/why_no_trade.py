#!/usr/bin/env python3
"""Why did the engine not trade a move? Replays one index's 1-minute bars from
the engine's own candle cache (.cache/candles/<NAME>_1m.json) through the
decision code, minute by minute, on completed bars, with every gate in order.

    venv/bin/python research/why_no_trade.py NIFTY 2026-10-05 09:15 10:45
"""
import json, os, sys
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.config import IntradayLimits
from arb.confluence import evaluate
from arb.regime import regime_gate, trend_4h, atr, run_in_direction

name, day, lo, hi = (sys.argv[1:] + ["NIFTY", "2026-10-05", "09:15", "10:45"][len(sys.argv) - 1:])[:4]
L = IntradayLimits.load()
rows = json.load(open(os.path.join(ROOT, ".cache", "candles", f"{name}_1m.json")))
step = {"NIFTY": 50.0, "SENSEX": 100.0, "BANKNIFTY": 100.0}.get(name, 50.0)
idx = [k for k, r in enumerate(rows) if r[0].startswith(day)]
print(f"{name} {day}: {len(idx)} bars today, {len(rows)} in cache; trend_4h bars {L.trend_4h_bars}")
for k in idx:
    hm = rows[k][0][11:16]
    if not (lo <= hm <= hi):
        continue
    seg = rows[max(0, k - 399):k + 1]
    now = datetime.fromisoformat(rows[k][0]) + timedelta(minutes=1)
    why = []
    if (now.hour * 60 + now.minute) < 9 * 60 + 15 + L.warmup_minutes:
        why.append("warmup")
    veto = regime_gate(seg, L, now=now, name=name)
    if veto:
        why.append(veto)
    v = evaluate(seg, seg[-1][4], L, strike_step=step)
    t4 = trend_4h(rows[:k + 1], int(L.trend_4h_bars or 5))
    if v.tradable and t4 != v.side:
        why.append("against_4h_trend" if t4 else "4h_unknown")
    if not v.tradable:
        why.append(v.veto or ("no_setup" if not v.setups else "confluence"))
    a = atr(seg[:-1], period=L.atr_period)
    print(f" {hm} c={seg[-1][4]:9.1f} atr={a:5.1f} 4h={t4:+d} side={v.side:+d} "
          f"{'TRADABLE ' + v.setups[0].name if v.tradable else '':22s} -> {', '.join(why) or 'WOULD TRADE (then contract/plan checks)'}")
