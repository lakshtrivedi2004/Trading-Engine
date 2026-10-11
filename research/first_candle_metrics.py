#!/usr/bin/env python3
"""Every accuracy and performance metric for the first-candle body break
(research/first_candle_test.py), per rule, entry mode, timeframe and index.

  signal     setups (first candles of the rule's colour), trades, trigger rate
  accuracy   win rate after charges (+ Wilson 95% range), before charges, the win
             rate needed to break even, target / stop / time exits, and whether
             the INDEX was on the trade's side 15 / 30 min after entry and at 15:14
  money      net, gross, charges, expectancy (Rs and R), median, avg win / loss,
             payoff, largest win / loss, profit factor, return on premium and on
             the capital one lot ties up
  risk       max drawdown, longest win / loss streaks, trade std, daily Sharpe and
             Sortino (annualised, no-trade sessions count as 0), recovery factor
  path       MFE / MAE in % of the entry premium, losers that were +5% first,
             holding minutes for winners and losers
  stability  first / second half, months positive
  statistics t and two-sided p of the mean trade (Student t), and how many trades
             the current mean and spread would need to be significant at 5%

    venv/bin/python research/first_candle_metrics.py > research/first_candle_metrics.txt
"""
import contextlib, csv, io, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import first_candle_test as fc                      # noqa: E402

OUT_CSV = os.path.join(HERE, "first_candle_metrics.csv")
RULES = {
    "rule 1: red -> CE": lambda r: r.get("color") == "red" and r["variant"] == "mirror",
    "rule 2: green -> PE (mirror)": lambda r: r.get("color") == "green" and r["variant"] == "mirror",
    "rules 1+2 (mirror)": lambda r: r.get("color") in ("red", "green") and r["variant"] == "mirror",
    "rule 2: green -> PE (as written, no stop)": lambda r: r.get("color") == "green" and r["variant"] == "as_written",
}


def _betacf(a, b, x):
    """Continued fraction of the regularised incomplete beta (Numerical Recipes)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c if abs(1.0 + aa / c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def _ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_pvalue(t, df):
    """Two-sided p of Student's t with df degrees of freedom."""
    if df <= 0 or not math.isfinite(t):
        return float("nan")
    return _ibeta(df / 2.0, 0.5, df / (df + t * t))


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return 100 * (mid - half), 100 * (mid + half)


def streaks(wins):
    best_w = best_l = cur = 0
    last = None
    for w in wins:
        cur = cur + 1 if w == last else 1
        last = w
        if w:
            best_w = max(best_w, cur)
        else:
            best_l = max(best_l, cur)
    return best_w, best_l


