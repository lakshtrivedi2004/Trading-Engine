#!/usr/bin/env python3
"""Popular online intraday strategies on our data (owner, 21 Oct 2026). Report only - nothing is wired.

    HISTORY_DIR=.cache/history_live nice -n 19 venv/bin/python research/online_strategies_test.py

  VWAP trend   Zarattini & Aziz (2023, "VWAP: the Holy Grail for Day Trading
               Systems", QQQ): long above session VWAP, short below, flip on a
               cross. The index has no volume, so session TWAP of typical price
               stands in (the bars carry no index volume).
  RSI 50       the viral Indian rule (Multibagg 2026: buy ATM CE when RSI > 50,
               PE when < 50): RSI(14) on 5-minute closes crossing 50.
  Supertrend   (10, 3) on 5-minute bars, the most common Tradetron / Streak
               strategy: trade each flip, stop at the Supertrend line.
  BB squeeze   Bollinger(20, 2) width at a 50-bar low, then a 5-minute close
               outside the band.
Signals on COMPLETED 5-minute bars (09:15-aligned), entry at that bar's close,
09:30-14:45; exit at the opposite signal, the stop, or 15:13. One position per
index at a time. With and without the live 4-hour trend filter. Priced as the
option the engine buys (realistic.reprice), Rs 11,000, live 8% cap, 15% halt,
one position across the book, side cooldown 30; split 25 May 2026.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import lhm_refine_test as R                              # noqa: E402  (IX, F, lhm, row, table, price)
import screen as S                                       # noqa: E402
from common import gate_halves                           # noqa: E402
from playbook_lab import INDICES, candidates             # noqa: E402
from fixes_test import priced                            # noqa: E402

IX, F, L = R.IX, R.F, R.L
PAR = dict(st_n=10, st_k=3.0, bb_n=20, bb_k=2.0, bb_look=50)      # robustness pass varies these


def bars5(rows):
    """5-minute bars per session: (last 1-minute index, o, h, l, c)."""
    out, cur = [], None
    for i, r in enumerate(rows):
        m = S.hm(r[0]) - S.T("09:15")
        key = (r[0][:10], m // 5)
        if cur is None or cur[0] != key:
            if cur:
                out.append(cur[1])
            cur = (key, [i, r[1], r[2], r[3], r[4]])
        else:
            b = cur[1]
            b[0], b[2], b[3], b[4] = i, max(b[2], r[2]), min(b[3], r[3]), r[4]
    if cur:
        out.append(cur[1])
    return out


def rsi(c, n=14):
    out = np.full(len(c), 50.0)
    g = l_ = 0.0
    for k in range(1, len(c)):
        d = c[k] - c[k - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l_ = (l_ * (n - 1) + max(-d, 0)) / n
        out[k] = 100 - 100 / (1 + g / l_) if l_ > 0 else 100.0
    return out


def supertrend(b, n=10, k=3.0):
    h, l, c = (np.array([x[q] for x in b]) for q in (2, 3, 4))
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = np.zeros(len(b))
    for q in range(1, len(b)):
        atr[q] = (atr[q - 1] * (n - 1) + tr[q]) / n if q > 1 else tr[q]
    mid = (h + l) / 2
    up, dn = mid + k * atr, mid - k * atr
    st, dirn = np.zeros(len(b)), np.ones(len(b))
    for q in range(1, len(b)):
        if c[q - 1] <= up[q - 1]:
            up[q] = min(up[q], up[q - 1])
        if c[q - 1] >= dn[q - 1]:
            dn[q] = max(dn[q], dn[q - 1])
        dirn[q] = 1 if c[q] > up[q - 1] else -1 if c[q] < dn[q - 1] else dirn[q - 1]
        st[q] = dn[q] if dirn[q] > 0 else up[q]
    return dirn, st


def signals(name, kind):
    """[(i, side, stop_or_None)] at completed 5-minute bars."""
    ix = IX[name]
    rows, b = ix.rows, bars5(ix.rows)
    c = np.array([x[4] for x in b])
    out = []
    if kind == "vwap":
        tw, day, num, cnt, prev = 0.0, None, 0.0, 0, 0
        for x in b:
            r = rows[x[0]]
            if r[0][:10] != day:
                day, num, cnt, prev = r[0][:10], 0.0, 0, 0
            num += (x[2] + x[3] + x[4]) / 3
            cnt += 1
            tw = num / cnt
            side = 1 if x[4] > tw else -1
            if cnt >= 3 and side != prev:
                out.append((x[0], side, tw))
            prev = side if cnt >= 3 else prev
    elif kind == "rsi":
        rv = rsi(c)
        for q in range(1, len(b)):
            if rv[q - 1] <= 50 < rv[q]:
                out.append((b[q][0], 1, None))
            elif rv[q - 1] >= 50 > rv[q]:
                out.append((b[q][0], -1, None))
    elif kind == "st":
        d, st = supertrend(b, PAR["st_n"], PAR["st_k"])
        for q in range(1, len(b)):
            if d[q] != d[q - 1]:
                out.append((b[q][0], int(d[q]), st[q]))
    elif kind == "bb":
        n, k, lk = PAR["bb_n"], PAR["bb_k"], PAR["bb_look"]
        for q in range(n + lk, len(b)):
            w = c[q - n:q]
            mu, sd = w.mean(), w.std()
            widths = [c[p - n:p].std() for p in range(q - lk, q)]
            if sd > 0 and min(widths) >= sd * 0.999:
                if c[q] > mu + k * sd:
                    out.append((b[q][0], 1, None))
                elif c[q] < mu - k * sd:
                    out.append((b[q][0], -1, None))
    return out


def gen(name, kind):
    ix, P = IX[name], R.PP[name]
    rows, cc, atr = ix.rows, P["c"], P["atr"]
    sig = signals(name, kind)
    by = {}
    for i, side, lvl in sig:
        by.setdefault(i, (side, lvl))
    flips = sorted(by)
    out, busy = [], -1
    for n_, i in enumerate(flips):
        t = S.hm(rows[i][0]) + 1
        if i <= busy or not (S.T("09:30") <= t <= S.T("14:45")) or atr[i] <= 0:
            continue
        side, lvl = by[i]
        a = atr[i]
        stop = lvl if (lvl and (cc[i] - lvl) * side > 0.3 * a) else cc[i] - side * 1.5 * a
        stop = cc[i] - side * min(abs(cc[i] - stop), 3.0 * a)
        nxt = next((f for f in flips[n_ + 1:] if by[f][0] != side), None)
        day = rows[i][0][:10]
        j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), a, last=S.T("15:13"))
        if nxt is not None and nxt < j and rows[nxt][0][:10] == day:
            j, px, why = nxt, cc[nxt], "flip"
        out.append(S.cand(name, ix, i, j, px, side, abs(cc[i] - stop), why, kind.upper()))
        busy = j
    return out


def main():
    print(f"{R.DAYS[0]} - {R.DAYS[-1]}, {len(R.DAYS)} days", flush=True)
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OBx = [dict(t, plays=["OB"]) for t in priced(C, IX, R.CAPITAL, True)
           if t["u"] != "NIFTY" and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
           and gate_halves(t, *live_pace[t["u"]])]
    live = R.lhm("NIFTY")
    book = OBx + live
    rows_ = [("BEST BOOK (retest BANKNIFTY+SENSEX + LHM NIFTY)", book)]
    names = {"vwap": "VWAP trend", "rsi": "RSI 50", "st": "Supertrend 10,3", "bb": "BB squeeze"}
    for kind, label in names.items():
        raw = [t for n in INDICES for t in gen(n, kind)]
        allp = []
        for t in raw:
            r = R.rz.reprice(t, IX[t["u"]], R.CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                             slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
            if r:
                u = dict(t)
                u.update(r)
                allp.append(u)
        trend = [t for t in allp if F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0]
        rows_ += [(f"{label}, all three, no trend filter", allp),
                  (f"{label}, all three, 4h trend", trend)]
        rows_ += [(f"  {label} {n}, 4h trend", [t for t in trend if t["u"] == n]) for n in INDICES]
        rows_ += [(f"  BEST BOOK + {label} (4h trend)", book + trend)]
    R.table("Online strategies, alone and added to the best book", rows_)


if __name__ == "__main__" and not os.environ.get("PICKED") and not os.environ.get("ROBUST"):
    main()


def picked():
    """Second pass: the index subsets positive in both halves alone, added to the
    best book. Chosen AFTER seeing the first table, so a pass here is weaker evidence."""
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OBx = [dict(t, plays=["OB"]) for t in priced(C, IX, R.CAPITAL, True)
           if t["u"] != "NIFTY" and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
           and gate_halves(t, *live_pace[t["u"]])]
    book = OBx + R.lhm("NIFTY")

    def leg(n, kind):
        out = []
        for t in gen(n, kind):
            r = R.rz.reprice(t, IX[n], R.CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                             slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
            if r and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0:
                u = dict(t)
                u.update(r)
                out.append(u)
        return out
    st_se, bb_ni = leg("SENSEX", "st"), leg("NIFTY", "bb")
    R.table("Index subsets added to the best book (picked after the first table)", [
        ("BEST BOOK", book), ("BEST BOOK + Supertrend SENSEX", book + st_se),
        ("BEST BOOK + BB squeeze NIFTY", book + bb_ni),
        ("BEST BOOK + both", book + st_se + bb_ni)])


if __name__ == "__main__" and os.environ.get("PICKED") and not os.environ.get("ROBUST"):
    picked()


def robust():
    """Third pass: do the two picked legs survive their neighbouring settings?"""
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OBx = [dict(t, plays=["OB"]) for t in priced(C, IX, R.CAPITAL, True)
           if t["u"] != "NIFTY" and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
           and gate_halves(t, *live_pace[t["u"]])]
    book = OBx + R.lhm("NIFTY")
    _, b = R.row("base", book)

    def leg(n, kind):
        out = []
        for t in gen(n, kind):
            r = R.rz.reprice(t, IX[n], R.CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                             slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
            if r and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0:
                u = dict(t)
                u.update(r)
                out.append(u)
        return out
    for title, kind, n, grid in (
            ("BB squeeze NIFTY", "bb", "NIFTY",
             [dict(bb_n=bn, bb_k=bk, bb_look=lk) for bn in (15, 20, 25) for bk in (1.75, 2.0, 2.25)
              for lk in (30, 50, 70)]),
            ("Supertrend SENSEX", "st", "SENSEX",
             [dict(st_n=sn, st_k=sk) for sn in (7, 10, 14) for sk in (2.0, 2.5, 3.0, 3.5, 4.0)])):
        print(f"\n### {title}: neighbouring settings added to the best book "
              f"(base year {b['year']:+,}, IN {b['ins']:+,}, OUT {b['out']:+,})\n", flush=True)
        better = 0
        for g in grid:
            PAR.update(dict(st_n=10, st_k=3.0, bb_n=20, bb_k=2.0, bb_look=50), **g)
            _, s = R.row("x", book + leg(n, kind))
            ok = s["ins"] > b["ins"] and s["out"] > b["out"]
            better += ok
            print(f"{g}: year {s['year']:+,} IN {s['ins']:+,} OUT {s['out']:+,} DD {s['dd']:,}"
                  + ("  BETTER BOTH" if ok else ""), flush=True)
        print(f"{better} of {len(grid)} settings better in both halves", flush=True)


if __name__ == "__main__" and os.environ.get("ROBUST"):
    robust()
