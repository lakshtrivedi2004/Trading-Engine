#!/usr/bin/env python3
"""Broker API hygiene: every REST call is logged, and nothing hammers Angel.

A fake SmartAPI plays the broker. No network, no orders.
"""
import csv
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb import apilog

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" | {detail}" if detail else ""))


def section(t):
    print(f"\n[{t}]")


def wait_rows(path, n, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            with open(path) as fh:
                rows = list(csv.DictReader(fh))
            if len(rows) >= n:
                return rows
        time.sleep(0.05)
    return []


# ─────────────────────────────────────────────────────────────────────────────
section("1. Every REST call lands in api_calls.csv, with its outcome")


class Api:
    def ltpData(self, *a):
        return {"status": True, "data": {"ltp": 1.0}}

    def orderBook(self):
        return {"status": False, "message": "Access denied because of exceeding access rate"}

    def getCandleData(self, p):
        raise RuntimeError("Couldn't parse the JSON response received from the server: "
                           "b'Access denied because of exceeding access rate'")

    def position(self):
        return None

    def not_an_endpoint(self):
        return 1


path = os.path.join(tempfile.mkdtemp(), "api_calls.csv")
api = apilog.instrument(Api(), path=path)
check("instrumenting twice is a no-op", apilog.instrument(api, path=path) is api
      and hasattr(api.ltpData, "__wrapped__") and not hasattr(api.ltpData.__wrapped__, "__wrapped__"))
api.ltpData("NSE", "NIFTY", "1")
api.orderBook()
try:
    api.getCandleData({})
    raised = False
except RuntimeError:
    raised = True
api.position()
api.not_an_endpoint()
check("a failing call still raises to its caller", raised)
rows = wait_rows(path, 4)
got = {r["endpoint"]: r["outcome"] for r in rows}
check("four calls logged, the non-endpoint not", len(rows) == 4, str(got))
check("ok / rate_limited (dict) / rate_limited (raised) / error (None) told apart",
      got == {"ltpData": "ok", "orderBook": "rate_limited",
              "getCandleData": "rate_limited", "position": "error"}, str(got))
check("rows carry pid and thread", all(r["pid"] == str(os.getpid()) and r["thread"] for r in rows))
check("a comma in an error message cannot break the CSV",
      all(len(r) == len(apilog.HEADER.split(",")) for r in rows))

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
