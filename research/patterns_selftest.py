#!/usr/bin/env python3
"""Unit tests for research/patterns.py on small synthetic bar series.

    venv/bin/python research/patterns_selftest.py

Each case draws a price path through waypoints (one bar per step: open = the
previous close, high/low 0.3 beyond the body) and checks what the detectors do
with it: a clean W fires once on the neckline break (and on the retest when one
is drawn), a fake W (second low undercuts, or the neckline never breaks) does
not, the M is exactly the W reflected, and so on. The last block is the
look-ahead test: on a random walk and on real NIFTY minutes every signal found
on the full series is found identically on the series cut just after it, and a
signal on a cut series never disappears when more bars arrive.
"""
import os
import sys
import json
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))
import patterns as PT  # noqa: E402

FAILS = []


def check(name, cond, info=""):
    print(f"  {'ok ' if cond else 'FAIL'} {name}" + (f"   [{info}]" if info and not cond else ""))
    if not cond:
        FAILS.append(name)


def path(points, day="2026-01-01", wick=0.3):
    """Waypoints [(bars, price), ...]: from the previous price to `price` in
    `bars` equal steps. First tuple = (0, start)."""
    cs = [points[0][1]]
    for n, px in points[1:]:
        a = cs[-1]
        cs += list(np.linspace(a, px, n + 1)[1:])
    o = [cs[0]] + cs[:-1]
    h = [max(a, b) + wick for a, b in zip(o, cs)]
    l = [min(a, b) - wick for a, b in zip(o, cs)]
    return PT.Bars(o, h, l, cs, [day] * len(cs))


def sigs(B, fam, **kw):
    return PT.detect(B, fam, **kw)


def only(ss, pattern=None, mode=None, side=None, kind=None):
    return [s for s in ss if (pattern is None or s["pattern"] == pattern) and (mode is None or s["mode"] == mode)
            and (side is None or s["side"] == side) and (kind is None or s["kind"] == kind)]


def bar_of(points, upto):
    return sum(n for n, _ in points[1:upto + 1])


# ---------------------------------------------------------------------------
print("double bottom / top")
W = [(0, 120), (15, 100), (8, 108), (8, 100.1), (12, 114)]
B = path(W)
S = sigs(B, "double")
br = only(S, "double_bottom", "break")
check("clean W fires one break", len(br) == 1, S)
if br:
    s = br[0]
    neck = B.h[bar_of(W, 1):bar_of(W, 3) + 1].max()
    check("break bar is the first close above the neckline",
          B.c[s["t"]] > neck and all(B.c[q] <= neck for q in range(bar_of(W, 3), s["t"])), (s, neck))
    check("stop under the lower low, target = neck + height",
          s["stop"] < min(B.l) and abs(s["target"] - (neck + (neck - min(B.l[:s['t']])))) < 1e-6, s)
    check("side is +1 (buy calls)", s["side"] == 1)
check("no double top in a W", not only(S, "double_top"))

Wr = [(0, 120), (15, 100), (8, 108), (8, 100.1), (6, 111), (3, 108.6), (6, 115)]
S = sigs(path(Wr), "double")
check("W with a pull-back to the neckline fires break then retest",
      [s["mode"] for s in only(S, "double_bottom")] == ["break", "retest"], S)
rt = only(S, "double_bottom", "retest")
if rt:
    check("retest after the break", rt[0]["t"] > only(S, "double_bottom", "break")[0]["t"])

S = sigs(path([(0, 120), (15, 100), (8, 108), (8, 97), (12, 114)]), "double")
check("fake W (second low undercuts by 3 > 0.3 ATR) does not fire", not only(S, "double_bottom"), S)
S = sigs(path([(0, 120), (15, 100), (8, 108), (8, 100.1), (6, 106), (6, 103), (6, 105.5)]), "double")
check("W whose neckline never breaks does not fire", not only(S, "double_bottom"), S)
S = sigs(path([(0, 120), (15, 100), (8, 108), (8, 100.1), (12, 114)]), "double", tol=0.01)
check("tolerance is honoured (0.01 ATR rejects a 0.1-pt gap at ATR ~1)", not only(S, "double_bottom"), S)

Bm = path([(n, 240 - p) for n, p in W])         # W reflected around 120
S = sigs(Bm, "double")
top = only(S, "double_top", "break")
check("the reflected W is an M that fires one short", len(top) == 1 and top[0]["side"] == -1, S)
if top and br:
    check("M signal mirrors the W signal (same bar, stop/target reflected)",
          top[0]["t"] == br[0]["t"] and abs((240 - top[0]["stop"]) - br[0]["stop"]) < 1e-6
          and abs((240 - top[0]["target"]) - br[0]["target"]) < 1e-6, (top, br))

