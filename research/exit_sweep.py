#!/usr/bin/env python3
"""Every entry / strike / target / trail setting, ranked by real rupees (owner, 16-19 Oct 2026).

    nice -n 19 venv/bin/python research/exit_sweep.py [capital]

Capital defaults to Rs 11,000 - the live account (9,979 at the 16 Oct open,
+1,132 that day) - not the config's 13,467 ceiling: the per-trade risk cap,
the strike choice and the daily loss halt are all computed on it.

"Choose the best combination of win rate and risk-reward that gives maximum
profit, independent of the number of trades - just real profit."

Stage 1 moves one setting at a time off today's live config. Stage 2 is greedy:
starting from the live config, it adds the stage-1 winners in order of gain and
keeps each one only if the year's profit rises AND both halves (split 25 May
2026) stay profitable - a setting that earns only in one half is a fit to that
half, not an edge. Ranking is on the year's net rupees after costs; win rate,
realised reward:risk (avg win / avg loss) and drawdown are printed alongside.

Each finished variant is appended to research/exit_sweep.jsonl and skipped on
a rerun, so a suspended laptop resumes instead of starting over.

19 Oct additions: the retest window (owner: "a true retest happens within
4-5 one-minute candles" - find the real number), and the strike choice (owner:
"buy the dearer, higher-delta option that moves more"). The selector already
buys the dearest strike whose stop-out loss fits max_risk_pct_per_trade;
min_delta asks whether REFUSING trades where only a cheap strike fits pays,
and max_risk_pct_per_trade (REPORT ONLY - the owner approves risk changes from
a table) is what would let it buy dearer strikes.

Caveat: research/lab/realistic.py moves the PREMIUM stop to breakeven at +1R
whatever trail_trigger_r says; trail_trigger_r moves only the index-side stop.
"""
import copy
import json
import os
from datetime import date
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research"), os.path.join(ROOT, "research", "lab"), ROOT,
                os.path.join(ROOT, "tools")]
import backtest as bt                                   # noqa: E402
import realistic as rz                                   # noqa: E402
from common import Features, summ                        # noqa: E402
from playbook_lab import INDICES, candidates, live_gates  # noqa: E402
from fixes_test import priced                            # noqa: E402
from arb.config import IntradayLimits                    # noqa: E402

OUT = os.path.join(ROOT, "research", "exit_sweep.jsonl")

CAPITAL = float(sys.argv[1]) if len(sys.argv) > 1 else 11000.0
REPORT_ONLY = {"max_risk_pct_per_trade"}       # shown in the table, never auto-adopted

STAGE1 = (
    [("retest_max_bars", v) for v in (3, 4, 5, 6, 8, 15)]
    + [("min_delta", v) for v in (0.3, 0.4)]
    + [("max_risk_pct_per_trade", v) for v in (10.0, 12.0)]
    +
    [("target_percentile", v) for v in (40.0, 60.0)]
    + [("target_horizon_bars", v) for v in (15, 45)]
    + [("min_target_r", v) for v in (0.75, 1.5)]
    + [("max_target_r", v) for v in (3.0, 6.0)]
    + [("trail_trigger_r", v) for v in (0.5, 0.75)]
    + [("time_stop_min", v) for v in (0.0, 60.0)]
    + [("on_target", "exit")]
    + [("on_target+giveback", ("trail", g)) for g in (0.25, 0.5, 1.0)]
    + [("extend_any_bar", 10)]
    + [("target_mode", "structure")]
    + [("fixed_rr", v) for v in (1.5, 3.0)]
)


def expand(key, val):
    """A stage-1 knob as config overrides."""
    if key == "on_target+giveback":
        return {"on_target": val[0], "extend_giveback_r": val[1]}
    if key == "extend_any_bar":
        return {"max_target_extensions": val, "extend_body_atr": 0.0, "extend_close_pos": 0.5}
    if key == "fixed_rr":
        return {"target_mode": "fixed", "reward_risk": val, "on_target": "exit"}
    return {key: val}


