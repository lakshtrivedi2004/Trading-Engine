#!/usr/bin/env python3
"""Correctness suite for the premium-per-point and cross-side reads (arb/optionflow.py).

Arithmetic on constructed series only: no network, no market.
"""
import os
import sys
import tempfile
from collections import deque
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from arb.candles import CandleStore
from arb.config import IntradayLimits
from arb.confluence import Verdict, regrade
from arb.optionflow import effective_delta, mirror_index, read, realised_beta
from arb.smc import BEAR, BULL

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


L = IntradayLimits()
rng = np.random.default_rng(7)

# ─────────────────────────────────────────────────────────────────────────────
section("1. What a premium really moves per index point")

idx = 24000.0 + np.cumsum(rng.normal(0, 8, 40))
prem = 150.0 + 0.4 * (idx - idx[0]) + rng.normal(0, 0.3, 40)
b, r2, n = realised_beta(prem, idx)
check("regression recovers the premium's own points-per-point", abs(b - 0.4) < 0.03,
      f"beta {b:.3f}, R2 {r2:.2f}, {n} minutes")
check("too little history is not a measurement", realised_beta([1, 2], [1, 2])[2] == 0)
check("a flat index gives no slope rather than a division by zero",
      realised_beta([1, 2, 3, 4, 5], [7, 7, 7, 7, 7])[0] == 0.0)

eff, d = effective_delta(0.5, "CE", list(prem), list(idx), L)
check("a call measured at 0.4 against a 0.5 chain delta is sized at ~0.4, not 0.5",
      0.38 <= eff <= 0.43, f"effective {eff:.3f}, weight {d.get('weight')}")
eff0, d0 = effective_delta(0.5, "CE", [], [], L)
check("with no premium history a call falls back to chain x prior",
      abs(eff0 - 0.5 * L.realised_delta_prior_ce) < 1e-9, f"{eff0:.3f}")
effp, _ = effective_delta(0.5, "PE", [], [], L)
check("...and a put to chain x its own prior", abs(effp - 0.5 * L.realised_delta_prior_pe) < 1e-9)
rich = 150.0 + 0.7 * (idx - idx[0])
effr, _ = effective_delta(0.5, "CE", list(rich), list(idx), L)
check("never promised MORE than the chain delta, even when it measured more",
      effr <= 0.5 + 1e-9, f"{effr:.3f}")
dead = 150.0 + 0.02 * (idx - idx[0])
effd, _ = effective_delta(0.5, "CE", list(dead), list(idx), L)
check("never below the floor", effd >= 0.5 * L.realised_delta_floor - 1e-9, f"{effd:.3f}")
noisy = 150.0 + rng.normal(0, 20, 40)
effn, dn = effective_delta(0.5, "CE", list(noisy), list(idx), L)
check("a premium that does not track the index (R2 ~ 0) is not trusted - the prior wins",
      abs(effn - 0.5 * L.realised_delta_prior_ce) < 0.06, f"{effn:.3f}, R2 {dn.get('r2')}")

# ─────────────────────────────────────────────────────────────────────────────
section("2. The matched-delta strike on the other side")

cv = SimpleNamespace(
    strikes=np.array([23800, 23900, 24000, 24100, 24200, 24300, 24400], float),
    ce_ask=np.array([340, 255, 180, 120, 75, 44, 25], float),
    pe_ask=np.array([28, 45, 78, 120, 175, 245, 330], float))
j = mirror_index(cv, 3, "CE")
check("the ATM call's mirror is the ATM put", j == 3, f"index {j}")
j2 = mirror_index(cv, 4, "CE")
check("an OTM call's mirror is an OTM put, on the other side of the money",
      int(cv.strikes[j2]) < 24100, f"24200 CE -> {cv.strikes[j2]:.0f} PE")

# ─────────────────────────────────────────────────────────────────────────────
section("3. Does the other side believe the move?")

W = L.cross_side_bars


def scene(call_win, put_win, idx_win, hist=21, bc=0.5, bp=-0.5):
    """Normal behaviour for `hist` minutes, then a W-minute window where the
    index, the call and the put each move by the given totals (linearly)."""
    s = 24000.0 + np.cumsum(rng.normal(0, 6, hist))
    c = 150.0 + bc * (s - s[0]) + rng.normal(0, 0.2, hist)
    p = 150.0 + bp * (s - s[0]) + rng.normal(0, 0.2, hist)
    f = np.linspace(0, 1, W + 1)[1:]
    return (list(c) + list(c[-1] + call_win * f), list(p) + list(p[-1] + put_win * f),
            list(s) + list(s[-1] + idx_win * f))


