#!/usr/bin/env python3
"""Replay 1 Oct 2026 12:45-13:40 through the engine's own decision code.

Live lost Rs 1,401 on three PE trades (13:03 NIFTY, 13:17 NIFTY, 13:27
SENSEX). This shows, minute by minute, what the engine saw: pace veto,
verdict, the setup's steps, the 4-hour trend, and how far the index had
already run before the entry.

History: Angel 1-minute bars to 30 Sep (.cache/history), plus Yahoo's 1-minute
^NSEI / ^BSESN for 1 Oct (research/data/yahoo_1m_*.json). Yahoo's index bars
can differ from Angel's by a point or two.

Two views per minute:
  done  bars up to the last COMPLETED minute - what tools/backtest.py tests
  live  plus the bar still forming, approximated by the next bar's first
        prints (open, and the open-high/low so far is unknown, so this view
        uses the completed bar's own OHLC as the "live" bar) - the engine's
        live hunt() passes the forming bar to the playbooks, see notes
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.config import IntradayLimits          # noqa: E402
from arb.confluence import evaluate             # noqa: E402
from arb.regime import regime_gate, trend_4h, atr  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
STEP = {"NIFTY": 50.0, "SENSEX": 100.0}


def yahoo_day(sym, day):
    r = json.load(open(os.path.join(ROOT, "research", "data", f"yahoo_1m_%5E{sym}.json")))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for t, o, h, l, c in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        iso = datetime.fromtimestamp(t, IST).strftime("%Y-%m-%dT%H:%M:00+05:30")
        if iso.startswith(day) and c and "09:15" <= iso[11:16] <= "15:29":
            out.append([iso, o, h, l, c, 0.0])
    return out


def series(name, ysym, day):
    hist = [r for r in json.load(open(os.path.join(ROOT, ".cache", "history", f"{name}.json")))
            if r[0][:10] < day and "09:15" <= r[0][11:16] <= "15:29"]
    return hist + yahoo_day(ysym, day)


def main(day="2026-10-01", lo="12:45", hi="13:40"):
    L = IntradayLimits.load()
    for name, ysym in (("NIFTY", "NSEI"), ("SENSEX", "BSESN")):
        rows = series(name, ysym, day)
        idx = [k for k, r in enumerate(rows) if r[0].startswith(day)]
        print(f"\n== {name}  (4h trend uses {L.trend_4h_bars} bars)")
        day_open = rows[idx[0]][1]
        for k in idx:
            hm = rows[k][0][11:16]
            if not (lo <= hm <= hi):
                continue
            seg = rows[max(0, k - 399):k + 1]           # bar k is the last COMPLETED bar
            now = datetime.fromisoformat(rows[k][0]) + timedelta(minutes=1)
            veto = regime_gate(seg, L, now=now, name=name)
            v = evaluate(seg, seg[-1][4], L, strike_step=STEP[name])
            t4 = trend_4h(rows[:k + 1], int(L.trend_4h_bars or 5))
            a = atr(seg[:-1], period=L.atr_period)
            hi30 = max(r[2] for r in seg[-30:]); lo30 = min(r[3] for r in seg[-30:])
            run_dn = (hi30 - seg[-1][4]) / a if a else 0
            run_up = (seg[-1][4] - lo30) / a if a else 0
            chain = " > ".join(s for s, _ in v.setups[0].steps) if v.setups else ""
            print(f"  {hm} c={seg[-1][4]:9.1f} atr={a:5.1f} off30-high={run_dn:4.1f}ATR off30-low={run_up:4.1f}ATR "
                  f"4h={t4:+d} veto={veto or '-':18s} side={v.side:+d} tradable={str(v.tradable):5s} "
                  f"grade={v.grade or '-'} {chain} {v.veto or ''}")


if __name__ == "__main__":
    main(*sys.argv[1:])
