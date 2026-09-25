"""Candlestick patterns - as confirmation only, never as a signal.

The honest position first. Marshall, Young and Rose (Journal of Banking &
Finance, 2006) ran the first properly bootstrapped test of candlestick charting
on DJIA stocks and found no value in it; they repeated the result on the
Japanese equity market. Later work found some patterns profitable in Taiwan and
China, which is exactly the pattern of a weak effect that appears and disappears
across samples. Treating a candlestick pattern as a reason to trade is therefore
not supported by the evidence, and this module will not be used that way.

What a pattern IS good for is answering a much narrower question: price has
arrived at a level we already cared about for structural reasons - did it react
there, or drift through? An engulfing bar at an order block says the zone was
defended. The same bar in the middle of nowhere says nothing. So every function
here returns a direction, and the caller may only count it once price is already
in a zone that structure identified independently.

`candle` is [iso, o, h, l, c, v], matching CandleStore.
"""
from arb.smc import BULL, BEAR, RANGE


def _parts(c):
    o, h, l, cl = c[1], c[2], c[3], c[4]
    body = abs(cl - o)
    rng = h - l
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    return o, h, l, cl, body, rng, upper, lower


def engulfing(candles):
    """The last bar's body swallows the one before it, in the other direction.

    Two conditions, both required: the bodies must actually engulf, and the
    previous bar must have been the opposite colour. A big green bar after
    another big green bar is continuation, not a reversal signal.
    """
    if len(candles) < 2:
        return RANGE
    po, _, _, pc, pbody, _, _, _ = _parts(candles[-2])
    o, _, _, c, body, _, _, _ = _parts(candles[-1])
    if pbody <= 0 or body <= pbody:
        return RANGE
    if c > o and pc < po and c >= po and o <= pc:
        return BULL
    if c < o and pc > po and c <= po and o >= pc:
        return BEAR
    return RANGE


def rejection(candles, wick_ratio=2.0, body_frac=0.35):
    """A long wick with a small body: price went there and was refused.

    `wick_ratio` is how many times the body the rejecting wick must be, and
    `body_frac` caps the body as a share of the whole range. Both are needed -
    a long wick on a long body is a wide bar, not a rejection.
    """
    if not candles:
        return RANGE
    _, _, _, _, body, rng, upper, lower = _parts(candles[-1])
    if rng <= 0 or body > rng * body_frac:
        return RANGE
    ref = max(body, rng * 0.05)
    if lower >= ref * wick_ratio and lower > upper:
        return BULL
    if upper >= ref * wick_ratio and upper > lower:
        return BEAR
    return RANGE


def momentum_bar(candles, min_body_frac=0.65):
    """A bar that closed at its extreme with most of its range as body.

    The impulse leg itself, in one bar. Used to confirm that a break of
    structure was driven rather than drifted into.
    """
    if not candles:
        return RANGE
    o, _, _, c, body, rng, _, _ = _parts(candles[-1])
    if rng <= 0 or body < rng * min_body_frac:
        return RANGE
    return BULL if c > o else BEAR


def reaction(candles):
    """The one call the engine makes: did this bar react, and which way?

    Returns (side, name). Checks the patterns in order of how much they say -
    an engulfing bar is a stronger statement than a wick, which is a stronger
    statement than a body.
    """
    for fn, name in ((engulfing, "engulfing"),
                     (rejection, "rejection_wick"),
                     (momentum_bar, "momentum_bar")):
        side = fn(candles)
        if side != RANGE:
            return side, name
    return RANGE, ""