# ---------------------------------------------------------------------------
print("triple bottom")
TB = [(0, 120), (12, 100), (6, 106), (6, 100.2), (6, 106.5), (6, 99.9), (10, 112)]
S = sigs(path(TB), "triple")
check("clean triple bottom fires one break", len(only(S, "triple_bottom", "break")) == 1, S)
S = sigs(path([(0, 120), (12, 100), (6, 106), (6, 100.2), (6, 106.5), (6, 96), (10, 112)]), "triple")
check("third low far below -> no triple bottom", not only(S, "triple_bottom"), S)

# ---------------------------------------------------------------------------
print("inverse head and shoulders")
IHS = [(0, 120), (10, 104), (6, 110), (8, 99), (8, 110.3), (6, 104.4), (10, 118)]
S = sigs(path(IHS), "ihs")
x = only(S, "inverse_hs", "break")
check("clean inverse H&S fires one break", len(x) == 1, S)
if x:
    check("stop under the right shoulder (stop_mode last)", 104.4 - 0.3 - 2 < x[0]["stop"] < 104.4 - 0.3, x)
S = sigs(path([(0, 120), (10, 104), (6, 110), (8, 103.8), (8, 110.3), (6, 104.4), (10, 118)]), "ihs")
check("no head (middle low not lower) -> no inverse H&S", not only(S, "inverse_hs"), S)
S = sigs(path([(n, 240 - p) for n, p in IHS]), "ihs")
check("reflected -> head and shoulders short", len(only(S, "head_shoulders", "break", -1)) == 1, S)

# ---------------------------------------------------------------------------
print("range box")
BOX = [(0, 103)] + [(4, 106), (4, 100)] * 6 + [(3, 109), (6, 112)]
S = sigs(path(BOX), "box", W=30, k=2)
check("box breakout up fires", len(only(S, "box_up", "break")) >= 1, S)
check("box edge rejection fires both ways while the box holds",
      len(only(S, "box_up", "reject")) >= 1 and len(only(S, "box_down", "reject")) >= 1, S)
b = only(S, "box_up", "break")
if b:
    check("breakout target = top + height", abs(b[0]["target"] - (b[0]["top"] + (b[0]["top"] - b[0]["bot"]))) < 1e-6)
S = sigs(path([(0, 100), (40, 120), (5, 125)]), "box", W=30, k=2)
check("a straight trend is not a box", not S, S)

# ---------------------------------------------------------------------------
print("channel")
pts = [(0, 100)]
lvl = 100.0
for leg in range(5):                   # rising channel: +6 up, -4 down, 5 bars a leg
    lvl += 6; pts.append((5, lvl)); lvl -= 4; pts.append((5, lvl))
pts += [(3, lvl - 0.1), (6, lvl + 5)]
S = sigs(path(pts), "channel")
check("ascending channel: bounce off the lower line (buy)", len(only(S, "channel_up", "bounce")) >= 1, S)
check("no short bounce inside an ascending channel", not only(S, "channel_down", "bounce"), S)
dn = path(pts[:-2] + [(6, lvl - 9)])
S = sigs(dn, "channel")
x = only(S, "channel_down", "break")
check("close below the ascending channel = short break 'against'", len(x) == 1 and x[0]["kind"] == "against", S)

# ---------------------------------------------------------------------------
print("triangle")
asc = [(0, 100), (5, 110), (5, 102), (5, 110), (5, 104.5), (5, 110), (5, 107), (5, 114)]
S = sigs(path(asc), "triangle")
x = only(S, "triangle_up", "break")
check("ascending triangle breaks up -> one buy, kind classic", len(x) == 1 and x[0]["kind"] == "classic", S)
S = sigs(path([(n, 220 - p) for n, p in asc]), "triangle")
x = only(S, "triangle_down", "break")
check("descending triangle breaks down -> one sell, kind classic, geom desc",
      len(x) == 1 and x[0]["kind"] == "classic" and x[0]["geom"] == "desc", S)
sym = [(0, 100), (5, 112), (5, 100), (5, 110), (5, 102), (5, 108), (5, 104), (5, 113)]
S = sigs(path(sym), "triangle")
check("symmetrical triangle breaks up -> sym buy", len(only(S, "triangle_up", "break", kind="sym")) == 1, S)

