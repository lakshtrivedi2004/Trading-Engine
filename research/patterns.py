"""Classical chart patterns as objective, look-ahead-free detectors on CLOSED bars.

Owner, 7 Oct 2026: "I see W patterns forming on 1m - add patterns alongside the
candlesticks." This module is research only (nothing in arb/ imports it). It
turns each textbook pattern into a rule a machine can check on bars that have
already closed, with every tolerance in ATR of the bar series it runs on:

  double_bottom (W) / double_top (M)
  triple_bottom / triple_top
  inverse_hs / head_shoulders
  range_box            sideways box: breakout, or rejection at an edge (fade)
  channel              ascending / descending parallel channel: bounce off the
                       line with the slope, or a close through either line
  triangle             symmetrical / ascending / descending: close through a line
  flag / pennant       pole, tight consolidation, close beyond it

  rounding top/bottom  NOT implemented: a "saucer" has no edge a rule can fire
                       on (no neckline, no line, no count of touches); every
                       mechanical version is a curve-fit (quadratic R^2 over a
                       window of arbitrary length) whose entry is then just a
                       range breakout, which range_box already tests.

Look-ahead rules (all enforced in code, all unit-tested in
research/patterns_selftest.py):
  * A swing pivot at bar p with k bars each side is only USED from bar p + k on
    (the bar that confirms it). Pivots never straddle a session boundary.
  * Every structure (neckline, lines, box, pole) is built from bars < t; the
    trigger bar t is decided on its own CLOSE; the entry is that close.
  * The ATR used at bar t is the mean true range of bars t-14 .. t-1.
  * Patterns are intraday: nothing carries over to the next session.

Only the bullish form of each detector is written. The bearish form is the
SAME code run on the price series reflected through zero (high <-> -low,
close -> -close), so an M is exactly a W upside down and the two sides can never
drift apart. `detect(B, name, **p)` runs both and returns signals in the
original price frame.

A signal is a dict:
  t       bar index (in the series it was detected on) whose close is the entry
  side    +1 buy calls / -1 buy puts
  entry   that close
  stop    index stop (the pattern's own invalidation, pad included)
  target  measured-move index target, or None
  mode    which trigger fired ("break", "retest", "bounce", "reject", ...)
  kind    sub-type (e.g. "asc"/"desc"/"sym" triangle, "with"/"against" channel)
  key     identity of the structure (one signal per structure per mode)
"""
import numpy as np

SESSION_START = 9 * 60 + 15


def hm(iso):
    return int(iso[11:13]) * 60 + int(iso[14:16])


# ----------------------------------------------------------------------------
# bars
# ----------------------------------------------------------------------------
class Bars:
    """OHLC arrays with session boundaries and a prior-bars ATR.

    end[t]   index of the LAST 1-minute row inside bar t (its close is bar t's
             close, so an entry on bar t is an entry on that 1-minute row)
    days     list of (first, last + 1) bar ranges, one per session
    atr[t]   mean true range of bars t-n .. t-1 (known before bar t closes)
    """

    def __init__(self, o, h, l, c, day, end=None, atr_n=14):
        self.o, self.h, self.l, self.c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
        self.day = list(day)
        self.n = len(self.c)
        self.end = np.arange(self.n) if end is None else np.asarray(end)
        self.days = []
        s = 0
        for t in range(1, self.n + 1):
            if t == self.n or self.day[t] != self.day[t - 1]:
                self.days.append((s, t)); s = t
        pc = np.concatenate([[self.c[0]], self.c[:-1]])
        tr = np.maximum(self.h - self.l, np.maximum(abs(self.h - pc), abs(self.l - pc)))
        cs = np.concatenate([[0.0], np.cumsum(tr)])
        self.atr = np.zeros(self.n)
        for t in range(1, self.n):          # warm-up: the mean of whatever has closed so far
            m = min(t, atr_n)
            self.atr[t] = (cs[t] - cs[t - m]) / m
        if self.n:
            self.atr[0] = max(self.h[0] - self.l[0], 1e-9)
        self.atr[self.atr <= 0] = 1e-9

    def neg(self):
        """The same bars reflected through zero: a top becomes a bottom."""
        B = Bars.__new__(Bars)
        B.o, B.h, B.l, B.c = -self.o, -self.l, -self.h, -self.c
        B.day, B.n, B.end, B.days, B.atr = self.day, self.n, self.end, self.days, self.atr
        return B


