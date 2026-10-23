#!/usr/bin/env python3
"""First-candle body break (owner's rules, 8 Oct 2026) on the 5 / 10 / 15-minute
first candle of the day, NIFTY / BANKNIFTY / SENSEX, every session of the last
three months of real 1-minute index candles (.cache/history, Angel).

  rule 1  RED first candle (close < open): any of the next 5 candles of the same
          timeframe trades above the open -> buy the ATM CE. Target: premium
          +10% of the entry price. Stop: the index back at the first candle's close.
  rule 2  GREEN first candle (close > open). As written ("crosses the close ->
          PE, stop at the open") the stop sits BELOW the entry, where a put is in
          profit, so it cannot work as a stop. Run two ways:
            mirror      rule 1 turned over: the index trades below the open -> ATM PE,
                        stop: the index back at the first candle's close
            as_written  the index trades below the close -> ATM PE, exit when the
                        index reaches the open (a profit level for a put); no stop

  entry   touch: the first 1-minute bar in the window that trades through the level,
                 filled at the level (or the bar's open if it gapped through)
          close: a candle of the timeframe CLOSES through the level, filled at
                 that close (the other common reading of "cross")
  exits   1-minute bars from the bar after entry; target is a limit (sell at
          +10%), stop and the 15:14 square-off are market (config exit slip);
          a bar that reaches both counts as the stop
  premium Black-Scholes on trading time (research/lab/bsmodel.py: time at
          THETA_F speed intraday, calibrated on recorded NIFTY books),
          IV = 20-day realised vol + the fitted premium, x1.15 on expiry day
          (research/lab/realistic.py), current expiry, the next one on expiry
          day (as the live engine buys), ATM strike at entry, 1 lot, buy at
          mid + spread/2, exact charges (arb/costs.py)

The index candles are real; option premiums are modelled (Angel serves no
history for expired options).

    venv/bin/python tools/fetch_history.py index 140        # once
    venv/bin/python research/first_candle_test.py > research/first_candle_result.txt
"""
import csv, os, sys
from datetime import date, datetime, timedelta
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research", "lab"), ROOT]
import realistic as rz                               # noqa: E402
from bsmodel import bs, T_years                      # noqa: E402
from arb.costs import leg_cost                       # noqa: E402

START, END = "2026-07-08", "2026-10-08"
TFS = (5, 10, 15)
WATCH = 5              # candles after the first one
TARGET = 0.10          # of the entry premium
SQUARE_M = 15 * 60 + 14   # 15:14 = session_end 15:15 - squareoff_minutes 1
NAMES = ("NIFTY", "BANKNIFTY", "SENSEX")
OUT_CSV = os.path.join(ROOT, "research", "first_candle_trades.csv")
L = rz.L


def hm(ts):
    return int(ts[11:13]) * 60 + int(ts[14:16])


def tf_candles(bars, tf, first_m=9 * 60 + 15):
    """1-minute bars of one day folded into tf-minute candles from 09:15:
    [start_m, o, h, l, c, last_bar_index]."""
    out = {}
    for k, r in enumerate(bars):
        b = (hm(r[0]) - first_m) // tf
        c = out.get(b)
        if c is None:
            out[b] = [first_m + b * tf, r[1], r[2], r[3], r[4], k]
        else:
            c[2], c[3], c[4], c[5] = max(c[2], r[2]), min(c[3], r[3]), r[4], k
    return out


def spec(variant, color, O, C):
    """(opt, trigger level, trigger dir, exit level, exit dir) or None.
    dir +1: the index trades at or above the level; -1: at or below."""
    if color == "red":
        return ("CE", O, +1, C, -1)
    if variant == "mirror":
        return ("PE", O, -1, C, +1)
    return ("PE", C, -1, O, -1)       # as_written


def premium_at(S, K, T0, exp, ts, ix, sig, opt):
    Tk = T0 - rz.THETA_F * (T0 - T_years(rz.when(ts), exp, ix.days))
    return bs(S, K, max(Tk, 0.0), sig, opt)


