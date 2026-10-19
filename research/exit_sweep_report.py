#!/usr/bin/env python3
"""research/exit_sweep.jsonl as one table, best year profit first.

    venv/bin/python research/exit_sweep_report.py > research/exit_sweep_table.md
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = [json.loads(ln) for ln in open(os.path.join(ROOT, "research", "exit_sweep.jsonl"))]
rows.sort(key=lambda r: -r["year"])
cols = ["Setting", "Trades", "Win %", "Avg win", "Avg loss", "RR", "PF", "Per trade",
        "Year Rs", "IN", "OUT", "Per day", "Per week", "Per month", "Win days %",
        "Win weeks %", "Best day", "Worst day", "Max DD", "Both halves +"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for r in rows:
    ok = "yes" if r["ins"] > 0 and r["out"] > 0 else "no"
    print("| " + " | ".join(str(x) for x in [
        r["label"], r["n"], r["win"], r["avg_win"], r["avg_loss"], r["rr"], r["pf"],
        r["per_trade"], f"{r['year']:+,}", f"{r['ins']:+,}", f"{r['out']:+,}",
        f"{r['per_day']:+,}", f"{r['per_week']:+,}", f"{r['per_month']:+,}",
        r["win_days"], r["win_weeks"], f"{r['best_day']:+,}", f"{r['worst_day']:+,}",
        f"{r['dd']:,}", ok]) + " |")
