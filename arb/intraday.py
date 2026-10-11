"""Intraday option-buying: which contract to buy, and whether to buy at all.

The arbitrage scanners in arb/strategies.py build risk-free structures out of
four legs. This module does something different and much weaker, and the
difference must not be blurred: it uses the same chain arithmetic only to rank
*single* strikes as dear or cheap, and then buys one of them outright. A naked
long option is a directional bet. Nothing here is arbitrage, and the profit is
not locked at entry - it is decided by where the index goes next.

Two independent things have to agree before a trade is taken:

  direction   from arb/smc.py - market structure on the index candles
  contract    the strike inside ATM +/- N whose ONE LOT fits the capital, is
              quoting properly, and carries enough delta to move

Delta is measured off the chain itself (dC/dK across adjacent strikes), never
from a pricing model. A model would need a volatility input, and a wrong vol
input silently sizes every stop wrong.
"""
from dataclasses import dataclass, field

from arb.smc import BULL, BEAR


@dataclass
class Candidate:
    symbol: str
    token: str
    strike: float
    opt: str                 # "CE" | "PE"
    ask: float
    bid: float
    lot_size: int
    lot_cost: float          # ask * lot_size - the rupees this trade costs
    delta: float             # |dPremium/dSpot|, from the chain
    cheapness: float         # premium points the strike is under its neighbours
    offset: int              # strikes away from ATM (signed)
    depth: float
    age_ms: float
    expiry: str = ""
    underlying: str = ""
    segment: str = "NFO"
    detail: dict = field(default_factory=dict)


def atm_index(strikes, spot):
    """Index of the strike nearest spot."""
    best, bi = None, 0
    for i, k in enumerate(strikes):
        d = abs(k - spot)
        if best is None or d < best:
            best, bi = d, i
    return bi


def chain_delta(prices, strikes, i, opt):
    """|dPremium/dSpot| at strike i, straight from the chain.

    For calls dC/dK = -delta, for puts dP/dK = +(1-delta); either way the
    magnitude of the central difference across adjacent strikes is the local
    sensitivity. Model-free, and it degrades gracefully at the edge of the
    ladder where only a one-sided difference exists.
    """
    n = len(strikes)
    if n < 2:
        return 0.0
    lo, hi = max(0, i - 1), min(n - 1, i + 1)
    dk = strikes[hi] - strikes[lo]
    if dk <= 0:
        return 0.0
    d = abs(prices[hi] - prices[lo]) / dk
    return max(0.0, min(1.0, d))


def convexity_cheapness(prices, strikes, i):
    """How far strike i sits BELOW the straight line joining its neighbours.

    C(K) must be convex, so the middle of any three equally spaced strikes can
    never exceed the average of the outer two. A middle that sits unusually far
    under that average is locally cheap - the same arithmetic scan_butterfly
    uses, read one strike at a time instead of as a tradable structure.

    Positive = cheap. Units are premium points.
    """
    if i <= 0 or i >= len(strikes) - 1:
        return 0.0
    k0, k1, k2 = strikes[i - 1], strikes[i], strikes[i + 1]
    p0, p1, p2 = prices[i - 1], prices[i], prices[i + 1]
    if not all(p > 0 for p in (p0, p1, p2)):
        return 0.0
    span = k2 - k0
    if span <= 0:
        return 0.0
    w = (k2 - k1) / span
    fair = w * p0 + (1.0 - w) * p2      # linear interpolation between neighbours
    return float(fair - p1)


