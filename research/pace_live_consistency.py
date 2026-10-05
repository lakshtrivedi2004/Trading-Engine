#!/usr/bin/env python3
"""Does the live engine's ATR pace equal the pace the backtest computes?

    venv/bin/python research/pace_live_consistency.py > research/pace_live_consistency_result.txt

Read-only: no API, no engine contact. Sources
  logs/*session_2026{0929..1005}_*.log  the "| pace NIFTY 1.46x ..." line, printed
                     once a minute by run_bot.py from engine._atr_ratio(): candles.get(
                     limit=250), which INCLUDES the forming bar (CandleStore.get
                     appends self.live). The entry gate (regime_gate in
                     _evaluate) gets the same rows after _closed_rows() drops the
                     forming bar, at most entry_window_sec after a bar closed.
  .cache/candles/<X>_1m.json   the engine's own 1-minute store, pinned to
                     research_out/pace_live/final_<date>/ on first run after 15:30.
                     History refreshes overwrite live-built bars, so these are
                     (almost all) Angel history bars by the time we read them.
  research_out/pace_live/snaps/HHMMSS/   copies of .cache/candles taken every
                     10 s on 5 Oct from 14:49. _sample_live saves the store on
                     every minute roll and every history refresh, so a snapshot is
                     the engine's closed-bar memory at that moment (live-built
                     bars included, the forming bar not).
  .cache/history/<X>.json      the backtest's series (tools/backtest.py,
                     research/lab/realistic.py:load_rows, 09:15-15:29, seg = last
                     400 closed bars), ending 2026-09-30.

Sections
  A  every logged pace reading against arb.regime.atr_ratio on the final bars:
     closed bars only / + the full bar of the logging minute / + a partial
     estimate of the forming bar (high/low shrunk toward the open by sqrt(s/60)).
  B  live store vs backtest history, 28-30 Sep, bar by bar and as pace per minute.
  C  5 Oct snapshots: the gate's exact input (closed bars the engine held) vs
     the final history bars for the same minute, and live-built bars vs the
     history bar that later replaced them.
"""
import bisect
import glob
import json
import math
import os
import re
import shutil
import statistics as stx
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.config import IntradayLimits   # noqa: E402
from arb.regime import atr_ratio, true_range   # noqa: E402

OUT = os.path.join(ROOT, "research_out", "pace_live")
L = IntradayLimits.load()
P, B = int(L.atr_period), int(L.atr_baseline_bars)
HALVES = dict(L.atr_by_index or {})
SPLIT = str(getattr(L, "atr_half_split", "12:15"))
LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] ticks=([\d,]+) .*\| pace (.*?) \(need")
HIST = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[candles\] ([A-Z]+): (history unavailable|historical API recovered)")


def ratio(rows):
    return atr_ratio(rows, period=P, baseline_bars=B)


def need(name, hhmm):
    h = HALVES.get(name)
    if not h:
        return float(L.min_atr_ratio)
    return float(h[1] if hhmm >= SPLIT else h[0])


def q(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(math.ceil(p * len(xs))) - 1))
    return xs[k]


def stats(label, d):
    if not d:
        return f"  {label:34s} n=0"
    a = [abs(x) for x in d]
    return (f"  {label:34s} n={len(d):4d} median|d|={stx.median(a):.3f} "
            f"p95|d|={q(a, .95):.3f} max|d|={max(a):.3f} mean d={stx.mean(d):+.3f} "
            f"<=0.01: {100 * sum(x <= 0.0101 for x in a) / len(a):3.0f}% "
            f"<=0.05: {100 * sum(x <= 0.0501 for x in a) / len(a):3.0f}%")


# ---------------------------------------------------------------- inputs
def pin_final():
    """The live store as it stands after the close, copied once so reruns see
    the same bars (the engine keeps rewriting .cache/candles)."""
    day = datetime.now().strftime("%Y%m%d")
    dst = os.path.join(OUT, f"final_{day}")
    src = os.path.join(ROOT, ".cache", "candles")
    if not os.path.isdir(dst):
        # The engine idles from 15:15 (no history refresh after it), so the
        # store is final from then on.
        if datetime.now().strftime("%H:%M") < "15:16":
            dst = os.path.join(OUT, "provisional")
            if os.path.isdir(dst):
                shutil.rmtree(dst)
        os.makedirs(dst, exist_ok=True)
        for f in glob.glob(os.path.join(src, "*_1m.json")):
            shutil.copy(f, dst)
    return dst


