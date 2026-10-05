#!/usr/bin/env python3
"""Premium pace vs index pace on the RECORDED option books (owner's question, 5 Oct 2026).

"ATR ko index se shift karke premium per shift kardo ... dono direction me call and
put ... dono ke relation ka fayda" - does the ATM call/put premium show a spike
BEFORE the index pace gate (ATR20 / baseline) opens, and does that spike carry
information about the next minutes that the index itself does not already carry?

Data: data/book_*.f4 (arb/recorder.py), the only real option-premium history we
have: Sep 17 (12:30-15:30 at 4 Hz), Sep 25, 28, 29 and Oct 5 at one frame a minute.
Everything is resampled to one value per minute (last frame of the minute).

Per index, per minute, nearest expiry:
  S      synthetic spot  K + C - P at the strike where |C - P| is smallest
  ce, pe mid of the ATM call / put; their 1-minute change is taken on the SAME
         contract (the strike chosen at t-1), so a strike roll is not a "spike"
  dir    (dCE - dPE) / 2   the directional part  (~ delta * dS)
  vol    (dCE + dPE) / 2   the both-legs-together part (~ vega * dIV + theta)
Measures compared at every minute t (all use data up to t only):
  index pace   mean|dS| over 20 min / median|dS| of the day so far   (the live gate's idea)
  fast index   mean|dS| over 3 min  / same baseline
  premium pace mean(|dCE|+|dPE|) over 3 min / its own day-so-far median
and what follows: |S(t+5) - S(t)| and |S(t+10) - S(t)| in index points.

    venv/bin/python research/premium_pace_test.py > research/premium_pace_result.txt
"""
import glob, json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.recorder import load_session

UNDER = ["MIDCPNIFTY", "FINNIFTY", "BANKNIFTY", "SENSEX", "NIFTY"]


def underlying(sym):
    for u in UNDER:
        if sym.startswith(u):
            return u
    return None


def exp_date(s):
    return datetime.strptime(s, "%d%b%Y").date()


def minute_series(meta_path):
    """{(day, underlying): {minute 'HH:MM': (S, {strike: (ce, pe)}, lot)}} for one recording."""
    meta, arr = load_session(meta_path)
    if arr.shape[0] < 2:
        return {}
    t0 = datetime.fromisoformat(meta["started_ist"]).replace(tzinfo=None)
    t1 = datetime.fromtimestamp(os.path.getmtime(meta_path.replace(".meta.json", ".f4")))
    period = max(0.2, (t1 - t0).total_seconds() / arr.shape[0])
    day = t0.date()
    best = {}
    for e in meta["expiries"]:
        u = underlying(e["ce_symbol"][0]) if e["ce_symbol"] else None
        ed = exp_date(e["expiry"])
        if u is None or ed < day:
            continue
        if u not in best or ed < exp_date(best[u]["expiry"]):
            best[u] = e
    out = defaultdict(dict)
    mid = (arr[:, :, 1] + arr[:, :, 2]) / 2.0
    ok = (arr[:, :, 1] > 0) & (arr[:, :, 2] > 0) & (arr[:, :, 2] >= arr[:, :, 1])
    for i in range(arr.shape[0]):
        ts = t0 + timedelta(seconds=i * period)
        hm = ts.strftime("%H:%M")
        if not ("09:15" <= hm <= "15:30"):
            continue
        for u, e in best.items():
            ks, cs, ps = e["strikes"], e["ce_slots"], e["pe_slots"]
            quotes = {}
            for k, c, p in zip(ks, cs, ps):
                if c < mid.shape[1] and p < mid.shape[1] and ok[i, c] and ok[i, p]:
                    quotes[k] = (float(mid[i, c]), float(mid[i, p]))
            if len(quotes) < 3:
                continue
            k0 = min(quotes, key=lambda k: abs(quotes[k][0] - quotes[k][1]))
            S = k0 + quotes[k0][0] - quotes[k0][1]
            out[(str(day), u)][hm] = (S, quotes, e["lot_size"])   # last frame in the minute wins
    return out