def from_rows(rows, tf=1, atr_n=14):
    """1-minute rows [iso, o, h, l, c, ...] -> Bars of tf minutes on the 09:15
    grid of each session (a bucket missing minutes is kept; its close is the
    last minute present, so nothing later is ever used)."""
    o, h, l, c, day, end = [], [], [], [], [], []
    cur = None
    for i, r in enumerate(rows):
        d = r[0][:10]
        b = (hm(r[0]) - SESSION_START) // tf
        if cur is not None and cur[0] == d and cur[1] == b:
            h[-1] = max(h[-1], r[2]); l[-1] = min(l[-1], r[3]); c[-1] = r[4]; end[-1] = i
            continue
        cur = (d, b)
        o.append(r[1]); h.append(r[2]); l.append(r[3]); c.append(r[4]); day.append(d); end.append(i)
    return Bars(o, h, l, c, day, end, atr_n=atr_n)


def pivots(B, k):
    """lo[p] / hi[p]: bar p is a swing low / high with k bars each side, all in
    the same session. Strict on the left, non-strict on the right, so a flat
    double bar gives one pivot (the first). Usable from bar p + k on."""
    lo = np.zeros(B.n, bool); hi = np.zeros(B.n, bool)
    for d0, d1 in B.days:
        for p in range(d0 + k, d1 - k):
            if B.l[p] < B.l[p - k:p].min() and B.l[p] <= B.l[p + 1:p + k + 1].min():
                lo[p] = True
            if B.h[p] > B.h[p - k:p].max() and B.h[p] >= B.h[p + 1:p + k + 1].max():
                hi[p] = True
    return lo, hi


def _sessions(B, k):
    """Yield (t, lows, highs, new_lo, new_hi): confirmed pivot indices of this
    session known at the close of bar t, and whether one was confirmed at t."""
    lo, hi = B._piv if getattr(B, "_piv_k", None) == k else pivots(B, k)
    for d0, d1 in B.days:
        lows, highs = [], []
        for t in range(d0, d1):
            p = t - k
            nl = nh = False
            if p >= d0 and lo[p]:
                lows.append(p); nl = True
            if p >= d0 and hi[p]:
                highs.append(p); nh = True
            yield t, d0, lows, highs, nl, nh


def _sig(t, side, entry, stop, target, mode, kind, key, **info):
    return dict(t=int(t), side=side, entry=float(entry), stop=float(stop),
                target=None if target is None else float(target), mode=mode, kind=kind, key=key, **info)


# ----------------------------------------------------------------------------
# neckline patterns: W, triple bottom, inverse head and shoulders (bullish forms)
# ----------------------------------------------------------------------------
def _neck_runner(B, k, form, wait, brk, rt_tol, rt_wait, pad, stop_mode):
    """Shared trigger for every neckline reversal.

    form(B, lows, t) -> None or a structure dict:
        neck(t) -> neckline price at bar t; low = pattern low; last = last
        pivot (stop 'last' sits under it); height = neckline - low (measured
        move); key.
    A structure is armed at the bar its last pivot is confirmed, provided no
    close has been above the neckline since that pivot (else it already broke
    before anyone could know it was a pattern - stale, skipped).
      break   the FIRST close above the neckline within `wait` bars
      retest  after that break, within rt_wait bars: a bar whose low comes back
              to within rt_tol ATR of the neckline and closes above it, green;
              any close back below the neckline first cancels the retest.
    Any low under the pattern low cancels the structure.
    stop: 'low' = pattern low - pad ATR, 'last' = last pivot low - pad ATR.
    target: neckline at the trigger + height."""
    out, live = [], []
    for t, d0, lows, highs, nl, nh in _sessions(B, k):
        if t == d0:
            live = []
        A = B.atr[t]
        if nl:      # arm first: the bar that confirms the last pivot may itself break out
            s = form(B, lows, t)
            if s is not None:
                stale = any(B.c[q] > s["neck"](q) for q in range(s["last"] + 1, t))
                if not stale and s["height"] > 0:
                    s.update(state="armed", armed=t)
                    live.append(s)
        keep = []
        for s in live:
            if B.l[t] < s["low"]:
                continue
            nk = s["neck"](t)
            if s["state"] == "armed":
                if t - s["armed"] > wait:
                    continue
                if B.c[t] > nk + brk * A:
                    stop = (s["low"] if stop_mode == "low" else B.l[s["last"]]) - pad * A
                    out.append(_sig(t, 1, B.c[t], stop, nk + s["height"], "break", s["kind"], s["key"],
                                    neck=nk, height=s["height"]))
                    s["state"], s["tb"] = "broken", t
                keep.append(s)
            else:                                   # broken: watch for the retest
                if t - s["tb"] > rt_wait or B.c[t] < nk:
                    continue
                if B.l[t] <= nk + rt_tol * A and B.c[t] > B.o[t]:
                    stop = (s["low"] if stop_mode == "low" else B.l[s["last"]]) - pad * A
                    stop = min(stop, B.l[t] - pad * A)
                    out.append(_sig(t, 1, B.c[t], stop, nk + s["height"], "retest", s["kind"], s["key"],
                                    neck=nk, height=s["height"]))
                    continue
                keep.append(s)
        live = keep
    return out