def load_store(d):
    out = {}
    for f in glob.glob(os.path.join(d, "*_1m.json")):
        name = os.path.basename(f)[:-len("_1m.json")]
        rows = sorted(json.load(open(f)), key=lambda r: r[0])
        out[name] = rows
    return out


def load_history(name):
    p = os.path.join(ROOT, ".cache", "history", f"{name}.json")
    if not os.path.exists(p):
        return []
    return [r for r in json.load(open(p)) if "09:15" <= r[0][11:16] <= "15:29"]


def _sec(t):
    return int(t[:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])


def readings():
    """Every pace reading, tagged with what the engine's store was going through.

    feed_dead: the tick counter did not move since the previous status line, at
      some point in the last 20 minutes. The sampler keeps opening a bar a minute
      from the last LTP, so a dead feed writes flat bars (TR 0) into the window.
    hole: the session's first history fetch for this index was refused, and the
      engine had been down before it: the minutes it missed are absent from the
      window until a fetch succeeds (plus 20 minutes for them to roll out).
    """
    out = []
    files = sorted(glob.glob(os.path.join(ROOT, "logs", "*session_2026*.log")))
    for f in files:
        m = re.search(r"session_(\d{8})_(\d{6})\.log$", f)
        if not m or not ("20260929" <= m.group(1) <= "20261005"):
            continue
        day = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
        lines = open(f, errors="replace").read().splitlines()
        start = None
        refused_from, hole_until = {}, {}
        for ln in lines:
            g = re.match(r"^\[(\d\d:\d\d:\d\d)\]", ln)
            if g and start is None:
                start = _sec(g.group(1))
            h = HIST.match(ln)
            if h:
                t, n, what = _sec(h.group(1)), h.group(2), h.group(3)
                if what.startswith("history") and t - start < 60:
                    refused_from[n] = t
                elif what.startswith("historical") and n in refused_from and n not in hole_until:
                    hole_until[n] = t
        dead, prev = [], None
        for ln in lines:
            g = LINE.match(ln)
            if not g:
                continue
            t, ticks = _sec(g.group(1)), int(g.group(2).replace(",", ""))
            if prev is not None and ticks == prev:
                dead.append(t)
            prev = ticks
            for name, v in re.findall(r"([A-Z]+) ([\d.]+)x", g.group(3)):
                hole = name in refused_from and t <= hole_until.get(name, 10 ** 9) + 1200
                fd = any(0 <= t - d <= 1200 for d in dead)
                out.append(dict(day=day, t=g.group(1), name=name, logged=float(v),
                                log=os.path.basename(f), feed_dead=fd, hole=hole))
    return out


# ---------------------------------------------------------------- A
def partial(bar, prev_close, frac):
    """The forming bar `frac` of the way through its minute, guessed from the
    full bar: a random walk's range grows with sqrt(time)."""
    o, h, l, c = bar[1:5]
    s = math.sqrt(max(0.0, min(1.0, frac)))
    return [bar[0], o, o + (h - o) * s, o - (o - l) * s, o + (c - o) * frac, 0.0]