def load_all():
    days = defaultdict(dict)
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "book_*.meta.json"))):
        for key, mins in minute_series(p).items():
            days[key].update(mins)
    return days


def build(mins):
    """Minute-aligned arrays; a gap > 2 min breaks the chain (dS etc. = nan)."""
    hms = sorted(mins)
    rows = []
    prev_hm, prev = None, None
    for hm in hms:
        S, q, lot = mins[hm]
        if prev is not None:
            gap = (datetime.strptime(hm, "%H:%M") - datetime.strptime(prev_hm, "%H:%M")).seconds / 60
        else:
            gap = 99
        if prev is not None and gap <= 2:
            pS, pq = prev
            k = min(pq, key=lambda k: abs(k - pS))          # ATM at t-1, same contract at t
            if k in q:
                dce, dpe = q[k][0] - pq[k][0], q[k][1] - pq[k][1]
            else:
                dce = dpe = np.nan
            rows.append((hm, S, S - pS, dce, dpe, q.get(k, (np.nan, np.nan))))
        else:
            rows.append((hm, S, np.nan, np.nan, np.nan, (np.nan, np.nan)))
        prev_hm, prev = hm, (S, q)
    return rows


def ratio(x, i, n, base):
    w = x[max(0, i - n + 1):i + 1]
    w = w[~np.isnan(w)]
    if len(w) < n or not base:
        return np.nan
    return float(np.mean(w)) / base


def main():
    days = load_all()
    print("recorded option books, minutes with a usable ATM call+put per index-day:")
    for (d, u), mins in sorted(days.items()):
        print(f"  {d} {u:10s} {len(mins):4d} min  {min(mins)}-{max(mins)}")
    feats = []        # one row per minute with everything known at t and the outcome after
    for (d, u), mins in sorted(days.items()):
        R = build(mins)
        hm = [r[0] for r in R]
        S = np.array([r[1] for r in R]); dS = np.abs(np.array([r[2] for r in R]))
        dce = np.array([r[3] for r in R]); dpe = np.array([r[4] for r in R])
        prem = np.abs(dce) + np.abs(dpe)
        dirn = (dce - dpe) / 2.0; voln = (dce + dpe) / 2.0
        for i in range(len(R)):
            past = dS[:i + 1]; past = past[~np.isnan(past)]
            pp = prem[:i + 1]; pp = pp[~np.isnan(pp)]
            if len(past) < 20 or len(pp) < 20:
                continue
            bS, bP = float(np.median(past)), float(np.median(pp))
            j5, j10 = i + 5, i + 10
            f5 = abs(S[j5] - S[i]) if j5 < len(R) and hm[j5] <= _plus(hm[i], 7) else np.nan
            f10 = abs(S[j10] - S[i]) if j10 < len(R) and hm[j10] <= _plus(hm[i], 13) else np.nan
            feats.append(dict(d=d, u=u, hm=hm[i], base=bS,
                              idx20=ratio(dS, i, 20, bS), idx3=ratio(dS, i, 3, bS), prem3=ratio(prem, i, 3, bP),
                              vol3=np.nansum(voln[max(0, i - 2):i + 1]), dir3=np.nansum(dirn[max(0, i - 2):i + 1]),
                              f5=f5, f10=f10))
    print(f"\nminutes with a 20-minute history and a baseline: {len(feats)}")
    _table(feats)
    _lead(days)


def _plus(hm, m):
    t = datetime.strptime(hm, "%H:%M") + timedelta(minutes=m)
    return t.strftime("%H:%M")