def _line(p1, v1, p2, v2):
    sl = (v2 - v1) / (p2 - p1) if p2 != p1 else 0.0
    return lambda t, v2=v2, p2=p2, sl=sl: v2 + sl * (t - p2)


def form_double(tol, minsep, maxsep, hmin, hmax):
    """W: the last two confirmed pivot lows within tol ATR of each other,
    minsep..maxsep bars apart, nothing lower between them, neckline = the
    highest high between them, height hmin..hmax ATR."""
    def form(B, lows, t):
        if len(lows) < 2:
            return None
        p1, p2 = lows[-2], lows[-1]
        A = B.atr[t]
        if not (minsep <= p2 - p1 <= maxsep) or abs(B.l[p1] - B.l[p2]) > tol * A:
            return None
        low = min(B.l[p1], B.l[p2])
        if B.l[p1 + 1:p2].min(initial=np.inf) < low:
            return None
        N = B.h[p1:p2 + 1].max()
        H = N - low
        if not (hmin * A <= H <= hmax * A):
            return None
        return dict(neck=lambda q, N=N: N, low=low, last=p2, height=H, kind="double", key=("W", p1, p2))
    return form


def form_triple(tol, minsep, maxsep, hmin, hmax):
    """Triple bottom: the last three pivot lows all within tol ATR (max - min),
    each gap minsep..maxsep bars, nothing lower in between, neckline = highest
    high between the first and the third."""
    def form(B, lows, t):
        if len(lows) < 3:
            return None
        p = lows[-3:]
        A = B.atr[t]
        v = [B.l[q] for q in p]
        if max(v) - min(v) > tol * A:
            return None
        if not all(minsep <= b - a <= maxsep for a, b in zip(p, p[1:])):
            return None
        low = min(v)
        if B.l[p[0] + 1:p[2]].min(initial=np.inf) < low:
            return None
        N = B.h[p[0]:p[2] + 1].max()
        H = N - low
        if not (hmin * A <= H <= hmax * A):
            return None
        return dict(neck=lambda q, N=N: N, low=low, last=p[2], height=H, kind="triple", key=("TB", *p))
    return form


def form_ihs(sh_tol, head_min, minsep, maxsep, hmin, hmax, neck_tol):
    """Inverse head and shoulders: last three pivot lows LS, HEAD, RS; the head
    at least head_min ATR below both shoulders, the shoulders within sh_tol ATR
    of each other, each gap minsep..maxsep bars; neckline through the highest
    high between LS-HEAD and HEAD-RS, its two points within neck_tol ATR of
    each other (a near-flat neckline); height = neckline above the head at the
    head's bar."""
    def form(B, lows, t):
        if len(lows) < 3:
            return None
        a, hd, b = lows[-3:]
        A = B.atr[t]
        la, lh, lb = B.l[a], B.l[hd], B.l[b]
        if not all(minsep <= y - x <= maxsep for x, y in ((a, hd), (hd, b))):
            return None
        if lh > min(la, lb) - head_min * A or abs(la - lb) > sh_tol * A:
            return None
        q1 = a + int(np.argmax(B.h[a:hd + 1])); q2 = hd + int(np.argmax(B.h[hd:b + 1]))
        n1, n2 = B.h[q1], B.h[q2]
        if abs(n1 - n2) > neck_tol * A or q1 == q2:
            return None
        neck = _line(q1, n1, q2, n2)
        H = neck(hd) - lh
        if not (hmin * A <= H <= hmax * A):
            return None
        return dict(neck=neck, low=lh, last=b, height=H, kind="ihs", key=("IHS", a, hd, b))
    return form


