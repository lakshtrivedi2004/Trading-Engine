"""What the option chain says about the index move, and what a premium really moves.

Two of the owner's rules (research/PAPERS.md section 0h), both about the gap
between the index chart and the premium that is actually bought.

1. THE PREMIUM MOVES LESS THAN THE INDEX, AND LESS THAN ITS DELTA SAYS.
   One index point moves an at-the-money premium 0.5-0.6 points, and on a
   rally a call earns even less than that: index implied volatility falls as
   the index rises, so a call's realised sensitivity sits below its chain delta
   (Hull & White 2017; Simon 2013 finds the same intraday on SPY). On the
   recorded books (17 and 28 Sep 2026, nearest expiry, spread-filtered) calls
   realised ~0.74 of chain delta and puts ~0.94. Over 30 minutes a call's
   gains came in at 0.43-0.6x delta while its losses came in at ~1.0x - and
   even this measured figure still over-promises the 30-minute gain on a
   call, because it is fitted on one-minute changes. A target converted at chain delta therefore
   promises a premium move the market does not usually pay, and a trade that
   "clears its costs" on paper does not. `effective_delta` is the premium's own
   measured points-per-index-point, used for the reward side only. Risk keeps
   the chain delta, because losses realise in full.

2. CHECK THE OTHER SIDE BEFORE BELIEVING THE MOVE.
   For the call being bought at |delta| d, find the put at the same |delta|.
   If the index really moved, the call rose about d x move and the put fell
   about d x move. The matched pair separates two things:
       C - P  moves about 2d x the index: the direction the options priced
       C + P  is about delta-neutral: the change in volatility, i.e. whether
              buyers (vol bid) or writers (vol crush) are running the chain
   Two strikes at the same |delta| carry different skew and vega, so an
   uneven move is normal. Each leg is therefore judged against its OWN usual
   response to the index, regressed on the minutes before the window, not
   against the textbook "equal and opposite". When the index moves up but the
   pair prices a smaller move, the index has moved away from where the options
   anchor it. Goncalves-Pinto et al. (2020) find that such a gap is mostly
   price pressure on the underlying, which reverts toward the option-implied
   level. The neighbouring strikes are checked too: on a real move the deeper
   strike moves more than ours and the further one less, and a strike out of
   line with both is a stale or pushed quote (Perignon 2006), not a signal.

What this is NOT: a detector for the manipulation in SEBI's July 2025 Jane
Street order. There the options priced faithfully off the pushed index
("artificially inflated call option premiums and subdued put option
premiums"); the footprint was aggressive cash/futures buying in the morning,
sold into the close, on expiry days. A parity-style check cannot see that.

What the evidence does and does not say (research/PAPERS.md 0h): the pair's
lean correlated +0.15 to +0.24 with the next 10 minutes of index move on the
recorded books, t ~ 1-1.5, and nothing at 30 minutes. That is the sign
Goncalves-Pinto predicts, and it is not proof: Muravyev, Pearson & Broussard
(2013) find no economically meaningful price discovery in option quotes, and
Vipul (2008) finds NIFTY parity gaps close within two minutes. So by default
this is a graded read (`cross_side_mode = "grade"`) and every trade journals
it; the veto (`"gate"`) is one setting away once the journal says it earns
its place.

No pricing model is used anywhere here, in keeping with arb/intraday.py: the
deltas come off the chain, and the "normal" response of each contract is its
own regression on the index.
"""
from dataclasses import dataclass, field

import numpy as np

from arb.intraday import chain_delta
from arb.smc import BULL


def realised_beta(prem, index):
    """Slope of premium changes on index changes, and its R^2.

    `prem` and `index` are aligned per-minute series, oldest first. Returns
    (beta, r2, n) with n the number of one-minute changes used, or
    (0.0, 0.0, 0) when there is not enough to regress.
    """
    p = np.asarray(prem, dtype=float)
    s = np.asarray(index, dtype=float)
    if len(p) != len(s) or len(p) < 4:
        return 0.0, 0.0, 0
    dp, ds = np.diff(p), np.diff(s)
    ok = np.isfinite(dp) & np.isfinite(ds)
    dp, ds = dp[ok], ds[ok]
    n = int(len(ds))
    if n < 3:
        return 0.0, 0.0, 0
    vs = float(np.var(ds))
    if vs <= 0:
        return 0.0, 0.0, n
    cov = float(np.mean((ds - ds.mean()) * (dp - dp.mean())))
    beta = cov / vs
    vp = float(np.var(dp))
    r2 = (cov * cov) / (vs * vp) if vp > 0 else 0.0
    return beta, max(0.0, min(1.0, r2)), n