def trade_day(ix, d, bars, tf, variant, entry_mode):
    first = tf_candles(bars, tf)
    c0 = first.get(0)
    if c0 is None or hm(bars[0][0]) != 9 * 60 + 15:
        return {"status": "no_data"}
    O, C = c0[1], c0[4]
    if C == O:
        return {"status": "doji", "O": O, "C": C}
    color = "red" if C < O else "green"
    opt, lvl, tdir, xlvl, xdir = spec(variant, color, O, C)
    base = {"color": color, "O": O, "C": C, "body": abs(C - O), "opt": opt}
    window_end = 9 * 60 + 15 + (1 + WATCH) * tf      # exclusive
    k_in = S_in = None
    if entry_mode == "touch":
        for k in range(c0[5] + 1, len(bars)):
            r = bars[k]
            if hm(r[0]) >= window_end:
                break
            if (tdir > 0 and r[2] >= lvl) or (tdir < 0 and r[3] <= lvl):
                S_in = max(lvl, r[1]) if tdir > 0 else min(lvl, r[1])
                k_in = k
                break
    else:
        for b in range(1, 1 + WATCH):
            cb = first.get(b)
            if cb is None:
                continue
            if (tdir > 0 and cb[4] > lvl) or (tdir < 0 and cb[4] < lvl):
                k_in, S_in = cb[5], cb[4]
                break
    if k_in is None:
        return dict(base, status="no_trigger")

    d0 = date.fromisoformat(d)
    exp = ix.cal.get(d0)
    if exp is None:
        return dict(base, status="no_expiry")
    if exp == d0:                                     # expiry day: next expiry
        later = [x for x in ix.days if x > d0]
        exp = ix.cal.get(later[0]) if later else None
        if exp is None or exp == d0:
            return dict(base, status="no_expiry")
    sig = ix.iv(d0, exp == d0)
    K = round(S_in / ix.step) * ix.step
    T0 = T_years(rz.when(bars[k_in][0]), exp, ix.days)
    mid0 = bs(S_in, K, T0, sig, opt)
    sp = ix.spread(mid0)
    ask0 = mid0 + sp / 2
    tgt = ask0 * (1 + TARGET)

    def slip(px):
        return max(0.05, px - L.exit_slip_spreads * sp - L.exit_slip_frac * px)

    exit_k = exit_px = why = None
    ambiguous = False
    mfe = mae = 0.0        # best / worst bid while held, premium points from the entry price
    for k in range(k_in + 1, len(bars)):
        r = bars[k]
        ts = r[0]
        if hm(ts) >= SQUARE_M:
            bid = premium_at(r[1], K, T0, exp, ts, ix, sig, opt) - sp / 2
            exit_k, exit_px, why = k, slip(bid), "time"
            break
        hit_x = (xdir < 0 and r[3] <= xlvl) or (xdir > 0 and r[2] >= xlvl)
        fav = r[2] if opt == "CE" else r[3]           # best index price for the option this bar
        adv = r[3] if opt == "CE" else r[2]
        b_fav = premium_at(fav, K, T0, exp, ts, ix, sig, opt) - sp / 2
        hit_t = b_fav >= tgt
        if hit_x:
            ambiguous = hit_t
            S_x = min(xlvl, r[1]) if xdir < 0 else max(xlvl, r[1])
            bid = premium_at(S_x, K, T0, exp, ts, ix, sig, opt) - sp / 2
            stop_side = variant == "mirror" or color == "red"
            exit_k, exit_px, why = k, slip(bid), ("stop" if stop_side else "open_level")
            mae, mfe = min(mae, bid - ask0), max(mfe, bid - ask0)
            break
        mae = min(mae, premium_at(adv, K, T0, exp, ts, ix, sig, opt) - sp / 2 - ask0)
        if hit_t:
            bid_open = premium_at(r[1], K, T0, exp, ts, ix, sig, opt) - sp / 2
            exit_k, exit_px, why = k, max(tgt, bid_open), "target"
            mfe = max(mfe, exit_px - ask0)
            break
        mfe = max(mfe, b_fav - ask0)
    if exit_k is None:                                # data ends before 15:14
        r = bars[-1]
        bid = premium_at(r[4], K, T0, exp, r[0], ix, sig, opt) - sp / 2
        exit_k, exit_px, why = len(bars) - 1, slip(bid), "time"
    lot = ix.lot
    fees = leg_cost(ask0, lot, "BUY", "OPT", rz.CM) + leg_cost(exit_px, lot, "SELL", "OPT", rz.CM)
    net = (exit_px - ask0) * lot - fees
    # Where the INDEX was 15 / 30 minutes after entry and at the 15:14 square-off,
    # whatever the option exit: does the signal call the direction at all?
    m_in, side = hm(bars[k_in][0]), (1 if opt == "CE" else -1)

    def spot_after(mins):
        for r in bars[k_in + 1:]:
            if hm(r[0]) >= min(m_in + mins, SQUARE_M):
                return r[1]
        return bars[-1][4]
    dirs = {f"dir_{m}": side * (spot_after(m) - S_in) > 0 for m in (15, 30)}
    dirs["dir_eod"] = side * (spot_after(10 ** 4) - S_in) > 0
    return dict(base, **dirs, status="trade", entry_t=bars[k_in][0][11:16], entry_spot=round(S_in, 2),
                lot=lot, hold_min=hm(bars[exit_k][0]) - m_in,
                mfe_pct=round(100 * mfe / ask0, 2), mae_pct=round(100 * mae / ask0, 2),
                strike=K, expiry=exp.isoformat(), dte=(exp - d0).days, iv=round(sig, 4),
                entry_prem=round(ask0, 2), target_prem=round(tgt, 2), exit_t=bars[exit_k][0][11:16],
                exit_prem=round(exit_px, 2), why=why, ambiguous=ambiguous,
                pts=round(exit_px - ask0, 2), fees=round(fees, 2), net=round(net, 2),
                ret_pct=round(100 * (exit_px - ask0) / ask0, 2))