# ----------------------------------------------------------------------------
# range box
# ----------------------------------------------------------------------------
def range_box(B, k, W, touch_tol, hmin, hmax, brk, pad, poke, modes=("break", "reject")):
    """Sideways box over the last W bars (t-W .. t-1, same session): top = their
    highest high, bottom = lowest low, height hmin..hmax ATR, and at least two
    confirmed pivot highs within touch_tol ATR of the top AND two pivot lows
    within touch_tol ATR of the bottom (a box is a range that was tested on
    both sides, not any quiet stretch).
      break   first close above top + brk ATR -> buy; stop at the box middle
              - pad ATR; target top + height
      reject  (fade the bottom edge) the bar's low comes within touch_tol ATR of
              the bottom, pokes at most `poke` ATR under it, closes back inside
              and green -> buy; stop under the bar / bottom - pad ATR; target the
              box middle (measured) - the short at the top is the mirror
    One signal per mode per box (box = its top and bottom bars)."""
    lo, hi = B._piv if getattr(B, "_piv_k", None) == k else pivots(B, k)
    out, used = [], set()
    for d0, d1 in B.days:
        for t in range(d0 + W, d1):
            a, b = t - W, t
            A = B.atr[t]
            top = B.h[a:b].max(); bot = B.l[a:b].min(); H = top - bot
            if not (hmin * A <= H <= hmax * A):
                continue
            # pivots confirmed by t-1 that sit inside the window
            ph = [p for p in range(a, b - k) if hi[p] and B.h[p] >= top - touch_tol * A]
            pl = [p for p in range(a, b - k) if lo[p] and B.l[p] <= bot + touch_tol * A]
            if len(ph) < 2 or len(pl) < 2:
                continue
            key = (int(a + np.argmax(B.h[a:b])), int(a + np.argmin(B.l[a:b])))
            mid = 0.5 * (top + bot)
            if "break" in modes and B.c[t] > top + brk * A and B.c[t - 1] <= top and ("break", key) not in used:
                used.add(("break", key))
                out.append(_sig(t, 1, B.c[t], mid - pad * A, top + H, "break", "box", ("BOX",) + key,
                                top=top, bot=bot))
            if ("reject" in modes and B.l[t] <= bot + touch_tol * A and B.l[t] >= bot - poke * A
                    and B.c[t] > bot and B.c[t] > B.o[t] and B.c[t] < mid and ("reject", key) not in used):
                used.add(("reject", key))
                out.append(_sig(t, 1, B.c[t], min(B.l[t], bot) - pad * A, mid, "reject", "box", ("BOX",) + key,
                                top=top, bot=bot))
    return out


# ----------------------------------------------------------------------------
# two-line structures: channels and triangles
# ----------------------------------------------------------------------------
def _two_lines(B, highs, lows, t, maxspan):
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2:]; l1, l2 = lows[-2:]
    first = min(h1, l1)
    if t - first > maxspan:
        return None
    up = _line(h1, B.h[h1], h2, B.h[h2]); dn = _line(l1, B.l[l1], l2, B.l[l2])
    su = (B.h[h2] - B.h[h1]) / (h2 - h1); sl = (B.l[l2] - B.l[l1]) / (l2 - l1)
    return dict(up=up, dn=dn, su=su, sl=sl, first=first, piv=(h1, h2, l1, l2))


def _contained(B, S, a, b, tol):
    """Every bar a .. b-1 inside the two lines (tol ATR of slack)."""
    for q in range(a, b):
        A = B.atr[q]
        if B.h[q] > S["up"](q) + tol * A or B.l[q] < S["dn"](q) - tol * A:
            return False
    return True


