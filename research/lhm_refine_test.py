#!/usr/bin/env python3
"""Refine last-half-hour momentum, and test published intraday strategies not yet tried (owner, 21 Oct 2026).

    HISTORY_DIR=.cache/history_live nice -n 19 venv/bin/python research/lhm_refine_test.py

Real 1-minute bars 22 Sep 2025 - 21 Oct 2026 (Angel history + the engine's own
live bars). Every trade priced as the option the engine would buy
(realistic.reprice: costs inside the cap, exit slip, next expiry on expiry
day), Rs 11,000, live risk cap, 15% halt, one position at a time, side
cooldown 30. Split 25 May 2026; "last month" = 22 Sep - 21 Oct 2026.

LHM, as live: NIFTY, at 15:00 the day's move from the previous close >=
0.5 ADR(10), 4-hour trend with it, stop 1.5 ATR, out 15:13. Varied one at a time:
  entry time 14:45 - 15:05; move threshold 0.3 - 0.8 ADR;
  Gao, Han, Li & Zhou (2018, JFE): the first half-hour's return predicts the
    last half-hour's - "gao": also require the 09:45 move from the previous
    close to agree; "gao only": the first half-hour alone;
  Baltussen, Da, Lammers & Martens (2021, JFE): last-half-hour momentum is
    driven by option hedging - split by expiry day;
  more indices: SENSEX / BANKNIFTY on days NIFTY does not fire.
New: Heston, Korajczyk & Sadka (2010, J. Finance) half-hour periodicity: a
  half-hour's return tends to repeat the same half-hour's return on earlier
  days - trade slot s (09:45 ... 14:45) in the sign of its mean over the
  previous 20 sessions when |t| >= 1.5 / 2.0, stop 1.5 ATR, out after 30 min.
Then the book: the live retest playbook (ob_fvg_retest) + the best of these.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S                                       # noqa: E402
import backtest as bt                                   # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features, gate_halves                 # noqa: E402
from playbook_lab import INDICES, candidates             # noqa: E402
from fixes_test import priced                            # noqa: E402
from exit_sweep import stats                             # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

CAPITAL = 11000.0
MONTH = "2026-09-22"
L = IntradayLimits.load()
rz.L = L
IX = {n: rz.Index(n) for n in INDICES}
F = Features(IX)
PP = {n: S.prep(IX[n]) for n in INDICES}
DAYS = sorted({r[0][:10] for ix in IX.values() for r in ix.rows})
HALT = L.max_daily_loss_pct * CAPITAL / 10000.0


def price(ts):
    out = []
    for t in ts:
        r = rz.reprice(t, IX[t["u"]], CAPITAL, next_expiry_on_expiry_day=True, costs_inside=True,
                       slip_spreads=L.exit_slip_spreads, slip_frac=L.exit_slip_frac)
        if r and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0:
            u = dict(t)
            u.update(r)
            out.append(u)
    return out


def bar_at(dd, rows, hhmm):
    for i in dd["idx"]:
        if S.hm(rows[i][0]) + 1 == S.T(hhmm):
            return i
    return None


def lhm(name, at="15:00", frac=0.5, minr=1.5, last="15:13", signal="day", expiry=None, play="LHM"):
    ix, P = IX[name], PP[name]
    rows, c, atr = ix.rows, P["c"], P["atr"]
    out = []
    for d in P["keys"]:
        dd = P["D"][d]
        if not dd["sig"] or dd["adr"] <= 0:
            continue
        if expiry is not None:
            from datetime import date
            dt = date.fromisoformat(d)
            if (ix.cal.get(dt) == dt) != expiry:
                continue
        i = bar_at(dd, rows, at)
        if i is None or atr[i] <= 0:
            continue
        mv = c[i] - dd["pc"]
        i30 = bar_at(dd, rows, "09:45")
        first = (c[i30] - dd["pc"]) if i30 is not None else 0.0
        if signal == "gao_only":
            if abs(first) < 0.2 * dd["adr"]:
                continue
            side = 1 if first > 0 else -1
        else:
            if abs(mv) < frac * dd["adr"]:
                continue
            side = 1 if mv > 0 else -1
            if signal == "gao" and first * side <= 0:
                continue
        stop = c[i] - side * minr * atr[i]
        j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), atr[i], last=S.T(last))
        out.append(S.cand(name, ix, i, j, px, side, abs(c[i] - stop), why, play))
    return price(out)


def hks(name, tmin=1.5, lookback=20, minr=1.5):
    ix, P = IX[name], PP[name]
    rows, c, atr = ix.rows, P["c"], P["atr"]
    slots = [f"{h:02d}:{m:02d}" for h in range(9, 15) for m in (15, 45)
             if "09:45" <= f"{h:02d}:{m:02d}" <= "14:45"]
    ret = {}
    for d in P["keys"]:
        dd = P["D"][d]
        for s in slots:
            a = bar_at(dd, rows, s)
            b = bar_at(dd, rows, f"{(S.T(s) + 30) // 60:02d}:{(S.T(s) + 30) % 60:02d}")
            if a is not None and b is not None:
                ret[(d, s)] = (c[b] / c[a] - 1, a)
    out = []
    keys = P["keys"]
    for k, d in enumerate(keys):
        if k < lookback:
            continue
        busy = -1
        for s in slots:
            past = [ret[(p, s)][0] for p in keys[k - lookback:k] if (p, s) in ret]
            if (d, s) not in ret or len(past) < lookback * 0.8:
                continue
            m, sd = float(np.mean(past)), float(np.std(past, ddof=1))
            if sd <= 0 or abs(m) / (sd / len(past) ** 0.5) < tmin:
                continue
            i = ret[(d, s)][1]
            if i <= busy or atr[i] <= 0:
                continue
            side = 1 if m > 0 else -1
            stop = c[i] - side * minr * atr[i]
            j, px, why = S.exit_walk(rows, P, i, side, stop, dict(trail="none"), atr[i],
                                     last=S.T(s) + 30)
            out.append(S.cand(name, ix, i, j, px, side, abs(c[i] - stop), why, "HKS"))
            busy = j
    return price(out)


def row(label, ts):
    tr = bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=30, halt_pct=HALT)
    s = stats(tr, DAYS)
    mo = [t["net2"] for t in tr if t["day"] >= MONTH]
    wins = [t["net2"] for t in tr if t["net2"] > 0]
    by = {}
    for t in tr:
        by[t["plays"][0] if t.get("plays") else "?"] = by.get(t["plays"][0] if t.get("plays") else "?", 0) + 1
    return (f"| {label} | {s['n']} | {s['win']} | {s['avg_win']:,} | {s['avg_loss']:,} | {s['year']:+,} | "
            f"{s['ins']:+,} | {s['out']:+,} | {len(mo)} | {sum(mo):+,.0f} | {s['per_month']:+,} | "
            f"{s['dd']:,} | {100 * s['trade_days'] / s['days']:.0f}% | {by} |"), s


HEAD = ("| {} | Trades | Win % | Avg win | Avg loss | Year Rs | Before 25 May | After 25 May | "
        "Last month trades | Last month Rs | Per month | Max DD | Days with a trade | By strategy |\n"
        + "|---" * 14 + "|")


def table(title, rows_):
    print(f"\n### {title}\n\n" + HEAD.format("Variant"), flush=True)
    res = {}
    for label, ts in rows_:
        line, s = row(label, ts)
        res[label] = (ts, s)
        print(line, flush=True)
    return res


def main():
    print(f"{DAYS[0]} - {DAYS[-1]}, {len(DAYS)} days, Rs {CAPITAL:,.0f}, risk {L.max_risk_pct_per_trade:g}%",
          flush=True)
    live = lhm("NIFTY")
    table("LHM: entry time (NIFTY)", [(f"{a}" + (" (live)" if a == "15:00" else ""), lhm("NIFTY", at=a))
                                    for a in ("14:45", "14:50", "14:55", "15:00", "15:05")])
    table("LHM: day move threshold (NIFTY)", [(f"{f:g} ADR" + (" (live)" if f == 0.5 else ""),
                                              lhm("NIFTY", frac=f)) for f in (0.3, 0.4, 0.5, 0.6, 0.8)])
    table("LHM: signal (NIFTY)", [("day move (live)", live), ("day move + first half-hour agrees (Gao)",
                                  lhm("NIFTY", signal="gao")), ("first half-hour only (Gao)",
                                                                lhm("NIFTY", signal="gao_only"))])
    table("LHM: expiry day (NIFTY)", [("all days (live)", live), ("expiry days only",
                                      lhm("NIFTY", expiry=True)), ("non-expiry days only",
                                                                   lhm("NIFTY", expiry=False))])
    se, bn = lhm("SENSEX"), lhm("BANKNIFTY")
    nd = {t["day"] for t in live}
    se_fb = [t for t in se if t["day"] not in nd]
    sd = nd | {t["day"] for t in se_fb}
    bn_fb = [t for t in bn if t["day"] not in sd]
    lhm_res = table("LHM: indices", [("NIFTY (live)", live), ("SENSEX alone", se), ("BANKNIFTY alone", bn),
                                     ("NIFTY, else SENSEX", live + se_fb),
                                     ("NIFTY, else SENSEX, else BANKNIFTY", live + se_fb + bn_fb)])
    hk = {}
    rows_ = []
    for tmin in (1.5, 2.0):
        allx = [t for n in INDICES for t in hks(n, tmin=tmin)]
        hk[tmin] = allx
        rows_.append((f"HKS all three, |t|>={tmin:g}", allx))
        for n in INDICES:
            rows_.append((f"HKS {n}, |t|>={tmin:g}", [t for t in allx if t["u"] == n]))
    table("New: half-hour periodicity (Heston, Korajczyk & Sadka 2010)", rows_)

    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OB = [t for t in priced(C, IX, CAPITAL, True)
          if F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0 and gate_halves(t, *live_pace[t["u"]])]
    OBx = [t for t in OB if t["u"] != "NIFTY"]
    for t in OB:
        t["plays"] = ["OB"]
    best_lhm = lhm_res["NIFTY, else SENSEX, else BANKNIFTY"][0]
    table("Book: retest setup (OB) + LHM + new", [
        ("OB (live, all three) + LHM NIFTY (live today)", OB + live),
        ("OB without NIFTY + LHM NIFTY", OBx + live),
        ("OB without NIFTY + LHM NIFTY, else SENSEX", OBx + lhm_res["NIFTY, else SENSEX"][0]),
        ("OB without NIFTY + LHM NIFTY/SENSEX/BANKNIFTY", OBx + best_lhm),
        ("OB without NIFTY + LHM 3-index + HKS 2.0", OBx + best_lhm + hk[2.0]),
        ("OB without NIFTY + LHM 3-index + HKS 1.5", OBx + best_lhm + hk[1.5]),
    ])


if __name__ == "__main__" and not os.environ.get("HKS_PASS") and not os.environ.get("HKS_ROBUST"):
    main()


def book_with_hks():
    """Second pass (21 Oct): HKS slots that end by 14:45, so a half-hour trade never
    blocks the 15:00 LHM entry, added to the best book."""
    global hks
    base_hks = hks

    def early(name, tmin):
        return [t for t in base_hks(name, tmin=tmin) if t["m"] <= S.T("14:15")]
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OBx = [dict(t, plays=["OB"]) for t in priced(C, IX, CAPITAL, True)
           if t["u"] != "NIFTY" and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
           and gate_halves(t, *live_pace[t["u"]])]
    live = lhm("NIFTY")
    rows_ = [("OB without NIFTY + LHM NIFTY (best so far)", OBx + live)]
    for tmin in (1.5, 2.0):
        hn = early("NIFTY", tmin)
        hs = early("SENSEX", tmin)
        rows_ += [(f"HKS NIFTY to 14:15, |t|>={tmin:g} alone", hn),
                  (f"  + to the best book", OBx + live + hn),
                  (f"  + HKS SENSEX too", OBx + live + hn + hs)]
    table("Book + HKS ending by 14:45", rows_)


if __name__ == "__main__" and os.environ.get("HKS_PASS") and not os.environ.get("HKS_ROBUST"):
    book_with_hks()


def hks_robust():
    """Third pass (21 Oct): is the book gain from HKS robust to its own settings?"""
    C = candidates(dict(use_regime_gate=False, playbooks=list(L.playbooks)), 6)
    live_pace = dict(L.atr_by_index)
    OBx = [dict(t, plays=["OB"]) for t in priced(C, IX, CAPITAL, True)
           if t["u"] != "NIFTY" and F.h4_ret(t, int(L.trend_4h_bars or 5)) * t["side"] > 0
           and gate_halves(t, *live_pace[t["u"]])]
    live = lhm("NIFTY")
    _, b = row("base", OBx + live)
    rows_, better = [("best book without HKS", OBx + live)], 0
    grid = [(lb, tm, mr) for lb in (10, 20, 30) for tm in (1.25, 1.5, 1.75) for mr in (1.0, 1.5, 2.0)]
    print(f"\nbase: year {b['year']:+,} IN {b['ins']:+,} OUT {b['out']:+,}", flush=True)
    for lb, tm, mr in grid:
        h = [t for n in ("NIFTY", "SENSEX") for t in hks(n, tmin=tm, lookback=lb, minr=mr)
             if t["m"] <= S.T("14:15")]
        line, s = row(f"lookback {lb}, |t|>={tm:g}, stop {mr:g} ATR", OBx + live + h)
        ok = s["ins"] > b["ins"] and s["out"] > b["out"]
        better += ok
        print(line + (" BETTER BOTH" if ok else ""), flush=True)
    print(f"\n{better} of {len(grid)} HKS settings make the book better in both halves", flush=True)


if __name__ == "__main__" and os.environ.get("HKS_ROBUST"):
    hks_robust()