SPLIT = None   # middle session of the window, set in main(): halves are compared on it


def summary(rows):
    tr = sorted((r for r in rows if r["status"] == "trade"),
                key=lambda r: (r["day"], r["entry_t"], r["index"]))
    p = np.array([r["net"] for r in tr], dtype=float)
    if not len(p):
        return None
    eq = np.cumsum(p)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    win, loss = p[p > 0], p[p <= 0]
    pf = win.sum() / -loss.sum() if loss.sum() < 0 else float("inf")
    why = {}
    for r in tr:
        why[r["why"]] = why.get(r["why"], 0) + 1
    h1 = sum(r["net"] for r in tr if r["day"] < SPLIT)
    h2 = sum(r["net"] for r in tr if r["day"] >= SPLIT)
    return dict(n=len(p), win=100 * (p > 0).mean(), total=p.sum(), avg=p.mean(),
                avg_w=win.mean() if len(win) else 0.0, avg_l=loss.mean() if len(loss) else 0.0,
                pf=pf, dd=dd, why=why, h1=h1, h2=h2,
                amb=sum(r["ambiguous"] for r in tr),
                ret=np.mean([r["ret_pct"] for r in tr]))


def fmt(s):
    if s is None:
        return "no trades"
    why = " ".join(f"{k} {v}" for k, v in sorted(s["why"].items()))
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"n={s['n']:3d} win {s['win']:3.0f}%  net Rs {s['total']:+9,.0f}  avg {s['avg']:+6,.0f} "
            f"(win {s['avg_w']:+,.0f} / loss {s['avg_l']:+,.0f})  PF {pf}  maxDD {s['dd']:,.0f}  "
            f"halves {s['h1']:+,.0f} / {s['h2']:+,.0f}  avg ret {s['ret']:+.1f}%  [{why}]"
            + (f"  {s['amb']} bars hit both" if s["amb"] else ""))


def build_rows():
    """Every (session, index, timeframe, rule reading, entry mode): a trade or why not."""
    IX = {}
    for n in NAMES:
        path = os.path.join(ROOT, ".cache", "history", f"{n}.json")
        if os.path.exists(path):
            IX[n] = rz.Index(n)
    if not IX:
        raise SystemExit("no index history in .cache/history - run: venv/bin/python tools/fetch_history.py index 140")
    rows = []
    for n, ix in IX.items():
        days = {}
        for r in ix.rows:
            days.setdefault(r[0][:10], []).append(r)
        sess = [d for d in sorted(days) if START <= d <= END]
        print(f"{n}: {len(sess)} sessions {sess[0]} .. {sess[-1]}, lot {ix.lot}, step {ix.step:g}, "
              f"IV {ix.iv(date.fromisoformat(sess[0]), False):.3f} .. {ix.iv(date.fromisoformat(sess[-1]), False):.3f}")
        for d in sess:
            for tf in TFS:
                for variant in ("mirror", "as_written"):
                    for mode in ("touch", "close"):
                        r = trade_day(ix, d, days[d], tf, variant, mode)
                        if variant == "as_written" and r.get("color") == "red":
                            continue                 # red is the same trade under both readings
                        rows.append(dict(r, day=d, index=n, tf=tf, variant=variant, entry=mode))
    return IX, rows