def metrics(group, sessions, split, index_sessions):
    """group: the rule's rows of one (entry, tf, index set) - a trade or a setup
    that never triggered; sessions: the window's dates, for the daily series."""
    setups = [r for r in group if r.get("color")]
    tr = sorted((r for r in group if r["status"] == "trade"),
                key=lambda r: (r["day"], r["entry_t"], r["index"]))
    n = len(tr)
    m = {"index-sessions": index_sessions,
         "setups": len(setups), "trades": n,
         "trigger rate %": 100 * n / len(setups) if setups else float("nan")}
    if n == 0:
        return m
    net = np.array([r["net"] for r in tr], dtype=float)
    gross = np.array([r["pts"] * r["lot"] for r in tr], dtype=float)
    fees = np.array([r["fees"] for r in tr], dtype=float)
    wins, losses = net[net > 0], net[net <= 0]
    k = len(wins)
    lo, hi = wilson(k, n)
    avg_w = wins.mean() if k else 0.0
    avg_l = losses.mean() if len(losses) else 0.0
    why = [r["why"] for r in tr]
    cap = np.array([r["entry_prem"] * r["lot"] for r in tr], dtype=float)

    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    sw, sl = streaks([x > 0 for x in net])
    daily = {d: 0.0 for d in sessions}
    for r in tr:
        daily[r["day"]] += r["net"]
    dv = np.array([daily[d] for d in sessions])
    sd = dv.std(ddof=1)
    down = math.sqrt(np.mean(np.minimum(dv, 0.0) ** 2))
    std = net.std(ddof=1) if n > 1 else float("nan")
    t = net.mean() / (std / math.sqrt(n)) if n > 1 and std > 0 else float("nan")
    # Only a positive mean is an edge to prove; a losing one has none.
    need = (1.96 * std / net.mean()) ** 2 if n > 1 and net.mean() > 0 else float("nan")
    months = {}
    for r in tr:
        months[r["day"][:7]] = months.get(r["day"][:7], 0.0) + r["net"]
    los = [r for r in tr if r["net"] <= 0]
    wns = [r for r in tr if r["net"] > 0]

    m.update({
        # accuracy
        "win rate %": 100 * k / n,
        "win rate 95% low %": lo, "win rate 95% high %": hi,
        "win rate before charges %": 100 * np.mean([r["pts"] > 0 for r in tr]),
        "break-even win rate %": 100 * -avg_l / (avg_w - avg_l) if (avg_w - avg_l) > 0 else float("nan"),
        "target hit %": 100 * why.count("target") / n,
        "stop hit %": 100 * (why.count("stop") + why.count("open_level")) / n,
        "time exit %": 100 * why.count("time") / n,
        "index right way +15 min %": 100 * np.mean([r["dir_15"] for r in tr]),
        "index right way +30 min %": 100 * np.mean([r["dir_30"] for r in tr]),
        "index right way at 15:14 %": 100 * np.mean([r["dir_eod"] for r in tr]),
        # money
        "net Rs": net.sum(), "gross Rs (before charges)": gross.sum(), "charges Rs": fees.sum(),
        "charges per trade Rs": fees.mean(),
        "expectancy Rs/trade": net.mean(), "median trade Rs": float(np.median(net)),
        "expectancy R (per avg loss)": net.mean() / -avg_l if avg_l < 0 else float("nan"),
        "avg win Rs": avg_w, "avg loss Rs": avg_l,
        "payoff (avg win / avg loss)": avg_w / -avg_l if avg_l < 0 else float("nan"),
        "largest win Rs": net.max(), "largest loss Rs": net.min(),
        "profit factor": wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf"),
        "avg return on premium, before charges %": float(np.mean([r["ret_pct"] for r in tr])),
        "avg return on premium, after charges %": float(np.mean(100 * net / cap)),
        "avg capital per trade Rs": cap.mean(),
        "net / one lot's capital %": 100 * net.sum() / cap.mean(),
        # risk
        "max drawdown Rs": dd,
        "recovery factor (net / DD)": net.sum() / dd if dd > 0 else float("inf"),
        "longest win streak": sw, "longest loss streak": sl,
        "trade std Rs": std,
        "Sharpe (daily, annualised)": dv.mean() / sd * math.sqrt(252) if sd > 0 else float("nan"),
        "Sortino (daily, annualised)": dv.mean() / down * math.sqrt(252) if down > 0 else float("nan"),
        # path
        "avg MFE % of premium": float(np.mean([r["mfe_pct"] for r in tr])),
        "avg MAE % of premium": float(np.mean([r["mae_pct"] for r in tr])),
        "losers' avg MFE %": float(np.mean([r["mfe_pct"] for r in los])) if los else float("nan"),
        "losers that were +5% first %": 100 * np.mean([r["mfe_pct"] >= 5 for r in los]) if los else float("nan"),
        "avg hold min": float(np.mean([r["hold_min"] for r in tr])),
        "avg hold min, winners": float(np.mean([r["hold_min"] for r in wns])) if wns else float("nan"),
        "avg hold min, losers": float(np.mean([r["hold_min"] for r in los])) if los else float("nan"),
        # stability
        "net 1st half Rs": sum(r["net"] for r in tr if r["day"] < split),
        "net 2nd half Rs": sum(r["net"] for r in tr if r["day"] >= split),
        "months positive %": 100 * np.mean([v > 0 for v in months.values()]),
        # statistics
        "t-stat of mean trade": t,
        "p-value (two-sided)": t_pvalue(t, n - 1),
        "trades to prove edge (p<0.05)": need,
    })
    return m