def channel(B, k, tol, maxspan, par_tol, smin, wmin, wmax, brk, pad, wait, modes=("bounce", "break")):
    """Parallel channel from the last two confirmed pivot highs (upper line)
    and the last two pivot lows (lower line), all within maxspan bars:
    parallel when the two lines' rise over the span differs by <= par_tol x
    width; trending when both rise >= smin ATR over the span (descending is the
    mirror); width wmin..wmax ATR; every bar since the first pivot inside the
    lines (tol ATR slack). The structure is rebuilt whenever a new pivot is
    confirmed and lives `wait` bars after its last pivot.
      bounce  ascending channel only: the bar's low comes within tol ATR of the
              lower line, the close is above it and green -> buy; stop lower
              line - pad ATR (or the bar low); target the upper line
      break   first close beyond either line by brk ATR -> trade that way;
              kind 'with' (a break up out of an ascending channel) or 'against'
              (up out of a descending one); stop = channel middle - pad ATR,
              target + one channel width"""
    out = []
    S = None
    for t, d0, lows, highs, nl, nh in _sessions(B, k):
        if t == d0:
            S = None
        A = B.atr[t]
        if S is not None and t - max(S["piv"]) > wait + k:
            S = None
        if S is not None:
            u, d = S["up"](t), S["dn"](t)
            mid, w = 0.5 * (u + d), u - d
            if "break" in modes and B.c[t] > u + brk * A and not S.get("broke"):
                kind = "with" if S["dir"] > 0 else "against"
                out.append(_sig(t, 1, B.c[t], mid - pad * A, B.c[t] + w, "break", kind, ("CH",) + S["piv"]))
                S["broke"] = True
            elif ("bounce" in modes and S["dir"] > 0 and not S.get("bounced") and not S.get("broke")
                  and B.l[t] <= d + tol * A and B.c[t] > d and B.c[t] > B.o[t] and B.c[t] < mid):
                out.append(_sig(t, 1, B.c[t], min(d, B.l[t]) - pad * A, u, "bounce", "with", ("CH",) + S["piv"]))
                S["bounced"] = True
            if B.c[t] < d - brk * A or B.c[t] > u + brk * A:
                S["broke"] = True       # either way out ends the structure for bounces
        if nl or nh:
            X = _two_lines(B, highs, lows, t, maxspan)
            if X is not None:
                span = t - X["first"]
                w0 = X["up"](t) - X["dn"](t)
                rise_u, rise_d = X["su"] * span, X["sl"] * span
                ok = (wmin * A <= w0 <= wmax * A and abs(rise_u - rise_d) <= par_tol * w0
                      and _contained(B, X, X["first"], t + 1, tol))
                if ok and min(rise_u, rise_d) >= smin * A:
                    X["dir"] = 1
                elif ok and max(rise_u, rise_d) <= -smin * A:
                    X["dir"] = -1
                else:
                    ok = False
                if ok and (S is None or S["piv"] != X["piv"]):
                    S = X
    return out


def triangle(B, k, tol, maxspan, flat, hmin, hmax, wmin_frac, brk, pad, wait):
    """Triangle from the last two pivot highs and the last two pivot lows, all
    within maxspan bars, every bar since the first pivot inside the lines:
      sym   upper line falls and lower line rises, each by > flat ATR over the span
      asc   upper flat (|rise| <= flat ATR), lower rises > flat ATR
      desc  lower flat, upper falls > flat ATR
    Opening height hmin..hmax ATR; still open (width now >= wmin_frac of the
    opening height). Signal: first close above the upper line by brk ATR -> buy
    (the bearish mirror covers the lower line). kind = 'classic' (an ascending
    triangle broken up / descending broken down), 'counter' (broken against
    its bias) or 'sym'; geom = the triangle's shape in the original frame. Stop: the last pivot low - pad ATR; target: entry + opening height."""
    out = []
    S = None
    for t, d0, lows, highs, nl, nh in _sessions(B, k):
        if t == d0:
            S = None
        A = B.atr[t]
        if S is not None and t - max(S["piv"]) > wait + k:
            S = None
        if S is not None:
            u, d = S["up"](t), S["dn"](t)
            if u - d < wmin_frac * S["H0"]:
                S = None
            elif B.c[t] > u + brk * A:
                stop = B.l[S["piv"][3]] - pad * A
                out.append(_sig(t, 1, B.c[t], stop, B.c[t] + S["H0"], "break",
                                {"asc": "classic", "desc": "counter", "sym": "sym"}[S["kind"]], ("TRI",) + S["piv"],
                                geom=S["kind"]))
                S = None
            elif B.c[t] < d - brk * A:
                S = None
        if (nl or nh):
            X = _two_lines(B, highs, lows, t, maxspan)
            if X is None:
                continue
            span = t - X["first"]
            ru, rd = X["su"] * span, X["sl"] * span
            if ru < -flat * A and rd > flat * A:
                kind = "sym"
            elif abs(ru) <= flat * A and rd > flat * A:
                kind = "asc"
            elif abs(rd) <= flat * A and ru < -flat * A:
                kind = "desc"
            else:
                continue
            H0 = X["up"](X["first"]) - X["dn"](X["first"])
            if not (hmin * A <= H0 <= hmax * A) or not _contained(B, X, X["first"], t + 1, tol):
                continue
            if S is None or S["piv"] != X["piv"]:
                X.update(kind=kind, H0=H0)
                S = X
    return out