# ---------------------------------------------------------------------------
print("flag / pennant")
FL = [(0, 100), (5, 100.5), (6, 115), (2, 113), (2, 114), (2, 112.5), (3, 118)]
B = path(FL)
S = sigs(B, "flag", pole_min=3.0)
x = only(S, "bull_flag")
check("bull flag: one buy on the close above the pole top", len(x) == 1 and B.c[x[0]["t"]] > 115.3, S)
S = sigs(path([(0, 100), (5, 100.5), (6, 115), (3, 106), (3, 118)]), "flag", pole_min=3.0)
check("pullback giving back > 50% of the pole is not a flag", not only(S, "bull_flag"), S)
S = sigs(path([(0, 100), (5, 100.5), (6, 115), (2, 112), (2, 114.5), (2, 113), (2, 114), (3, 118)]),
         "flag", pole_min=3.0, shape="pennant", cmin=4)
check("pennant (converging consolidation) fires", len(only(S, "bull_flag", kind="pennant")) == 1, S)
S = sigs(path([(0, 100), (5, 100.5), (6, 115), (2, 113.5), (2, 114), (2, 111.5), (2, 114.8), (3, 118)]),
         "flag", pole_min=3.0, shape="pennant", cmin=4)
check("widening consolidation is not a pennant", not only(S, "bull_flag", kind="pennant"), S)

# ---------------------------------------------------------------------------
print("session boundary")
a = path(W[:3], day="2026-01-01"); b = path([(0, 100.2)] + W[3:], day="2026-01-02")
B = PT.Bars(np.r_[a.o, b.o], np.r_[a.h, b.h], np.r_[a.l, b.l], np.r_[a.c, b.c], a.day + b.day)
check("a W split across two sessions does not fire", not only(sigs(B, "double"), "double_bottom"))

# ---------------------------------------------------------------------------
print("look-ahead (prefix invariance)")
FAM = dict(double={}, triple={}, ihs={}, box={}, channel={}, triangle={}, flag={},
           pennant=dict(shape="pennant"), flagtrig=dict(trig="flag"))


def prefix_test(B, label, cuts=40, seed=1):
    rng = np.random.default_rng(seed)
    bad = 0; total = 0
    for nm, kw in FAM.items():
        fam = nm if nm in PT.DEFAULTS else "flag"
        full = sigs(B, fam, **kw)
        total += len(full)
        pts = sorted(set([s["t"] + 1 for s in full][:cuts] + list(rng.integers(20, B.n, cuts))))
        for cut in pts:
            Bc = PT.Bars(B.o[:cut], B.h[:cut], B.l[:cut], B.c[:cut], B.day[:cut])
            a = [(s["t"], s["side"], s["mode"], round(s["stop"], 6)) for s in sigs(Bc, fam, **kw)]
            b = [(s["t"], s["side"], s["mode"], round(s["stop"], 6)) for s in full if s["t"] < cut]
            if a != b:
                bad += 1
                print("    mismatch", label, nm, cut, set(a) ^ set(b))
                break
    check(f"{label}: signals identical on every cut ({total} signals in the full series)", bad == 0)


rng = np.random.default_rng(7)
n = 1500
c = 100 + np.cumsum(rng.normal(0, 1, n))
o = np.r_[c[0], c[:-1]]
h = np.maximum(o, c) + rng.uniform(0, 0.8, n); l = np.minimum(o, c) - rng.uniform(0, 0.8, n)
day = [f"d{q // 375:03d}" for q in range(n)]
prefix_test(PT.Bars(o, h, l, c, day), "random walk")
p = os.path.join(ROOT, ".cache", "history", "NIFTY.json")
if os.path.exists(p):
    rows = [r for r in json.load(open(p)) if "09:15" <= r[0][11:16] <= "15:29"]
    keep = sorted({r[0][:10] for r in rows})[-4:]
    rows = [r for r in rows if r[0][:10] in keep]
    prefix_test(PT.from_rows(rows, 1), "NIFTY 1m, 4 sessions")
    B3 = PT.from_rows(rows, 3)
    ok = True
    for t in range(B3.n):
        a = B3.end[t - 1] + 1 if t else 0
        grp = rows[a:B3.end[t] + 1]
        ok &= (len({(r[0][:10], (PT.hm(r[0]) - PT.SESSION_START) // 3) for r in grp}) == 1
               and B3.o[t] == grp[0][1] and B3.h[t] == max(r[2] for r in grp)
               and B3.l[t] == min(r[3] for r in grp) and B3.c[t] == grp[-1][4])
    check("3m resample: each bar = one 09:15-grid bucket; o/h/l/c from its own minutes only", ok)
    prefix_test(B3, "NIFTY 3m, 4 sessions")

print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