def main():
    global SPLIT
    IX, rows = build_rows()
    allsess = sorted({r["day"] for r in rows})
    SPLIT = allsess[len(allsess) // 2]
    print(f"halves: {allsess[0]} .. before {SPLIT} | {SPLIT} .. {allsess[-1]}")
    print()

    def pick(n=None, tf=None, variant=None, mode=None, color=None, status=None):
        return [r for r in rows if (n is None or r["index"] == n) and (tf is None or r["tf"] == tf)
                and (mode is None or r["entry"] == mode)
                and (color is None or r.get("color") == color)
                and (variant is None or r["variant"] == variant or r.get("color") == "red")]

    for mode in ("touch", "close"):
        print("=" * 110)
        print(f"ENTRY ON {mode.upper()}  ({'first 1-min bar through the level' if mode == 'touch' else 'a timeframe candle closes through the level'})")
        print("=" * 110)
        for n in IX:
            print(f"\n{n}")
            for tf in TFS:
                red = pick(n, tf, "mirror", mode, "red")
                grn = pick(n, tf, "mirror", mode, "green")
                grn_w = pick(n, tf, "as_written", mode, "green")
                sess = {r["day"] for r in pick(n, tf, "mirror", mode)}
                nred, ngrn = len({r["day"] for r in red}), len({r["day"] for r in grn})
                print(f"  {tf:2d}-min first candle: {len(sess)} sessions, {nred} red, {ngrn} green")
                print(f"    rule 1  red  -> CE          {fmt(summary(red))}")
                print(f"    rule 2  green-> PE mirror   {fmt(summary(grn))}")
                print(f"    rule 2  green-> PE as written {fmt(summary(grn_w))}")
                print(f"    rules 1+2 (mirror)          {fmt(summary(red + grn))}")
        print()

    print("=" * 110)
    print("ALL THREE INDICES, rules 1 + 2 (mirror), by timeframe")
    print("=" * 110)
    for mode in ("touch", "close"):
        for tf in TFS:
            both = [r for n in IX for r in pick(n, tf, "mirror", mode)]
            print(f"  {mode:5s} {tf:2d}-min  {fmt(summary(both))}")
    print()
    print("By month, rules 1 + 2 (mirror), entry on touch, net Rs")
    months = sorted({r["day"][:7] for r in rows})
    print("  " + " " * 18 + "".join(f"{m:>10s}" for m in months))
    for n in IX:
        for tf in TFS:
            tr = [r for r in pick(n, tf, "mirror", "touch") if r["status"] == "trade"]
            vals = [sum(r["net"] for r in tr if r["day"][:7] == m) for m in months]
            print(f"  {n:10s} {tf:2d}-min  " + "".join(f"{v:+10,.0f}" for v in vals))

    print("\nEvery session, rules 1 + 2 (mirror), entry on touch: first-candle colour, then the trade")
    print("(R/G = red/green first candle; CE/PE target|stop|time net Rs; '-' = no trigger in the next 5 candles)")
    for n in IX:
        print(f"\n{n}")
        print(f"  {'day':10s}  " + "".join(f"{str(tf) + '-min':>34s}" for tf in TFS))
        by = {(r["day"], r["tf"]): r for r in pick(n, None, "mirror", "touch")
              if r.get("variant") == "mirror"}
        for d in sorted({r["day"] for r in rows if r["index"] == n}):
            cells = []
            for tf in TFS:
                r = by.get((d, tf))
                if r is None or r["status"] in ("no_data", "no_expiry"):
                    cells.append("n/a")
                elif r["status"] == "doji":
                    cells.append("doji")
                elif r["status"] == "no_trigger":
                    cells.append(f"{r['color'][0].upper()} -")
                else:
                    cells.append(f"{r['color'][0].upper()} {r['opt']} {r['entry_t']}-{r['exit_t']} "
                                 f"{r['why']:6s} {r['net']:+7,.0f}")
            print(f"  {d}  " + "".join(f"{c:>34s}" for c in cells))

    keys = ["day", "index", "tf", "variant", "entry", "status", "color", "O", "C", "body", "opt",
            "entry_t", "entry_spot", "strike", "expiry", "dte", "iv", "entry_prem", "target_prem",
            "exit_t", "exit_prem", "why", "ambiguous", "pts", "fees", "net", "ret_pct",
            "hold_min", "mfe_pct", "mae_pct", "dir_15", "dir_30", "dir_eod"]
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["index"], r["tf"], r["variant"], r["entry"], r["day"])):
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"\nevery session, every timeframe: {os.path.relpath(OUT_CSV, ROOT)} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
