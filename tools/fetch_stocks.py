#!/usr/bin/env python3
"""Fetch history for the F&O stock universe into .cache/history/stocks/.

    python tools/fetch_stocks.py daily 370              # NSE daily bars, every F&O stock
    python tools/fetch_stocks.py minute 120 [SYM ...]   # Angel 1-minute bars (after the close!)

Daily bars come from NSE's own site (arb/nse.py) and never touch the broker key.
Minute bars need Angel's historical API, which the live engine shares and which
already refuses calls for rate during market hours - run that mode after 15:30.
Files already present are skipped, so either mode can be re-run to resume.
"""
import json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.scanner import fo_universe
OUT = os.path.join(ROOT, ".cache", "history", "stocks"); os.makedirs(OUT, exist_ok=True)

def main():
    mode, days = sys.argv[1], int(sys.argv[2])
    master = json.load(open(os.path.join(ROOT, ".cache", "scrip_master.json")))
    uni = fo_universe(master)
    names = sys.argv[3:] or sorted(uni)
    if mode == "daily":
        from arb.nse import NseClient
        nse = NseClient(min_gap_sec=float(os.environ.get("GAP", "1.0")))
        for k, s in enumerate(names):
            path = f"{OUT}/{s}_1d.json"
            if os.path.exists(path):
                continue
            rows = nse.daily(s, days)
            if rows:
                json.dump(rows, open(path, "w"))
            print(f"[{k + 1}/{len(names)}] {s} {len(rows)} days {nse.last_error}", flush=True)
    elif mode == "minute":
        import logging; logging.disable(logging.CRITICAL)
        from datetime import datetime, timedelta
        from arb.config import Credentials
        from arb.feed import AngelSession
        api = AngelSession(Credentials.load()).login().api
        gap = float(os.environ.get("GAP", "1.5"))
        for k, s in enumerate(names):
            path = f"{OUT}/{s}_1m.json"
            if os.path.exists(path) or s not in uni:
                continue
            rows, end = {}, datetime.now()
            t = end - timedelta(days=days)
            while t < end:
                e = min(t + timedelta(days=28), end)
                for tries in range(4):
                    time.sleep(gap * (1 + 10 * tries))
                    try:
                        r = api.getCandleData({"exchange": "NSE", "symboltoken": uni[s]["eq_token"],
                                               "interval": "ONE_MINUTE",
                                               "fromdate": t.strftime("%Y-%m-%d 09:15"),
                                               "todate": e.strftime("%Y-%m-%d 15:30")})
                    except Exception as ex:
                        r = {"message": str(ex)[:80]}
                    if isinstance(r, dict) and r.get("status"):
                        for x in r.get("data") or []:
                            rows[x[0]] = [x[0]] + [float(v or 0) for v in x[1:6]]
                        break
                t = e
            out = [rows[x] for x in sorted(rows)]
            if out:
                json.dump(out, open(path, "w"))
            print(f"[{k + 1}/{len(names)}] {s} {len(out)} bars", flush=True)

if __name__ == "__main__":
    main()
