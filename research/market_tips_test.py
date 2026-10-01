#!/usr/bin/env python3
"""Do the four "is the market bullish or bearish" tips predict anything an
intraday bot can trade? Tested on real data only.

    venv/bin/python research/market_tips_test.py

Data:
  .cache/history/NIFTY.json            NIFTY 1-minute bars, Sep 2025 - Sep 2026 (Angel)
  .cache/history/stocks/*_1d.json      daily bars of 210 F&O stocks (Angel)
  .cache/history/stocks/*_1m.json      their 1-minute bars, Jun - Sep 2026
  research/data/yahoo_%5E*.json        daily Nasdaq (^IXIC), Dow (^DJI), India VIX
                                       (^INDIAVIX), from Yahoo's chart API

The question each tip has to answer is not "does it agree with the market"
(a breadth count taken at the close agrees with the close by construction)
but "known at the time the bot could act, does it tell which way NIFTY goes
NEXT". The bot cannot enter before 09:30 (warmup), so the target is the
NIFTY move from 09:30 to 15:14. The gap (prev close -> 09:15 open) is shown
separately: it is what overnight news moves, and the bot cannot trade it.
"""
import glob
import json
import math
import os
import random
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))


def yahoo(sym):
    d = json.load(open(os.path.join(ROOT, "research", "data", f"yahoo_%5E{sym}.json")))
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = {}
    for t, o, c in zip(r["timestamp"], q["open"], q["close"]):
        if o is None or c is None:
            continue
        # Yahoo stamps a session at its local open; the date in the exchange's
        # own zone is the trading date.
        tz = timezone(timedelta(seconds=r["meta"]["gmtoffset"]))
        out[datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")] = (o, c)
    return out


def nifty_days():
    """Per day: prev close, 09:15 open, 09:30, 09:45, 15:14 and close."""
    rows = json.load(open(os.path.join(ROOT, ".cache", "history", "NIFTY.json")))
    by = defaultdict(dict)
    for r in rows:
        day, hm = r[0][:10], r[0][11:16]
        by[day][hm] = r
    days, prev = {}, None
    for day in sorted(by):
        b = by[day]
        need = ("09:15", "09:29", "09:44", "15:14")
        if not all(k in b for k in need):
            prev = None
            continue
        last = b[max(b)]
        rec = {"open": b["09:15"][1], "p930": b["09:29"][4], "p945": b["09:44"][4],
               "p1514": b["15:14"][4], "close": last[4],
               "hi": max(x[2] for x in b.values()), "lo": min(x[3] for x in b.values())}
        if prev is not None:
            rec["prev_close"] = prev
            days[day] = rec
        prev = last[4]
    return days


def sign(x):
    return (x > 0) - (x < 0)


def hit_rate(pairs):
    """Share of days the signal's sign matched the outcome's sign, with a
    binomial z against a coin flip, and the mean outcome when following it."""
    p = [(s, o) for s, o in pairs if s != 0 and o != 0]
    n = len(p)
    if not n:
        return n, float("nan"), float("nan"), float("nan")
    k = sum(1 for s, o in p if s == o)
    z = (k - n / 2) / math.sqrt(n / 4)
    return n, k / n, z, None


def follow_pnl(pairs):
    """Mean NIFTY points per day if you go long when the signal says up and
    short when it says down (before costs), and a permutation p-value."""
    p = [(s, o) for s, o in pairs if s != 0]
    if not p:
        return float("nan"), float("nan")
    real = sum(s * o for s, o in p) / len(p)
    rng = random.Random(7)
    sig = [s for s, _ in p]
    out = [o for _, o in p]
    worse = 0
    for _ in range(5000):
        rng.shuffle(sig)
        if sum(s * o for s, o in zip(sig, out)) / len(p) >= real:
            worse += 1
    return real, worse / 5000


def report(name, pairs_sign, pairs_pts):
    n, hr, z, _ = hit_rate(pairs_sign)
    mean, pval = follow_pnl(pairs_pts)
    print(f"  {name:52} n={n:3}  hit {hr*100:5.1f}%  z={z:+.2f}  "
          f"follow {mean:+6.1f} pt/day  p={pval:.3f}")


def breadth_daily():
    """Per day: advances, declines among the F&O stocks (close vs prev close),
    and the OPENING breadth (open vs prev close), known at 09:15."""
    by_day = defaultdict(lambda: [0, 0, 0, 0])
    for f in glob.glob(os.path.join(ROOT, ".cache", "history", "stocks", "*_1d.json")):
        rows = json.load(open(f))
        for a, b in zip(rows, rows[1:]):
            d = by_day[b[0][:10]]
            pc = a[4]
            d[0] += b[4] > pc
            d[1] += b[4] < pc
            d[2] += b[1] > pc
            d[3] += b[1] < pc
    return by_day


def breadth_945():
    """Per day: stocks above / below their previous close at 09:44."""
    by_day = defaultdict(lambda: [0, 0])
    for f in glob.glob(os.path.join(ROOT, ".cache", "history", "stocks", "*_1m.json")):
        rows = json.load(open(f))
        prev_close, day, last = None, None, None
        for r in rows:
            d = r[0][:10]
            if d != day:
                if last is not None:
                    prev_close = last
                day = d
            if r[0][11:16] == "09:44" and prev_close:
                x = by_day[d]
                x[0] += r[4] > prev_close
                x[1] += r[4] < prev_close
            last = r[4]
    return by_day


def main():
    nd = nifty_days()
    days = sorted(nd)
    print(f"NIFTY sessions with full 1-minute data: {len(days)} "
          f"({days[0]} .. {days[-1]})\n")

    # ── 1. Nasdaq / Dow overnight ─────────────────────────────────────────
    # The US session that closed before NIFTY's open is the previous US
    # calendar date (US closes 01:30-02:30 IST).
    print("1. Nasdaq / Dow: the last US session's close-to-close move")
    for sym, label in (("IXIC", "Nasdaq"), ("DJI", "Dow")):
        us = yahoo(sym)
        us_days = sorted(us)
        prev_us = {}
        for a, b in zip(us_days, us_days[1:]):
            prev_us[b] = (us[b][1] / us[a][1] - 1)
        gap_s, gap_p, day_s, day_p = [], [], [], []
        for d in days:
            cands = [u for u in prev_us if u < d]
            if not cands:
                continue
            u = max(cands)
            if (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(u, "%Y-%m-%d")).days > 4:
                continue
            s = sign(prev_us[u])
            r = nd[d]
            gap = r["open"] - r["prev_close"]
            intr = r["p1514"] - r["p930"]
            gap_s.append((s, sign(gap))); gap_p.append((s, gap))
            day_s.append((s, sign(intr))); day_p.append((s, intr))
        report(f"{label} up/down -> NIFTY gap at 09:15", gap_s, gap_p)
        report(f"{label} up/down -> NIFTY 09:30-15:14 (what the bot trades)", day_s, day_p)
    print()

    # ── 2. Gap as the GIFT Nifty proxy ────────────────────────────────────
    # GIFT Nifty (formerly SGX Nifty; moved to GIFT City in July 2023) trades
    # through the Indian night and its 09:00 price is, to within a few points,
    # the gap NIFTY opens on. No free history of it exists, so the gap itself
    # stands in for "GIFT Nifty was up / down".
    print("2. GIFT Nifty (proxied by the actual opening gap, which it forecasts)")
    s1, p1 = [], []
    for d in days:
        r = nd[d]
        s = sign(r["open"] - r["prev_close"])
        intr = r["p1514"] - r["p930"]
        s1.append((s, sign(intr))); p1.append((s, intr))
    report("gap up/down -> NIFTY 09:30-15:14", s1, p1)
    print()

    # ── 3. Advances vs declines ──────────────────────────────────────────
    print("3. Advance / decline (210 F&O stocks, not the whole NSE)")
    bd = breadth_daily()
    co_s, co_p, op_s, op_p = [], [], [], []
    for d in days:
        if d not in bd:
            continue
        a, dcl, ao, do = bd[d]
        r = nd[d]
        day_move = r["close"] - r["prev_close"]
        intr = r["p1514"] - r["p930"]
        co_s.append((sign(a - dcl), sign(day_move))); co_p.append((sign(a - dcl), day_move))
        op_s.append((sign(ao - do), sign(intr))); op_p.append((sign(ao - do), intr))
    report("A/D at the CLOSE vs the same day (coincident, no use)", co_s, co_p)
    report("A/D at the OPEN -> NIFTY 09:30-15:14", op_s, op_p)
    b9 = breadth_945()
    s9, p9 = [], []
    for d in days:
        if d not in b9:
            continue
        a, dcl = b9[d]
        r = nd[d]
        rest = r["p1514"] - r["p945"]
        s9.append((sign(a - dcl), sign(rest))); p9.append((sign(a - dcl), rest))
    report("A/D at 09:44 -> NIFTY 09:45-15:14 (Jun-Sep 2026)", s9, p9)
    print()

    # ── 4. India VIX 15 ──────────────────────────────────────────────────
    print("4. India VIX: previous close above / below 15, against the day's range")
    vix = yahoo("INDIAVIX")
    vdays = sorted(vix)
    hi, lo = [], []
    vals = []
    for d in days:
        prev = [v for v in vdays if v < d]
        if not prev:
            continue
        v = vix[prev[-1]][1]
        vals.append(v)
        r = nd[d]
        rng = 100.0 * (r["hi"] - r["lo"]) / r["open"]
        (hi if v > 15 else lo).append(rng)
    vals.sort()
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float("nan")
    print(f"  VIX over the period: min {vals[0]:.1f}  median {med(vals):.1f}  max {vals[-1]:.1f}")
    print(f"  days VIX > 15: {len(hi):3}  median NIFTY day range {med(hi):.2f}%")
    print(f"  days VIX <=15: {len(lo):3}  median NIFTY day range {med(lo):.2f}%")
    # Does VIX tell direction? It should not.
    vs, vp = [], []
    for d in days:
        prev = [v for v in vdays if v < d]
        if len(prev) < 2:
            continue
        ch = vix[prev[-1]][1] - vix[prev[-2]][1]
        r = nd[d]
        intr = r["p1514"] - r["p930"]
        vs.append((-sign(ch), sign(intr))); vp.append((-sign(ch), intr))
    report("VIX fell yesterday -> NIFTY up 09:30-15:14", vs, vp)


if __name__ == "__main__":
    main()