def _table(F):
    """What follows a high reading of each measure? Outcome in units of the day's typical 1-min move."""
    def show(name, sel):
        a5 = np.array([f["f5"] / f["base"] for f in sel if not np.isnan(f["f5"])])
        a10 = np.array([f["f10"] / f["base"] for f in sel if not np.isnan(f["f10"])])
        if len(a5) < 8:
            print(f"  {name:44s} n={len(a5):4d}  (too few)")
            return
        print(f"  {name:44s} n={len(a5):4d}  next 5 min |move| {np.mean(a5):5.2f}x  next 10 min {np.mean(a10):5.2f}x"
              f"  (median {np.median(a10):4.2f}x)")
    print("\nnext-minutes index move after each reading (x = the day's median 1-minute |move|):")
    show("all minutes", F)
    for lo, hi in ((0, 1.0), (1.0, 1.3), (1.3, 9)):
        show(f"index pace (20 min) {lo}-{hi}", [f for f in F if lo <= f["idx20"] < hi])
    for lo, hi in ((0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 99)):
        show(f"fast index pace (3 min) {lo}-{hi}", [f for f in F if lo <= f["idx3"] < hi])
    for lo, hi in ((0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 99)):
        show(f"premium pace (3 min CE+PE) {lo}-{hi}", [f for f in F if lo <= f["prem3"] < hi])
    show("premium >= 2 while index pace < 1.3", [f for f in F if f["prem3"] >= 2 and f["idx20"] < 1.3])
    show("premium >= 2 while fast index < 2", [f for f in F if f["prem3"] >= 2 and f["idx3"] < 2])
    show("fast index >= 2 while premium < 2", [f for f in F if f["idx3"] >= 2 and f["prem3"] < 2])
    show("both legs up (vol3 > 0, IV spike)", [f for f in F if f["prem3"] >= 2 and f["vol3"] > 0])
    show("both legs down (vol3 < 0)", [f for f in F if f["prem3"] >= 2 and f["vol3"] <= 0])
    # Does the premium spike say anything the fast index pace does not? Partial correlation.
    X = np.array([[f["idx3"], f["prem3"], f["idx20"], f["f10"] / f["base"]] for f in F
                  if not any(np.isnan([f["idx3"], f["prem3"], f["idx20"], f["f10"]]))])
    if len(X) > 30:
        c = np.corrcoef(X.T)
        print(f"\ncorrelations (n={len(X)}): fast-index~premium {c[0, 1]:+.2f}  | with next-10-min move:"
              f" fast index {c[0, 3]:+.2f}  premium {c[1, 3]:+.2f}  index pace(20) {c[2, 3]:+.2f}")
        A = np.column_stack([np.ones(len(X)), X[:, 0], X[:, 1]])
        beta, *_ = np.linalg.lstsq(A, X[:, 3], rcond=None)
        res = X[:, 3] - A @ beta
        A0 = np.column_stack([np.ones(len(X)), X[:, 0]])
        b0, *_ = np.linalg.lstsq(A0, X[:, 3], rcond=None)
        r0 = X[:, 3] - A0 @ b0
        print(f"  next-10-min |move| ~ fast index + premium: R2 {1 - res.var() / X[:, 3].var():.3f}"
              f" vs fast index alone {1 - r0.var() / X[:, 3].var():.3f}  (premium coef {beta[2]:+.3f})")


def _lead(days):
    """Lead-lag: correlation of premium |change| at t with index |change| at t+k (k = -3..+3)."""
    print("\nlead/lag, per minute: corr(premium |dCE|+|dPE| at t, index |dS| at t+k)")
    xs = defaultdict(list)
    for (d, u), mins in days.items():
        R = build(mins)
        dS = np.abs(np.array([r[2] for r in R]))
        pr = np.abs(np.array([r[3] for r in R])) + np.abs(np.array([r[4] for r in R]))
        for k in range(-3, 4):
            for i in range(len(R)):
                j = i + k
                if 0 <= j < len(R) and not np.isnan(pr[i]) and not np.isnan(dS[j]):
                    xs[k].append((pr[i], dS[j]))
    print("  " + "  ".join(f"k={k:+d}: {np.corrcoef(np.array(xs[k]).T)[0, 1]:+.2f}" for k in sorted(xs)
                           if len(xs[k]) > 30))
    print("  (k>0 = premium leads the index; k=0 same minute; k<0 = index leads the premium)")


if __name__ == "__main__":
    main()