def stats(tr, days):
    """Per trade, and per calendar unit over EVERY trading day in the data -
    a day with no trade counts as a zero day, so these are what the account
    would actually have averaged."""
    s = summ(tr)
    wins = [t["net2"] for t in tr if t["net2"] > 0]
    loss = [-t["net2"] for t in tr if t["net2"] < 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    by_day = {d: 0.0 for d in days}
    for t in tr:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net2"]
    by_week, by_month = {}, {}
    for d, v in by_day.items():
        y, w, _ = date.fromisoformat(d).isocalendar()
        by_week[(y, w)] = by_week.get((y, w), 0.0) + v
        by_month[d[:7]] = by_month.get(d[:7], 0.0) + v
    traded = [v for d, v in by_day.items() if v != 0.0]
    return dict(n=s["n"], win=round(s["win"], 1), year=round(s["y"]), ins=round(s["i"]),
                out=round(s["o"]), dd=round(s["dd"]), avg_win=round(aw), avg_loss=round(al),
                rr=round(aw / al, 2) if al else 0.0,
                pf=round(sum(wins) / sum(loss), 2) if loss else 0.0,
                per_trade=round(s["avg"]), days=len(by_day), trade_days=len(traded),
                per_day=round(sum(by_day.values()) / max(1, len(by_day))),
                per_week=round(sum(by_week.values()) / max(1, len(by_week))),
                per_month=round(sum(by_month.values()) / max(1, len(by_month))),
                win_days=round(100 * sum(v > 0 for v in traded) / max(1, len(traded))),
                win_weeks=round(100 * sum(v > 0 for v in by_week.values()) / max(1, len(by_week))),
                best_day=round(max(by_day.values(), default=0)),
                worst_day=round(min(by_day.values(), default=0)),
                trades=[[t["day"], round(t["net2"]), t["why"], t.get("held", 0)] for t in tr])


class Runner:
    def __init__(self):
        self.L = IntradayLimits.load()
        rz.L = self.L
        self.capital = CAPITAL
        self.IX = {n: rz.Index(n) for n in INDICES}
        self.F = Features(self.IX)
        self.days = sorted({r[0][:10] for ix in self.IX.values() for r in ix.rows})
        self.done = {}
        if os.path.exists(OUT):
            for ln in open(OUT):
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue                # a line torn by a power cut: rerun it
                self.done[r["label"]] = r

    def __call__(self, label, over):
        if label in self.done:
            return self.done[label]
        sets = dict(over, use_regime_gate=False, playbooks=list(self.L.playbooks))
        C = candidates(sets, 6)
        Lv = copy.copy(self.L)                  # the pricing step sees the overrides too
        for k, v in over.items():
            setattr(Lv, k, v)
        rz.L = Lv
        ts = live_gates(priced(C, self.IX, self.capital, True), self.F, Lv)
        # bt.run's halt is a percent of a Rs 10,000 book (research/lab/common.py)
        tr = bt.run(sorted(ts, key=lambda t: (t["day"], t["m"], t["u"])), side_cooldown=30,
                    halt_pct=Lv.max_daily_loss_pct * self.capital / 10000.0)
        rz.L = self.L
        r = dict(label=label, over=over, **stats(tr, self.days))
        with open(OUT, "a") as f:
            f.write(json.dumps(r) + "\n")
        self.done[label] = r
        print(show(r), flush=True)
        return r


def show(r):
    return (f"{r['label']:44s} n={r['n']:3d} win={r['win']:4.1f}% RR {r['rr']:4.2f} "
            f"(+{r['avg_win']:,}/-{r['avg_loss']:,}) PF {r['pf']:4.2f} year Rs{r['year']:+8,} "
            f"(IN {r['ins']:+7,} / OUT {r['out']:+7,}) /day {r['per_day']:+4,} "
            f"/week {r['per_week']:+5,} /month {r['per_month']:+6,} maxDD {r['dd']:6,}")


def main():
    print(f"capital Rs {CAPITAL:,.0f}", flush=True)
    go = Runner()
    base = go("LIVE (today)", {})
    one = []
    for key, val in STAGE1:
        over = expand(key, val)
        one.append(go(f"{key}={val}", over))

    print("\n== stage 1, by year profit (both halves > 0 marked *)")
    for r in sorted(one + [base], key=lambda r: -r["year"]):
        print(("* " if r["ins"] > 0 and r["out"] > 0 else "  ") + show(r))

    # Stage 2: greedy combination, one setting per knob, best gain first.
    best_per_knob = {}
    for (key, _), r in zip(STAGE1, one):
        knob = "target_mode" if key in ("fixed_rr", "target_mode") else \
            "on_target" if key.startswith("on_target") or key == "extend_any_bar" else key
        if key in REPORT_ONLY:
            continue
        if r["year"] > base["year"] and r["ins"] > 0 and r["out"] > 0:
            if knob not in best_per_knob or r["year"] > best_per_knob[knob]["year"]:
                best_per_knob[knob] = r
    cur, cur_over = base, {}
    print("\n== stage 2, greedy")
    for r in sorted(best_per_knob.values(), key=lambda r: -r["year"]):
        trial = dict(cur_over, **r["over"])
        lbl = "COMBO " + " ".join(f"{k}={v}" for k, v in sorted(trial.items()))
        t = go(lbl, trial)
        keep = t["year"] > cur["year"] and t["ins"] > 0 and t["out"] > 0
        print(("KEEP " if keep else "drop ") + show(t), flush=True)
        if keep:
            cur, cur_over = t, trial
    print("\n== BEST")
    print(show(cur))
    print("overrides:", json.dumps(cur_over))


if __name__ == "__main__":
    main()