class ContractSelector:
    """Picks the one contract to buy for a given direction and purse."""

    def __init__(self, limits, log=lambda *_: None):
        self.limits = limits
        self.log = log
        self.last_reject = ""
        # Which check stopped the last refused selection ("cost", "stale",
        # "thin", "spread", "delta_floor", "delta_ceiling", "no_quote", ...)
        # and, for "cost", the cheapest lot that was out of reach.
        self.last_reject_kind = ""
        self.last_cheapest = None       # (lot_cost, strike) or None

    def select(self, cv, spot, side, capital, max_age_ms, min_depth_mult,
               max_delta=None, max_delta_fn=None):
        """Best affordable contract for `side`, or None.

        `cv` is a ChainView (arb/strategies.py); `capital` is rupees available
        right now, already net of anything else deployed.
        """
        L = self.limits
        opt = "CE" if side == BULL else "PE"
        strikes = list(cv.strikes)
        self.last_reject_kind, self.last_cheapest = "", None
        if len(strikes) < 3:
            self.last_reject, self.last_reject_kind = "chain too short", "chain"
            return None

        if opt == "CE":
            ask, bid = cv.ce_ask, cv.ce_bid
            askq, age = cv.ce_askq, cv.ce_age
            syms, toks = cv.ce_symbol, cv.ce_token
        else:
            ask, bid = cv.pe_ask, cv.pe_bid
            askq, age = cv.pe_askq, cv.pe_age
            syms, toks = cv.pe_symbol, cv.pe_token

        a = atm_index(strikes, spot)
        # The subscribed ladder must actually contain the money. If spot sits
        # beyond the outermost strike, the ladder was centred on a wrong price
        # at start-up (spot lookup refused) and every "ATM" here is a lie.
        step = (strikes[-1] - strikes[0]) / max(len(strikes) - 1, 1)
        if spot < strikes[0] - step or spot > strikes[-1] + step:
            self.last_reject = (f"spot {spot:,.0f} outside subscribed strikes "
                                f"{strikes[0]:,.0f}-{strikes[-1]:,.0f} - restart to re-centre")
            self.last_reject_kind = "chain"
            return None
        qty = cv.lot_size
        lo = max(0, a - L.max_strike_offset)
        hi = min(len(strikes) - 1, a + L.max_strike_offset)

        out, seen_any = [], False
        top = None          # the largest per-candidate delta ceiling met (D-L4)
        # The check that stopped each strike: the refusal names what actually
        # bound, not the delta ceiling every time (a purse too small for one
        # lot read "no strike at delta <= 1.95 fits the risk cap" - 11 Oct 2026).
        fails = {}
        cheapest = None
        for i in range(lo, hi + 1):
            px, dq, ag = float(ask[i]), float(askq[i]), float(age[i])
            if px <= 0 or float(bid[i]) <= 0:
                continue
            seen_any = True
            if ag > max_age_ms:
                fails["stale"] = fails.get("stale", 0) + 1
                continue
            if dq < qty * min_depth_mult:
                fails["thin"] = fails.get("thin", 0) + 1
                continue
            lot_cost = px * qty
            if lot_cost > capital:
                fails["cost"] = fails.get("cost", 0) + 1
                if cheapest is None or lot_cost < cheapest[0]:
                    cheapest = (lot_cost, float(strikes[i]))
                continue
            # A spread wider than the edge we are hunting is a losing trade the
            # moment it is opened: we pay the ask and can only leave at the bid.
            spread = px - float(bid[i])
            if spread > L.max_spread_points:
                fails["spread"] = fails.get("spread", 0) + 1
                continue
            d = chain_delta(ask, strikes, i, opt)
            if d < L.min_delta:
                fails["delta_floor"] = fails.get("delta_floor", 0) + 1
                continue
            # Delta sizing: the most delta whose rupee risk at the chart's own
            # stop still fits the per-trade cap. The stop is not moved; the
            # contract is chosen to fit it.
            # With costs inside the cap the ceiling depends on the contract's
            # own premium and spread, so it is asked per candidate.
            ceiling = max_delta_fn(px, spread) if max_delta_fn is not None else max_delta
            if ceiling is not None:
                top = ceiling if top is None else max(top, ceiling)
            if ceiling is not None and d > ceiling:
                fails["delta_ceiling"] = fails.get("delta_ceiling", 0) + 1
                continue
            out.append(Candidate(
                symbol=syms[i], token=toks[i], strike=float(strikes[i]), opt=opt,
                ask=px, bid=float(bid[i]), lot_size=qty, lot_cost=lot_cost,
                delta=d, cheapness=convexity_cheapness(ask, strikes, i),
                offset=i - a, depth=dq, age_ms=ag, expiry=cv.expiry,
                underlying=cv.underlying, segment=cv.segment,
                detail={"spread": spread, "atm_strike": float(strikes[a])}))

        if not out:
            # The ceiling the candidates were actually held to: with costs
            # inside the cap (max_delta_fn) it is lower than the cost-free
            # max_delta, and quoting that one misreported the refusal (D-L4).
            limit = top if top is not None else max_delta
            if not seen_any:
                self.last_reject, self.last_reject_kind = "no strike quoting", "no_quote"
            elif fails.get("delta_ceiling"):
                # The risk cap is to blame only when a strike got as far as it.
                self.last_reject_kind = "delta_ceiling"
                if limit is not None and limit < L.min_delta:
                    self.last_reject = "chart stop too wide for the risk cap even at min_delta"
                else:
                    self.last_reject = (f"no strike at delta <= {limit:.2f} "
                                        f"fits the risk cap")
            elif fails.get("cost") and set(fails) <= {"cost", "delta_floor"}:
                # Every strike was either a lot the purse cannot pay for or one
                # too far out of the money to count: the purse is the limit.
                self.last_reject_kind, self.last_cheapest = "cost", cheapest
                self.last_reject = (f"no affordable strike: cheapest lot Rs {cheapest[0]:,.0f} "
                                    f"({cheapest[1]:,.0f}) > free capital Rs {capital:,.0f}")
            else:
                names = {"stale": f"quote older than {max_age_ms:g} ms", "thin": "too thin",
                         "cost": f"lot over Rs {capital:,.0f}",
                         "spread": f"spread over {L.max_spread_points:g}",
                         "delta_floor": f"delta under {L.min_delta:g}"}
                self.last_reject_kind = max(fails, key=fails.get)
                self.last_reject = (f"no tradable strike in ATM+/-{L.max_strike_offset}: "
                                    + ", ".join(f"{n} {names[k]}" for k, n in
                                                sorted(fails.items(), key=lambda kv: -kv[1])))
            return None

        # "Lot price nearest our capital": the most expensive contract we can
        # actually afford. That is the closest to the money, so it carries the
        # most delta - a cheaper far-OTM strike would need a bigger index move
        # to pay the same rupees. Cheapness breaks ties between neighbours.
        out.sort(key=lambda c: (c.lot_cost + 2.0 * c.cheapness * c.lot_size),
                 reverse=True)
        self.last_reject, self.last_reject_kind = "", ""
        return out[0]


