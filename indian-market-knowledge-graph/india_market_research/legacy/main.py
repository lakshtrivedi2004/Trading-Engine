"""
India Market Research System
Run:  python main.py            (downloads data, writes the report)
      python main.py --demo     (simulated data, no internet needed)
      python main.py --refresh  (force fresh download even if cached today)
      python main.py --open     (open the report in your browser when done)
"""
import argparse
import os
import warnings
import webbrowser

import pandas as pd

import config
from data import BASE_DIR, load_events, load_flows, load_market_data
from decompose import build_factor_frame, decompose, explain_day
from events import run_event_studies
from flows import analyse_flows
from patterns import run_pattern_tests
from regime import all_regimes, regime_text
from report import build_report

warnings.filterwarnings("ignore", category=RuntimeWarning)


def main():
    ap = argparse.ArgumentParser(description="Research report for Nifty 50, Sensex, Bank Nifty, Bankex")
    ap.add_argument("--demo", action="store_true", help="use simulated data (no internet)")
    ap.add_argument("--refresh", action="store_true", help="force re-download")
    ap.add_argument("--start", default=config.START_DATE, help="history start date, YYYY-MM-DD")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    args = ap.parse_args()

    if args.demo:
        from demo_data import make_demo_data
        indices, factors, notes = make_demo_data()
    else:
        indices, factors, notes = load_market_data(args.start, args.refresh)
    if not indices:
        print("No index data could be loaded. Check your internet connection, or run with --demo.")
        return

    print("Decomposing daily moves...")
    decomps = {}
    for name, df in indices.items():
        frame = build_factor_frame(name, df, indices, factors)
        decomps[name] = decompose(frame)

    print("Assessing market regime...")
    regimes, vix = all_regimes(indices, factors)

    print("Running event studies...")
    event_results = run_event_studies(indices, factors, load_events())

    print("Testing patterns...")
    timing, cond = run_pattern_tests(indices)

    flow_res = analyse_flows(load_flows(), indices.get("Nifty 50")) if not args.demo else None

    asof = max(df.index.max() for df in indices.values())
    html = build_report(asof, indices, decomps, regimes, vix, event_results, timing, cond,
                        flow_res, notes, demo=args.demo)

    out_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(out_dir, exist_ok=True)
    tag = "demo_" if args.demo else ""
    path = os.path.join(out_dir, f"{tag}report_{asof:%Y-%m-%d}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    # CSV exports so you can dig into the numbers yourself
    csv_dir = os.path.join(out_dir, "csv")
    os.makedirs(csv_dir, exist_ok=True)
    for name, (c, _) in decomps.items():
        if c is not None:
            c.to_csv(os.path.join(csv_dir, f"{tag}decomposition_{name.replace(' ', '_')}.csv"))
    regimes.to_csv(os.path.join(csv_dir, f"{tag}regime.csv"))
    timing.to_csv(os.path.join(csv_dir, f"{tag}timing_rules.csv"), index=False)
    cond.to_csv(os.path.join(csv_dir, f"{tag}recurring_effects.csv"), index=False)
    ev_rows = [{"index": n, "event": l, **{k: v for k, v in r.items() if k != "path"}}
               for n, res in event_results.items() for l, r in res.items()]
    pd.DataFrame(ev_rows).to_csv(os.path.join(csv_dir, f"{tag}events.csv"), index=False)

    print("\n" + "=" * 70)
    for name, (c, _) in decomps.items():
        print(explain_day(name, c), "\n")
    for line in regime_text(regimes, vix):
        print(line)
    if notes:
        print("\nData notes:")
        for n in notes:
            print(" -", n)
    print("=" * 70)
    print(f"\nReport saved: {path}")
    if args.open:
        webbrowser.open("file://" + os.path.abspath(path))


if __name__ == "__main__":
    main()