def effective_delta(chain, opt, prem, index, L):
    """Premium points per index point to expect on a FAVOURABLE move.

    The measured ratio (realised |beta| over chain delta) is shrunk toward a
    prior by how much it can be trusted - its R^2 and how many minutes back it
    reaches - so a cold start or a stale-quoted strike falls back to the prior
    instead of to noise. It is capped at the chain delta: over-stating the
    reward is the mistake that lets a cost-losing trade through, while
    under-stating it only skips one.

    Returns (effective delta, detail dict).
    """
    prior = float(getattr(L, "realised_delta_prior_ce" if opt == "CE"
                          else "realised_delta_prior_pe", 1.0))
    detail = {"chain_delta": round(chain, 3), "prior_ratio": prior}
    if chain <= 0:
        return 0.0, detail
    need = max(3, int(getattr(L, "realised_delta_bars", 30)))
    beta, r2, n = realised_beta(prem[-(need + 1):], index[-(need + 1):])
    ratio = prior
    if n >= 3 and beta != 0.0:
        measured = abs(beta) / chain
        w = r2 * min(1.0, n / float(need))
        ratio = w * measured + (1.0 - w) * prior
        detail.update(realised_beta=round(abs(beta), 3), r2=round(r2, 2),
                      minutes=n, weight=round(w, 2))
    ratio = max(float(getattr(L, "realised_delta_floor", 0.3)), min(1.0, ratio))
    detail["ratio"] = round(ratio, 3)
    return chain * ratio, detail


def mirror_index(cv, i, opt):
    """Strike index of the OPPOSITE option type whose chain |delta| is nearest
    the delta of `opt` at strike i. -1 if the chain cannot say."""
    k = [float(x) for x in cv.strikes]
    mine = cv.ce_ask if opt == "CE" else cv.pe_ask
    other = cv.pe_ask if opt == "CE" else cv.ce_ask
    d0 = chain_delta(mine, k, i, opt)
    if d0 <= 0:
        return -1
    best, bj = None, -1
    for j in range(1, len(k) - 1):
        if float(other[j]) <= 0:
            continue
        d = chain_delta(other, k, j, "PE" if opt == "CE" else "CE")
        if d <= 0:
            continue
        gap = abs(d - d0)
        if best is None or gap < best:
            best, bj = gap, j
    return bj


@dataclass
class CrossSide:
    """The chain's opinion of the recent index move, for one trade."""
    verdict: str = "no_data"     # see read(); "no_data" never counts either way
    move: float = 0.0            # index points over the window
    implied: float = 0.0         # index move the matched pair priced
    lean: float = 0.0            # implied - move, signed toward the trade (+ = options agree more)
    response: float = 0.0        # bought premium's change / its normal response
    mirror: float = 0.0          # opposite side's change / its normal response
    vol: float = 0.0             # matched strangle change per point of normal response
    ladder_ok: bool = True
    confirms: bool = False
    against: bool = False
    note: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"verdict": self.verdict, "move": round(self.move, 2),
                "implied": round(self.implied, 2), "lean": round(self.lean, 2),
                "response": round(self.response, 2), "mirror": round(self.mirror, 2),
                "vol": round(self.vol, 2), "ladder_ok": self.ladder_ok,
                "confirms": self.confirms, "against": self.against,
                "note": self.note, **self.detail}


def _normal_beta(prem, index, chain, sign, n_hist):
    """This contract's usual response to the index, measured on the minutes
    BEFORE the window being judged. Falls back to the signed chain delta."""
    b, r2, n = realised_beta(prem[:n_hist], index[:n_hist])
    if n >= 10 and r2 >= 0.2 and b * sign > 0.05:
        return b
    return sign * chain