def structural_invalidation(structure, side, invalidation=None):
    """The index level where the idea dies: the setup's own, when it is on the
    correct side of price, else the last swing. 0.0 when neither exists."""
    invalid = structure.invalidation(side)
    if invalidation:
        ref = structure.closes[-1] if structure.closes else 0.0
        if ref > 0 and ((side == BULL and invalidation < ref)
                        or (side == BEAR and invalidation > ref)):
            invalid = invalidation
    return invalid or 0.0


def plan_trade(cand, structure, side, limits, cost_points, invalidation=None,
               max_risk_inr=None, atr=0.0, extra_risk_points=0.0):
    """Turn a chosen contract into an entry with a stop and a target.

    Risk is defined on the INDEX, where structure lives: the trade is wrong if
    price closes past the swing that framed it. That index distance is converted
    into premium with the chain's own delta, so the stop sits where the idea
    dies rather than at a round rupee number.

    `invalidation` overrides the generic swing level with the one the setup
    itself nominates - the sweep wick, the far side of the order block, the
    level past the golden pocket (arb/playbooks.py). That is nearly always the
    tighter and more meaningful stop, because it is the price that specific
    setup says should not trade again, whereas the swing is merely the last
    place structure changed. A setup-supplied level is used only when it sits
    on the correct side of spot; a stop above entry on a long is a bug in the
    setup, not a trade.
    """
    invalid = structural_invalidation(structure, side, invalidation)
    if not invalid or invalid <= 0:
        return None
    spot_ref = structure.closes[-1] if structure.closes else 0.0
    if spot_ref <= 0:
        return None

    index_risk = abs(spot_ref - invalid)
    if index_risk <= 0:
        return None
    # Convert to premium, and never trust a stop tighter than the spread we
    # already pay - that stop is noise and would be taken out by the touch.
    prem_risk = max(index_risk * cand.delta, cand.detail["spread"] * 2.0)
    prem_risk = min(prem_risk, cand.ask * limits.max_risk_frac_of_premium)
    if prem_risk <= 0:
        return None

    # The capital cap. At Rs 10,000 no single trade may lose more than
    # max_risk_pct_per_trade of the day's capital, however wide the chart says
    # the idea's invalidation is: a market that wants to go against us goes
    # through a wide stop too, just more expensively. stop_policy decides what
    # a too-wide structural stop means:
    #   "delta" (default) the contract was already chosen with a delta small
    #           enough for the chart's stop to fit (ContractSelector max_delta);
    #           the stop is never moved, so anything still over is refused
    #   "skip"  refuse the trade
    #   "cap"   tighten the stop to the limit. Tested and rejected by the owner:
    #           on 7 real sessions it lost Rs 12,720 over 102 trades, because a
    #           stop pulled inside the structure is hit by ordinary noise.
    capped = False
    # extra_risk_points (risk_includes_costs): the round trip and the expected
    # exit slippage count inside the cap, with no rounding allowance - the cap
    # is then the most a stopped-out trade can lose, costs included.
    extra = float(extra_risk_points or 0.0)
    if max_risk_inr and cand.lot_size > 0:
        cap_pts = max_risk_inr / cand.lot_size
        # A few percent over is chain-delta rounding, not a wide stop.
        if prem_risk + extra > (cap_pts if extra else cap_pts * 1.05):
            if str(getattr(limits, "stop_policy", "delta")) != "cap":
                return None
            prem_risk = cap_pts - extra
            capped = True
            d = cand.delta if cand.delta > 0 else 0.5
            index_risk = prem_risk / d
            invalid = spot_ref - index_risk if side == BULL else spot_ref + index_risk
    # A stop inside one ordinary bar's noise is a coin toss, not a stop.
    floor = float(getattr(limits, "min_stop_atr", 0.0) or 0.0)
    if floor > 0 and atr > 0 and index_risk < floor * atr:
        return None
    if prem_risk < cand.detail["spread"] * 2.0:
        return None

    target = cand.ask + limits.reward_risk * prem_risk
    stop = cand.ask - prem_risk

    # The move has to be worth making after both sides of the cost are paid.
    gross_target_points = limits.reward_risk * prem_risk
    if gross_target_points - cost_points < limits.min_net_points:
        return None
    # Friction as a share of the risk taken. A stop so tight that the round
    # trip is a big slice of it is a trade the spread and fees mostly decide.
    # On 7 real sessions (15-24 Sep 2026, research/PAPERS.md section 0) the
    # tight-stop trades were where the whole after-cost loss came from.
    # With costs inside the cap (extra > 0) the bet is the stop PLUS the costs
    # and slippage it already carries, so friction is measured against that
    # whole: measured against the stop alone, the same cost is counted twice
    # and the window left between the friction floor and the cap is a few
    # points wide on every index.
    frac = float(getattr(limits, "max_cost_frac_of_risk", 0.0) or 0.0)
    if frac > 0 and cost_points > frac * (prem_risk + extra):
        return None
    if stop <= 0.05:
        return None

    return {
        "entry": cand.ask,
        "stop": stop,
        "target": target,
        "risk_points": prem_risk,
        "reward_points": limits.reward_risk * prem_risk,
        "index_invalidation": invalid,
        "index_risk": index_risk,
        "risk_inr": prem_risk * cand.lot_size,
        # What a stop-out is planned to cost in all: the premium stop plus the
        # round trip and exit slippage when they count inside the cap.
        "planned_risk_inr": (prem_risk + extra) * cand.lot_size,
        "reward_inr": limits.reward_risk * prem_risk * cand.lot_size,
        "cost_points_round_trip": cost_points,
        "delta": cand.delta,
        "stop_capped": capped,
    }
