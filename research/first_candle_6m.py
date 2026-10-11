#!/usr/bin/env python3
"""First-candle body break on the 5-minute first candle over six full months
(Apr - Sep 2026, plus 1 - 8 Oct shown apart), each index on its own:
NIFTY, BANKNIFTY, SENSEX. Same trades as research/first_candle_test.py, same
metrics as research/first_candle_metrics.py; nothing is combined across indices.

  1. every metric per index, one column per rule
  2. per month, per index: trades, win rate, net, average Rs and % per trade
     (after charges, on the premium paid),
     and the month's return on the capital one lot ties up (that index's
     average entry premium x lot over the window, so months compare)
  3. the average full month (Apr - Sep; October is partial and left out)

    venv/bin/python tools/fetch_history.py index 240    # once: Feb 2026 on, for the IV warm-up
    venv/bin/python research/first_candle_6m.py > research/first_candle_6m_result.txt
"""
import contextlib, csv, io, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import first_candle_test as fc                      # noqa: E402
import first_candle_metrics as fm                   # noqa: E402

fc.START, fc.END, fc.TFS = "2026-04-01", "2026-10-08", (5,)
FULL_MONTHS = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
MAIN = "rules 1+2 (mirror)"
OUT = {k: os.path.join(HERE, f"first_candle_6m_{k}.csv") for k in ("metrics", "monthly", "trades")}


def month_rows(tr, cap):
    """Per-month figures for one index's trades; cap = one lot's capital."""
    out = {}
    for mth in sorted({r["day"][:7] for r in tr} | set(FULL_MONTHS)):
        t = [r for r in tr if r["day"][:7] == mth]
        net = [r["net"] for r in t]
        out[mth] = {
            "trades": len(t),
            "win rate %": 100 * np.mean([x > 0 for x in net]) if t else float("nan"),
            "net Rs": sum(net),
            "avg Rs/trade": np.mean(net) if t else float("nan"),
            "avg return/trade %": np.mean([100 * r["net"] / (r["entry_prem"] * r["lot"]) for r in t])
                                  if t else float("nan"),
            "month return on capital %": 100 * sum(net) / cap if cap else float("nan"),
        }
    return out


def fmt(v, kind):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if kind == "int":
        return f"{v:.0f}"
    if kind == "rs":
        return f"{v:+,.0f}"
    if kind == "pct":
        return f"{v:+.1f}%"
    return f"{v:.1f}%"