def section_a(store, R):
    print("A. Logged pace (run_bot.py, engine._atr_ratio, forming bar included) vs")
    print(f"   arb.regime.atr_ratio(period={P}, baseline_bars={B}) on the final stored bars")
    print("   (Angel history after the engine's refreshes).")
    print("   Variants: closed = bars before the logging minute (what regime_gate gets);")
    print("   full = closed + the whole bar of the logging minute; part = closed +")
    print("   that bar shrunk to the seconds elapsed (sqrt-time range).\n")
    keys = {n: [r[0] for r in rows] for n, rows in store.items()}
    rows_out = []
    for x in R:
        rows = store.get(x["name"])
        if not rows:
            continue
        iso_m = f"{x['day']}T{x['t'][:5]}:00+05:30"
        k = bisect.bisect_left(keys[x["name"]], iso_m)
        closed = rows[max(0, k - 249):k]
        cur = rows[k] if k < len(rows) and rows[k][0] == iso_m else None
        rc = ratio(closed)
        rf = ratio((closed + [cur])[-250:]) if cur else float("nan")
        frac = int(x["t"][6:8]) / 60.0
        rp = ratio((closed + [partial(cur, closed[-1][4], frac)])[-250:]) if cur else float("nan")
        x.update(closed=rc, full=rf, part=rp, frac=frac)
        rows_out.append(x)
    capped = [x for x in rows_out if x["logged"] in (2.0, 0.5)]
    body = [x for x in rows_out if x["logged"] not in (2.0, 0.5) and not math.isnan(x["part"])]
    print(f"   readings parsed {len(R)}, reconstructable {len(rows_out)}, "
          f"capped at 2.00/0.50 {len(capped)} (left out of the stats below)\n")
    for lab, sel in (("all days", body),
                     ("29 Sep", [x for x in body if x["day"] == "2026-09-29"]),
                     ("5 Oct", [x for x in body if x["day"] == "2026-10-05"])):
        print(f" {lab}")
        for v in ("closed", "full", "part"):
            print(stats(f"logged - {v}", [x["logged"] - x[v] for x in sel]))
    print("\n by index (logged - part)")
    for n in sorted({x["name"] for x in body}):
        print(stats(n, [x["logged"] - x["part"] for x in body if x["name"] == n]))
    # How big is the forming-bar effect itself (what the log shows vs what the gate sees)?
    print("\n forming-bar effect on the final bars (part - closed): the log is not the gate's number")
    print(stats("part - closed", [x["part"] - x["closed"] for x in body]))
    print(stats("full - closed", [x["full"] - x["closed"] for x in body]))
    clean = [x for x in body if not x["feed_dead"] and not x["hole"]]
    print(f"\n clean readings (no dead feed in the last 20 min, no restart hole): {len(clean)}")
    for v in ("closed", "part"):
        print(stats(f"logged - {v}", [x["logged"] - x[v] for x in clean]))
    for lab, key in (("feed dead in last 20 min", "feed_dead"), ("restart hole", "hole")):
        sel = [x for x in body if x[key]]
        print(stats(f"{lab}: logged - part", [x["logged"] - x["part"] for x in sel]))
    print("\n clean readings by session (logged - part)")
    for f in sorted({x["log"] for x in clean}):
        print(stats(f, [x["logged"] - x["part"] for x in clean if x["log"] == f]))
    # The gate's own number: the logged value with the forming bar taken back out.
    for x in body:
        x["gate_est"] = x["logged"] - (x["part"] - x["closed"])
    gf = [x for x in body if (x["gate_est"] >= need(x["name"], x["t"][:5]))
          != (x["closed"] >= need(x["name"], x["t"][:5]))]
    print(f"\n gate estimate (logged - forming-bar effect) vs backtest-equivalent closed pace,"
          f" opposite sides of the threshold: {len(gf)} of {len(body)}"
          f" (clean only: {sum(1 for x in gf if not x['feed_dead'] and not x['hole'])})")
    for x in gf:
        tag = "feed_dead" if x["feed_dead"] else "hole" if x["hole"] else ""
        print(f"   {x['day']} {x['t']} {x['name']:10s} gate~{x['gate_est']:.2f} backtest "
              f"{x['closed']:.2f} need {need(x['name'], x['t'][:5]):.2f} {tag}")
    # Threshold side disagreements: logged vs closed.
    flips = [x for x in body if (x["logged"] >= need(x["name"], x["t"][:5]))
             != (x["closed"] >= need(x["name"], x["t"][:5]))]
    print(f"\n readings where the logged value and the closed-bar value sit on opposite"
          f" sides of the index's threshold: {len(flips)} of {len(body)}")
    print("\n live-built bars: residual (logged - part) by minutes since the residual last"
          " reset\n (a drop of > 0.04 = a history refresh replaced the live-built bars; clean"
          " readings)")
    for f in sorted({x["log"] for x in clean}):
        acc = {}
        for n in sorted({x["name"] for x in clean if x["log"] == f}):
            seq = [x for x in body if x["log"] == f and x["name"] == n]
            last_reset, prev = None, None
            for x in seq:
                r = x["logged"] - x["part"]
                t = _sec(x["t"])
                if prev is not None and prev - r > 0.04:
                    last_reset = t
                prev = r
                if last_reset is None or x["feed_dead"] or x["hole"]:
                    continue
                b = min(3, int((t - last_reset) // 240))
                acc.setdefault(b, []).append(r)
        if sum(len(v) for v in acc.values()) >= 20:
            print(f"  {f:34s} " + "  ".join(
                f"{['0-3', '4-7', '8-11', '12+'][b]} min: {stx.mean(acc[b]):+.3f} (n={len(acc[b])})"
                for b in sorted(acc)))
    # Worst residuals, with context (restart / feed outage windows).
    print("\n largest |logged - part| (beyond what the forming-bar guess can explain):")
    for x in sorted(body, key=lambda x: -abs(x["logged"] - x["part"]))[:25]:
        print(f"   {x['day']} {x['t']} {x['name']:10s} logged {x['logged']:.2f} closed "
              f"{x['closed']:.2f} part {x['part']:.2f} full {x['full']:.2f}  {x['log']}")
    print("\n capped readings: logged vs final-bar closed ratio")
    agree = sum(1 for x in capped if abs(x["closed"] - x["logged"]) < 0.01 or
                (not math.isnan(x["part"]) and abs(x["part"] - x["logged"]) < 0.01))
    print(f"   {len(capped)} readings at the cap, {agree} also at the cap on the final bars")
    for x in capped[:12]:
        print(f"   {x['day']} {x['t']} {x['name']:10s} logged {x['logged']:.2f} closed "
              f"{x['closed']:.2f} part {x['part']:.2f}")
    # Residual by minute-of-day buckets and by session file.
    print("\n residual (logged - part) by log file")
    for f in sorted({x["log"] for x in body}):
        print(stats(f, [x["logged"] - x["part"] for x in body if x["log"] == f]))
    json.dump(rows_out, open(os.path.join(OUT, "section_a.json"), "w"))
    return body


# ---------------------------------------------------------------- B
def section_b(store):
    print("\n\nB. Live store (.cache/candles) vs backtest history (.cache/history), 28-30 Sep")
    for n in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"):
        H = load_history(n)
        S = [r for r in store.get(n, []) if "09:15" <= r[0][11:16] <= "15:29"]
        if not H or not S:
            continue
        hm = {r[0]: r for r in H}
        sm = {r[0]: r for r in S}
        days = ("2026-09-28", "2026-09-29", "2026-09-30")
        both = [k for k in sm if k in hm and k[:10] in days]
        only_s = sorted(k for k in sm if k not in hm and k[:10] in days)
        only_h = sorted(k for k in hm if k not in sm and k[:10] in days)
        d = {i: [sm[k][i] - hm[k][i] for k in both] for i in (1, 2, 3, 4)}
        exact = sum(all(abs(sm[k][i] - hm[k][i]) < 1e-6 for i in (1, 2, 3, 4)) for k in both)
        print(f"  {n:10s} common bars {len(both)}, identical OHLC {exact}, "
              f"max |dO,dH,dL,dC| = " + ", ".join(f"{max(abs(v) for v in d[i]):.2f}" for i in d)
              + f"; only in store {len(only_s)}, only in history {len(only_h)}"
              + (f" ({only_h[0][5:16]}..{only_h[-1][5:16]})" if only_h else ""))
        # Pace per minute 09:30-15:14 on 29-30 Sep, backtest-style on both series.
        hk = [r[0] for r in H]
        sk = [r[0] for r in S]
        diffs = []
        for k in both:
            if k[:10] == "2026-09-28" or not ("09:30" <= k[11:16] <= "15:14"):
                continue
            i, j = bisect.bisect_left(hk, k), bisect.bisect_left(sk, k)
            if j < B + 1:
                continue
            diffs.append(ratio(S[max(0, j - 399):j + 1]) - ratio(H[max(0, i - 399):i + 1]))
        print(stats("    pace store - history", diffs))
    print("  (history lost 15:15-15:28 on every day since Aug 2026, both series; the store's")
    print("   30 Sep copy was fetched before the close and lacks 15:20-15:28 that history has)")


# ---------------------------------------------------------------- C
def section_c(final):
    snaps = sorted(glob.glob(os.path.join(OUT, "snaps", "*")))
    print(f"\n\nC. 5 Oct snapshots of the engine's store ({len(snaps)} taken every ~10 s)")
    if not snaps:
        print("   none")
        return
    fk = {n: {r[0]: r for r in rows} for n, rows in final.items()}
    fl = {n: [r[0] for r in rows] for n, rows in final.items()}
    gate, bars, seen = {}, {}, set()
    for d in snaps:
        t = os.path.basename(d)
        hhmm = f"{t[:2]}:{t[2:4]}"
        if not ("09:30" <= hhmm <= "15:14"):
            continue
        for f in glob.glob(os.path.join(d, "*_1m.json")):
            n = os.path.basename(f)[:-len("_1m.json")]
            try:
                rows = json.load(open(f))
            except ValueError:
                continue
            iso_m = f"2026-10-05T{hhmm}:00+05:30"
            closed = [r for r in rows if r[0] < iso_m]
            if not closed:
                continue
            # The gate's input at the last close vs the final bars up to the same minute.
            last = closed[-1][0]
            k = bisect.bisect_right(fl[n], last)
            fin = final[n][max(0, k - 249):k]
            key = (n, last)
            # Only where the final window is all history bars (a live-built bar in
            # the final tail - no refresh after it - would be compared with itself).
            if any(r[5] > 0 for r in fin[-21:]):
                continue
            if key not in gate:
                gaps = 0
                for a, b in zip(closed[-21:], closed[-20:]):
                    ta = datetime.fromisoformat(a[0]); tb = datetime.fromisoformat(b[0])
                    if ta.date() == tb.date() and (tb - ta).seconds > 60:
                        gaps += 1
                live = sum(1 for r in closed[-20:] if r[5] > 0)
                gate[key] = (ratio(closed[-249:]), ratio(fin), live, gaps)
            for r in closed[-30:]:
                if r[5] > 0 and (n, r[0]) not in seen and r[0] in fk[n] and fk[n][r[0]][5] == 0:
                    seen.add((n, r[0]))
                    h = fk[n][r[0]]
                    prev = final[n][bisect.bisect_left(fl[n], r[0]) - 1]
                    bars.setdefault(n, []).append(
                        (r[0], [r[i] - h[i] for i in (1, 2, 3, 4)],
                         true_range(prev[4], r[2], r[3]) - true_range(prev[4], h[2], h[3])))
    print("  gate input (closed bars the engine held) minus final history bars, per closed minute:")
    for n in sorted({k[0] for k in gate}):
        v = [(a - b, lv, g) for (m, _), (a, b, lv, g) in gate.items() if m == n]
        print(stats(n, [x[0] for x in v]) +
              f" | minutes with live-built bars in the ATR window {sum(1 for x in v if x[1])}"
              f", with gaps {sum(1 for x in v if x[2])}")
    print("  live-built bar (tick sampler) minus the history bar that replaced it:")
    for n, xs in sorted(bars.items()):
        print(f"  {n:10s} bars {len(xs)}: median |dO,dH,dL,dC| = " +
              ", ".join(f"{stx.median(abs(x[1][i]) for x in xs):.2f}" for i in range(4)) +
              f"; TR live-history median {stx.median(x[2] for x in xs):+.2f}, "
              f"mean {stx.mean(x[2] for x in xs):+.2f}, mean |.| "
              f"{stx.mean(abs(x[2]) for x in xs):.2f}")


def main():
    os.makedirs(OUT, exist_ok=True)
    fdir = pin_final()
    final = load_store(fdir)
    print(f"pace = arb.regime.atr_ratio, atr_period {P}, atr_baseline_bars {B}, "
          f"atr_by_index {HALVES}, split {SPLIT}")
    print(f"final store: {os.path.relpath(fdir, ROOT)}\n")
    R = readings()
    section_a(final, R)
    section_b(final)
    section_c(final)


if __name__ == "__main__":
    main()