# ----------------------------------------------------------------------------
# flags and pennants
# ----------------------------------------------------------------------------
def flag(B, pole_min, pmax, cmin, cmax, retr, brk, pad, shape="flag", trig="pole", shrink=0.8):
    """Bull flag / pennant at bar t (same session throughout):
      pole top q = the highest high of bars t-cmax .. t-1, with cmin..cmax bars
      after it (the consolidation q+1 .. t-1); pole bottom s = the lowest low
      within pmax bars before q; pole height h[q] - l[s] >= pole_min ATR.
      The consolidation gives back at most `retr` of the pole.
      shape 'flag'    any such consolidation
            'pennant' its second half's range is inside the first half's and
                      at most `shrink` of it (converging)
      trig  'pole'    first close above the pole top
            'flag'    first close above the highest high of the consolidation's
                      second half (the flag's own upper edge)
    Stop: consolidation low - pad ATR. Target: entry + pole height."""
    out, used = [], set()
    for d0, d1 in B.days:
        for t in range(d0 + cmin + 2, d1):
            a = max(d0, t - cmax)
            q = a + int(np.argmax(B.h[a:t]))
            nc = t - 1 - q
            if nc < cmin or q in used:
                continue
            A = B.atr[t]
            s0 = max(d0, q - pmax)
            s = s0 + int(np.argmin(B.l[s0:q + 1]))
            if s >= q:
                continue
            P = B.h[q] - B.l[s]
            if P < pole_min * A:
                continue
            cl = B.l[q + 1:t].min()
            if B.h[q] - cl > retr * P or cl <= B.l[s]:
                continue
            half = (q + 1 + t) // 2
            if shape == "pennant":
                r1 = B.h[q + 1:half].max(initial=-np.inf) - B.l[q + 1:half].min(initial=np.inf)
                hi2, lo2 = B.h[half:t].max(), B.l[half:t].min()
                if half <= q + 1 or not (hi2 <= B.h[q + 1:half].max() and lo2 >= B.l[q + 1:half].min()
                                         and hi2 - lo2 <= shrink * r1):
                    continue
            lvl = B.h[q] if trig == "pole" else B.h[half:t].max()
            if B.c[t] > lvl + brk * A and B.c[t - 1] <= lvl:
                used.add(q)
                out.append(_sig(t, 1, B.c[t], cl - pad * A, B.c[t] + P, trig, shape, ("FLAG", s, q),
                                pole=P))
    return out


