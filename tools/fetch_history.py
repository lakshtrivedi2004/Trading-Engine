#!/usr/bin/env python3
"""Fetch 1-minute history from Angel into .cache/history/ for tools/backtest.py.

    python tools/fetch_history.py index 370                            # 3 indices, ~1 year
    python tools/fetch_history.py options NIFTY NFO 29SEP2026 23000 24400 100 32

Paced (GAP seconds between calls, default 12) because the live engine shares the
API key's rate limit; files already present are skipped, options save as they go.
"""
import json, os, sys, time, logging
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)
from datetime import datetime, timedelta
from arb.config import Credentials
from arb.feed import AngelSession
OUT = os.path.join(ROOT, ".cache", "history"); os.makedirs(OUT, exist_ok=True)
s = AngelSession(Credentials.load()).login(); api = s.api
def get(exch, token, interval, start, end, tries=6):
    for k in range(tries):
        time.sleep(float(os.environ.get("GAP", "12")))
        try:
          r = api.getCandleData({"exchange": exch, "symboltoken": token, "interval": interval,
                               "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M")})
        except Exception as ex:
          r = {"message": str(ex)[:80]}
        if isinstance(r, dict) and r.get("status"):
            return r.get("data") or []
        time.sleep(45 * (k + 1))
    print("  gave up", token, start.date(), (r or {}).get("message") if isinstance(r, dict) else r, flush=True)
    return None
def series(name, exch, token, days, interval="ONE_MINUTE", chunk=28):
    path = f"{OUT}/{name}.json"
    rows = {}
    end = datetime.now()
    start = end - timedelta(days=days)
    t = start
    while t < end:
        e = min(t + timedelta(days=chunk), end)
        d = get(exch, token, interval, t.replace(hour=9, minute=15), e)
        print("  chunk", name, t.date(), len(d or []) if d is not None else "FAILED", flush=True)
        for x in d or []:
            rows[x[0]] = [x[0]] + [float(v or 0) for v in x[1:6]]
        t = e
    out = [rows[k] for k in sorted(rows)]
    json.dump(out, open(path, "w"))
    print(name, len(out), out[0][0] if out else "", out[-1][0] if out else "", flush=True)
    return out
if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "index":
        days = int(sys.argv[2])
        for name, exch, tok in (("NIFTY", "NSE", "99926000"), ("BANKNIFTY", "NSE", "99926009"), ("SENSEX", "BSE", "99919000")):
            if os.path.exists(f"{OUT}/{name}.json"): continue
            series(name, exch, tok, days)
    elif mode == "options":
        # args: name exch_seg expiry lo hi step days
        name, seg, expiry, lo, hi, step, days = sys.argv[2], sys.argv[3], sys.argv[4], *map(float, sys.argv[5:8]), int(sys.argv[8])
        master = json.load(open(os.path.join(ROOT, ".cache", "scrip_master.json")))
        want = {}
        for x in master:
            if x.get("name") == name and x.get("expiry") == expiry and x.get("instrumenttype") == "OPTIDX":
                k = float(x["strike"]) / 100
                if lo <= k <= hi and abs(k / step - round(k / step)) < 1e-6:
                    want[(k, x["symbol"][-2:])] = x["token"]
        end = datetime.now(); start = (end - timedelta(days=days)).replace(hour=9, minute=15)
        path = f"{OUT}/{name}_{expiry}_opts.json"
        out = json.load(open(path)) if os.path.exists(path) else {}
        for (k, opt), tok in sorted(want.items(), key=lambda kv: (abs(kv[0][0] - (lo + hi) / 2), kv[0])):
            if f"{int(k)}{opt}" in out: continue
            d = get(seg, tok, "ONE_MINUTE", start, end)
            if d is None: continue
            out[f"{int(k)}{opt}"] = [[x[0]] + [float(v or 0) for v in x[1:6]] for x in d]
            print(name, expiry, int(k), opt, len(out[f"{int(k)}{opt}"]), flush=True)
            json.dump(out, open(path, "w"))