def read(side, bought, mirror, index, neighbours, deltas, L, atr=0.0):
    """Judge the last `cross_side_bars` minutes of index move against the chain.

    `bought` and `mirror` are aligned per-minute mids of the contract being
    bought and of its matched-delta opposite; `index` is the spot on the same
    minutes. `neighbours` is (deeper, further) - the same-type strikes either
    side of the one being bought, aligned the same way, or None. `deltas` is
    (chain delta bought, chain delta mirror). `atr` is the index's 1-minute ATR,
    which sets how large a disagreement has to be before it means anything.

    Verdicts, relative to the trade's side:
        options_lead     the pair priced MORE of a move our way than the index
                         made - the chain is ahead of the chart
        confirmed        a move our way that the pair priced in full
        fake_move        a move our way that the pair did not price
        fake_pullback    a move against us that the pair did not price - the
                         retrace is not believed
        options_against  the pair priced a move against us beyond the index's
        quiet            too little index move and too little lean to judge
    A falling matched strangle (writers crushing volatility) never confirms:
    a long option loses to it whichever way the index goes.
    """
    W = int(getattr(L, "cross_side_bars", 10))
    out = CrossSide()
    n = min(len(bought), len(mirror), len(index))
    if W < 2 or n < W + 1:
        out.note = f"premium history {n}/{W + 1} minutes"
        return out
    b = np.asarray(bought[-n:], float)
    m = np.asarray(mirror[-n:], float)
    s = np.asarray(index[-n:], float)
    if not (np.all(np.isfinite(b[-W - 1:])) and np.all(np.isfinite(m[-W - 1:]))
            and np.all(np.isfinite(s[-W - 1:]))):
        out.note = "gap in the premium history"
        return out
    sgn = 1.0 if side == BULL else -1.0
    db, dm = deltas
    hist = n - W                      # minutes before the window
    beta_b = _normal_beta(b, s, db, sgn, hist)
    beta_m = _normal_beta(m, s, dm, -sgn, hist)
    mv = float(s[-1] - s[-W - 1])
    ch_b = float(b[-1] - b[-W - 1])
    ch_m = float(m[-1] - m[-W - 1])
    # The risk reversal's change over the sum of the two normal responses is the
    # index move the pair has priced; the strangle is what is left over.
    # beta_b and beta_m have opposite signs, so the ratio is the same index move
    # whichever of the two was bought.
    span = beta_b - beta_m
    implied = (ch_b - ch_m) / span if span else 0.0
    unit = 0.5 * (abs(beta_b) + abs(beta_m))
    out.move, out.implied = mv, implied
    out.lean = sgn * (implied - mv)
    out.response = ch_b / (beta_b * mv) if mv else 0.0
    out.mirror = ch_m / (beta_m * mv) if mv else 0.0
    out.vol = (ch_b + ch_m) / unit if unit else 0.0
    out.detail = {"beta_bought": round(beta_b, 3), "beta_mirror": round(beta_m, 3),
                  "bought_change": round(ch_b, 2), "mirror_change": round(ch_m, 2),
                  "window_min": W}

    # The ladder: a real move is ordered by delta across neighbouring strikes.
    deep, far = neighbours if neighbours else (None, None)
    if deep is not None and far is not None and len(deep) >= W + 1 and len(far) >= W + 1:
        cd = float(deep[-1] - deep[-W - 1])
        cf = float(far[-1] - far[-W - 1])
        if np.isfinite(cd) and np.isfinite(cf) and abs(mv) > 0:
            lo, hi = sorted((cd, cf))
            tol = float(getattr(L, "cross_side_ladder_tol", 0.5)) * max(abs(ch_b), 0.5)
            out.ladder_ok = lo - tol <= ch_b <= hi + tol
            out.detail["ladder"] = [round(cd, 2), round(ch_b, 2), round(cf, 2)]

    thr = float(getattr(L, "cross_side_lean_atr", 0.5)) * atr if atr > 0 else 0.0
    thr = max(thr, float(getattr(L, "cross_side_min_points", 0.0)))
    toward = sgn * mv
    if out.lean >= thr and out.lean > 0:
        out.verdict = "options_lead"
    elif out.lean <= -thr and out.lean < 0:
        out.verdict = "fake_move" if toward > 0 else "options_against"
    elif abs(mv) < thr:
        out.verdict = "quiet"
    elif toward > 0:
        out.verdict = "confirmed"
    else:
        # A retrace priced in full is neither for nor against a pullback entry;
        # one the pair did not price shows up as a positive lean above.
        out.verdict = "pullback_priced"
    if out.verdict == "options_lead" and toward < 0:
        out.verdict = "fake_pullback"

    crush = out.vol <= -float(getattr(L, "cross_side_crush", 0.3)) * max(abs(mv), atr, 1e-9)
    out.detail["vol_crush"] = bool(crush)
    out.confirms = (out.verdict in ("options_lead", "confirmed", "fake_pullback")
                    and out.ladder_ok and not crush)
    out.against = out.verdict in ("fake_move", "options_against")
    tag = {"options_lead": "options priced more of the move than the index made",
           "confirmed": "opposite side mirrored the move",
           "fake_move": "index moved our way, options did not price it",
           "fake_pullback": "retrace not priced by the options",
           "options_against": "options priced a move against the trade",
           "quiet": "too little move to judge",
           "pullback_priced": "retrace priced in full"}[out.verdict]
    out.note = (f"{tag}; index {mv:+.1f}, pair implied {implied:+.1f}"
                + ("; strangle falling (vol crush)" if crush else "")
                + ("" if out.ladder_ok else "; strike out of line with neighbours"))
    return out