def main():
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        IX, rows = fc.build_rows()
    print(buf.getvalue().strip())
    sessions = sorted({r["day"] for r in rows})
    split = sessions[len(sessions) // 2]
    print(f"window {sessions[0]} .. {sessions[-1]}: {len(sessions)} sessions; halves split at {split}; "
          f"5-minute first candle only; 1 lot; modelled premiums; exact charges")
    met_out, mon_out = [], []

    for mode in ("touch", "close"):
        print("\n" + "#" * 120)
        print(f"#  ENTRY ON {mode.upper()}"
              + ("  (first 1-min bar through the level)" if mode == "touch"
                 else "  (a 5-min candle closes through the level)"))
        print("#" * 120)
        for n in IX:
            nsess = len({r["day"] for r in rows if r["index"] == n and r["entry"] == mode})
            cols = []
            for rule, keep in fm.RULES.items():
                g = [r for r in rows if r["index"] == n and r["entry"] == mode and keep(r)]
                m = fm.metrics(g, sessions, split, nsess)
                cols.append(m)
                met_out.append(dict({"entry": mode, "index": n, "rule": rule}, **m))
            heads = ["rule 1 CE", "rule 2 PE", "rules 1+2", "as written"]
            print(f"\n{n}  -  every metric (rule 2 PE = mirror; 'as written' = rule 2 with no working stop)")
            print(f"{'':40s}" + "".join(f"{h:>13s}" for h in heads))
            for sec, keys in fm.SECTIONS:
                print(sec)
                for key in keys:
                    if key == "index-sessions":
                        continue
                    print(f"  {key:38s}" + "".join(f"{fm.cell(c.get(key), key):>13s}" for c in cols))

        print(f"\nPER MONTH, {MAIN}, entry on {mode}  (Oct = 1-8 Oct only, not in the average)")
        for n in IX:
            tr = [r for r in rows if r["index"] == n and r["entry"] == mode and r["status"] == "trade"
                  and fm.RULES[MAIN](r)]
            cap = float(np.mean([r["entry_prem"] * r["lot"] for r in tr])) if tr else 0.0
            per = month_rows(tr, cap)
            by_rule = {}
            for rule in ("rule 1: red -> CE", "rule 2: green -> PE (mirror)",
                         "rule 2: green -> PE (as written, no stop)"):
                t2 = [r for r in rows if r["index"] == n and r["entry"] == mode and r["status"] == "trade"
                      and fm.RULES[rule](r)]
                by_rule[rule] = {m: sum(r["net"] for r in t2 if r["day"][:7] == m) for m in per}
            print(f"\n{n}   one lot's capital Rs {cap:,.0f} (average entry premium x lot {IX[n].lot})")
            print(f"  {'month':9s}{'trades':>7s}{'win %':>8s}{'net Rs':>10s}{'avg Rs/tr':>11s}"
                  f"{'avg ret/tr':>11s}{'month ret':>11s}   {'rule1 CE':>9s}{'rule2 PE':>9s}{'as written':>11s}")
            for mth, v in per.items():
                print(f"  {mth + ('*' if mth not in FULL_MONTHS else ''):9s}{fmt(v['trades'], 'int'):>7s}"
                      f"{fmt(v['win rate %'], 'plain'):>8s}{fmt(v['net Rs'], 'rs'):>10s}"
                      f"{fmt(v['avg Rs/trade'], 'rs'):>11s}{fmt(v['avg return/trade %'], 'pct'):>11s}"
                      f"{fmt(v['month return on capital %'], 'pct'):>11s}   "
                      + "".join(f"{fmt(by_rule[r][mth], 'rs'):>{w}s}" for r, w in zip(by_rule, (9, 9, 11))))
                mon_out.append(dict({"entry": mode, "index": n, "month": mth,
                                     "full_month": mth in FULL_MONTHS, "capital_one_lot": round(cap, 2)},
                                    **{k: (round(x, 4) if isinstance(x, float) else x) for k, x in v.items()},
                                    **{f"net {r}": round(by_rule[r][mth], 2) for r in by_rule}))
            full = [per[m] for m in FULL_MONTHS if m in per]
            ft = [r for r in tr if r["day"][:7] in FULL_MONTHS]
            avg = {
                "trades": np.mean([v["trades"] for v in full]),
                "win rate %": 100 * np.mean([r["net"] > 0 for r in ft]) if ft else float("nan"),
                "net Rs": np.mean([v["net Rs"] for v in full]),
                "avg Rs/trade": np.mean([r["net"] for r in ft]) if ft else float("nan"),
                "avg return/trade %": np.mean([100 * r["net"] / (r["entry_prem"] * r["lot"]) for r in ft])
                                      if ft else float("nan"),
                "month return on capital %": np.mean([v["month return on capital %"] for v in full]),
            }
            pos = sum(v["net Rs"] > 0 for v in full)
            print(f"  {'avg month':9s}{avg['trades']:>7.1f}{fmt(avg['win rate %'], 'plain'):>8s}"
                  f"{fmt(avg['net Rs'], 'rs'):>10s}{fmt(avg['avg Rs/trade'], 'rs'):>11s}"
                  f"{fmt(avg['avg return/trade %'], 'pct'):>11s}{fmt(avg['month return on capital %'], 'pct'):>11s}   "
                  + "".join(f"{fmt(np.mean([by_rule[r][m] for m in FULL_MONTHS]), 'rs'):>{w}s}"
                            for r, w in zip(by_rule, (9, 9, 11))))
            print(f"  positive months {pos} of {len(full)}; best {fmt(max(v['net Rs'] for v in full), 'rs')}, "
                  f"worst {fmt(min(v['net Rs'] for v in full), 'rs')}; "
                  f"monthly return std {np.std([v['month return on capital %'] for v in full], ddof=1):.1f} pts")

    keys = ["entry", "index", "rule"] + [k for _, ks in fm.SECTIONS for k in ks]
    with open(OUT["metrics"], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in met_out:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    with open(OUT["monthly"], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(mon_out[0]))
        w.writeheader()
        w.writerows(mon_out)
    tkeys = ["day", "index", "tf", "variant", "entry", "status", "color", "O", "C", "body", "opt",
             "entry_t", "entry_spot", "strike", "expiry", "dte", "iv", "entry_prem", "target_prem",
             "exit_t", "exit_prem", "why", "ambiguous", "pts", "fees", "net", "ret_pct",
             "hold_min", "mfe_pct", "mae_pct", "dir_15", "dir_30", "dir_eod"]
    with open(OUT["trades"], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=tkeys, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["index"], r["variant"], r["entry"], r["day"])):
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print("\nfiles: " + ", ".join(os.path.relpath(p, os.path.dirname(HERE)) for p in OUT.values()))


if __name__ == "__main__":
    main()