def judge(side, c, p, s, nb=None, atr=4.0):
    bought, mirror = (c, p) if side == BULL else (p, c)
    return read(side, bought, mirror, s, nb, (0.5, 0.5), L, atr=atr)


x = judge(BULL, *scene(+10, -10, +20))
check("index +20, call +10, put -10: confirmed", x.verdict == "confirmed" and x.confirms,
      x.note)
x = judge(BULL, *scene(+2, -2, +20))
check("index +20 but call +2 / put -2: a move the options did not price is a fake_move",
      x.verdict == "fake_move" and x.against and not x.confirms, x.note)
x = judge(BULL, *scene(+6, -6, 0))
check("index flat, call +6 / put -6: options_lead", x.verdict == "options_lead" and x.confirms,
      x.note)
x = judge(BULL, *scene(-2, +2, -20))
check("a pullback the options did not price is a fake_pullback, and it confirms the long",
      x.verdict == "fake_pullback" and x.confirms, x.note)
x = judge(BULL, *scene(-10, +10, -20))
check("a pullback priced in full neither confirms nor refuses",
      x.verdict == "pullback_priced" and not x.confirms and not x.against, x.note)
x = judge(BULL, *scene(+12, -30, +20))
check("the put collapsing faster than the call rises is writers crushing vol - no confirm",
      x.detail.get("vol_crush") and not x.confirms, x.note)
x = judge(BEAR, *scene(-10, +10, -20))
check("the same read works for a put bought on a fall", x.verdict == "confirmed" and x.confirms,
      x.note)
x = judge(BEAR, *scene(-2, +2, -20))
check("...and flags a fall the options did not price", x.verdict == "fake_move", x.note)
c, p, s = scene(+10, -10, +20)
deep = list(np.array(c) + 50)
deep[-1] = deep[-W - 1] + 4.0        # the deeper strike moved LESS than ours
far = list(np.array(c) - 50)
far[-1] = far[-W - 1] + 3.0
x = judge(BULL, c, p, s, nb=(deep, far))
check("our strike moving far more than both neighbours is out of line - no confirm",
      not x.ladder_ok and not x.confirms, x.note)
deep[-1] = deep[-W - 1] + 13.0
far[-1] = far[-W - 1] + 7.0
x = judge(BULL, c, p, s, nb=(deep, far))
check("a ladder ordered by delta passes", x.ladder_ok and x.confirms, str(x.detail.get("ladder")))
x = read(BULL, [1.0] * 5, [1.0] * 5, [1.0] * 5, None, (0.5, 0.5), L)
check("five minutes of history is no read at all", x.verdict == "no_data" and not x.confirms)

# ─────────────────────────────────────────────────────────────────────────────
section("4. Plumbing")

v = Verdict(confirmations=[("play:ob_fvg_retest", "")], setups=[object()])
before = regrade(v, L)
v.confirmations.append(("cross_side", "confirmed"))
after = regrade(v, L)
check("a confirming cross-side read is an extra and can lift the grade",
      before == "C" and after == "B", f"{before} -> {after}")

with tempfile.TemporaryDirectory() as tmp:
    cs = CandleStore(None, [], cache_dir=tmp)
    snaps = [np.array([24000.0 + i, 100.0 + i, 50.0 - i]) for i in range(6)]
    snaps[3][2] = 0.0                  # one minute the put did not quote
    cs._premium = deque([(f"t{i}", m) for i, m in enumerate(snaps)])
    cs._store = SimpleNamespace(bid=np.array([0.0, 110.0, 40.0]),
                                ask=np.array([0.0, 112.0, 42.0]),
                                ltp=np.array([24010.0, 0.0, 0.0]))
    got = cs.premium_series([0, 1, 2])
    check("series stay aligned: a minute missing on one slot is dropped for all",
          len(got[0]) == len(got[1]) == len(got[2]) == 5 and 24003.0 not in got[0],
          f"{len(got[0])} rows")
    check("the live row carries the index LTP and the option mids",
          got[0][-1] == 24010.0 and got[1][-1] == 111.0 and got[2][-1] == 41.0)

print("\n" + "=" * 64)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 64)
sys.exit(1 if FAIL else 0)