# ----------------------------------------------------------------------------
# registry and two-sided wrapper
# ----------------------------------------------------------------------------
DEFAULTS = dict(
    double=dict(k=3, tol=0.3, minsep=5, maxsep=60, hmin=1.0, hmax=8.0, wait=30, brk=0.0,
                rt_tol=0.25, rt_wait=15, pad=0.1, stop_mode="low"),
    triple=dict(k=3, tol=0.5, minsep=4, maxsep=45, hmin=1.0, hmax=8.0, wait=30, brk=0.0,
                rt_tol=0.25, rt_wait=15, pad=0.1, stop_mode="low"),
    ihs=dict(k=3, sh_tol=0.75, head_min=0.3, minsep=4, maxsep=45, hmin=1.0, hmax=8.0, neck_tol=1.0,
             wait=30, brk=0.0, rt_tol=0.25, rt_wait=15, pad=0.1, stop_mode="last"),
    box=dict(k=2, W=30, touch_tol=0.3, hmin=1.5, hmax=6.0, brk=0.1, pad=0.1, poke=0.5),
    channel=dict(k=3, tol=0.35, maxspan=90, par_tol=0.35, smin=1.0, wmin=1.5, wmax=6.0, brk=0.1, pad=0.1,
                 wait=30),
    triangle=dict(k=3, tol=0.35, maxspan=90, flat=0.5, hmin=1.5, hmax=8.0, wmin_frac=0.2, brk=0.1,
                  pad=0.1, wait=30),
    flag=dict(pole_min=3.0, pmax=15, cmin=3, cmax=20, retr=0.5, brk=0.0, pad=0.1, shape="flag",
              trig="pole", shrink=0.8),
)

NAMES = {   # bullish name, bearish name, family
    "double": ("double_bottom", "double_top"),
    "triple": ("triple_bottom", "triple_top"),
    "ihs": ("inverse_hs", "head_shoulders"),
    "box": ("box_up", "box_down"),
    "channel": ("channel_up", "channel_down"),
    "triangle": ("triangle_up", "triangle_down"),
    "flag": ("bull_flag", "bear_flag"),
}


def _detect_long(B, fam, p):
    B._piv_k = p.get("k"); B._piv = pivots(B, p["k"]) if "k" in p else None
    if fam in ("double", "triple", "ihs"):
        if fam == "double":
            form = form_double(p["tol"], p["minsep"], p["maxsep"], p["hmin"], p["hmax"])
        elif fam == "triple":
            form = form_triple(p["tol"], p["minsep"], p["maxsep"], p["hmin"], p["hmax"])
        else:
            form = form_ihs(p["sh_tol"], p["head_min"], p["minsep"], p["maxsep"], p["hmin"], p["hmax"],
                            p["neck_tol"])
        return _neck_runner(B, p["k"], form, p["wait"], p["brk"], p["rt_tol"], p["rt_wait"], p["pad"],
                            p["stop_mode"])
    if fam == "box":
        return range_box(B, p["k"], p["W"], p["touch_tol"], p["hmin"], p["hmax"], p["brk"], p["pad"], p["poke"])
    if fam == "channel":
        return channel(B, p["k"], p["tol"], p["maxspan"], p["par_tol"], p["smin"], p["wmin"], p["wmax"],
                       p["brk"], p["pad"], p["wait"])
    if fam == "triangle":
        return triangle(B, p["k"], p["tol"], p["maxspan"], p["flat"], p["hmin"], p["hmax"], p["wmin_frac"],
                        p["brk"], p["pad"], p["wait"])
    if fam == "flag":
        return flag(B, p["pole_min"], p["pmax"], p["cmin"], p["cmax"], p["retr"], p["brk"], p["pad"],
                    p["shape"], p["trig"], p["shrink"])
    raise KeyError(fam)


def detect(B, fam, sides=(1, -1), **over):
    """Both sides of one pattern family on Bars B, in B's price frame.
    over: parameter overrides on DEFAULTS[fam]. Returns signals sorted by t;
    each carries 'pattern' (e.g. 'double_bottom' / 'double_top')."""
    p = dict(DEFAULTS[fam]); p.update(over)
    out = []
    for side in sides:
        X = B if side > 0 else B.neg()
        for s in _detect_long(X, fam, p):
            if side < 0:
                s = dict(s, side=-1, entry=-s["entry"], stop=-s["stop"],
                         target=None if s["target"] is None else -s["target"],
                         **{k: -s[k] for k in ("neck", "top", "bot") if k in s})
                if "top" in s:
                    s["top"], s["bot"] = s["bot"], s["top"]
                if "geom" in s and s["geom"] in ("asc", "desc"):
                    s["geom"] = "desc" if s["geom"] == "asc" else "asc"
            s["pattern"] = NAMES[fam][0 if side > 0 else 1]
            out.append(s)
    return sorted(out, key=lambda s: (s["t"], -s["side"]))