SECTIONS = [("SIGNAL", ["index-sessions", "setups", "trades", "trigger rate %"]),
            ("ACCURACY", ["win rate %", "win rate 95% low %", "win rate 95% high %", "win rate before charges %",
                          "break-even win rate %", "target hit %", "stop hit %", "time exit %",
                          "index right way +15 min %", "index right way +30 min %", "index right way at 15:14 %"]),
            ("MONEY", ["net Rs", "gross Rs (before charges)", "charges Rs", "charges per trade Rs",
                       "expectancy Rs/trade", "median trade Rs", "expectancy R (per avg loss)", "avg win Rs",
                       "avg loss Rs", "payoff (avg win / avg loss)", "largest win Rs", "largest loss Rs",
                       "profit factor", "avg return on premium, before charges %",
                       "avg return on premium, after charges %", "avg capital per trade Rs", "net / one lot's capital %"]),
            ("RISK", ["max drawdown Rs", "recovery factor (net / DD)", "longest win streak", "longest loss streak",
                      "trade std Rs", "Sharpe (daily, annualised)", "Sortino (daily, annualised)"]),
            ("TRADE PATH", ["avg MFE % of premium", "avg MAE % of premium", "losers' avg MFE %",
                            "losers that were +5% first %", "avg hold min", "avg hold min, winners",
                            "avg hold min, losers"]),
            ("STABILITY", ["net 1st half Rs", "net 2nd half Rs", "months positive %"]),
            ("STATISTICS", ["t-stat of mean trade", "p-value (two-sided)", "trades to prove edge (p<0.05)"])]


def cell(v, key):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    if key.startswith("p-value"):
        return f"{v:.3f}"
    if key in ("index-sessions", "setups", "trades", "longest win streak", "longest loss streak"):
        return f"{int(v):d}"
    if key == "trades to prove edge (p<0.05)":
        return ">10k" if v > 10000 else f"{v:,.0f}"
    if "Rs" in key and "%" not in key:
        return f"{v:+,.0f}" if key not in ("charges Rs", "charges per trade Rs", "max drawdown Rs",
                                          "avg capital per trade Rs", "trade std Rs") else f"{v:,.0f}"
    if "%" in key:
        return f"{v:.1f}"
    return f"{v:.2f}"


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        IX, rows = fc.build_rows()
    sessions = sorted({r["day"] for r in rows})
    split = sessions[len(sessions) // 2]
    names = list(IX) + ["ALL"]
    out = []
    print(f"First-candle body break: every metric. {sessions[0]} .. {sessions[-1]}, {len(sessions)} sessions, "
          f"{', '.join(IX)}; 1 lot, modelled premiums, exact charges. Halves split at {split}.")
    print("ALL = the three indices traded side by side (index-sessions add up).")
    for mode in ("touch", "close"):
        for rule, keep in RULES.items():
            cols, head = [], []
            for tf in fc.TFS:
                for n in names:
                    g = [r for r in rows if r["entry"] == mode and r["tf"] == tf and keep(r)
                         and (n == "ALL" or r["index"] == n)]
                    nsess = len({(r["day"], r["index"]) for r in rows
                                 if r["entry"] == mode and r["tf"] == tf and (n == "ALL" or r["index"] == n)})
                    m = metrics(g, sessions, split, nsess)
                    cols.append(m)
                    head.append(f"{tf}m {n[:9]}")
                    out.append(dict({"entry": mode, "rule": rule, "tf": tf, "index": n}, **m))
            print("\n" + "=" * 188)
            print(f"{rule}  |  entry on {mode.upper()}")
            print("=" * 188)
            print(f"{'':40s}" + "".join(f" {h:>12s}" for h in head))
            for sec, keys in SECTIONS:
                print(f"{sec}")
                for key in keys:
                    print(f"  {key:38s}" + "".join(f" {cell(c.get(key), key):>12s}" for c in cols))
    keys = ["entry", "rule", "tf", "index"] + [k for _, ks in SECTIONS for k in ks]
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in out:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"\nevery metric, machine-readable: {os.path.relpath(OUT_CSV, os.path.dirname(HERE))} ({len(out)} rows)")


if __name__ == "__main__":
    main()
