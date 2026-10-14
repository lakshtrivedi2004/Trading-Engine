"""Volume, and the only two things worth asking it.

Volume is the one input here that is a fact rather than an interpretation: a
swing high is a reading, a traded contract is a receipt. That is why it is the
only non-price input this system uses, and why nothing was added alongside it.

Getting it was the problem. An index has no volume - NIFTY is an average, not an
instrument - so Angel's index candles carry none and our live bars were built
with a literal 0.0. What DOES trade is the front-month future on that index, and
the feed has been subscribing to it and recording `volume_trade_for_the_day`
into the book all along (arb/feed.py, arb/book.py). Differencing that cumulative
counter across candle buckets gives a genuine per-bar volume series for the
index's own most liquid proxy. That is what fills the field now.

Two questions, both about the same bar:

  relative volume  is this bar's participation unusual for this session? An
                   impulse on average volume is one trader; the same move on
                   three times the volume is the thing the method is named after.

  VWAP             what price did the day's business actually get done at?
                   Berkowitz, Logue and Noser (1988) introduced it as the
                   benchmark institutions are measured against, which is what
                   makes it a level they defend rather than a line on a chart.

Both degrade honestly: if the volume series is absent or all zeros - a dead
feed, a holiday, a cold start - they return None, and the caller must treat
None as "no opinion" rather than as a failed test.
"""


def _vols(candles):
    return [(c[5] if len(c) > 5 else 0.0) for c in candles]


def has_volume(candles, need=10):
    """Is there a real volume series here, or a column of zeros?"""
    v = _vols(candles)
    return sum(1 for x in v[-need * 3:] if x > 0) >= need


def relative_volume(candles, period=20):
    """The last bar's volume in standard deviations above its own recent mean.

    Returns None when there is no usable series. A z-score, not a ratio, because
    volume's spread changes through the session and a flat "twice the average"
    threshold fires constantly at the open and never at lunch.
    """
    if not has_volume(candles):
        return None
    v = _vols(candles)
    if len(v) < period + 1:
        return None
    window = v[-(period + 1):-1]          # prior bars only, never the bar itself
    window = [x for x in window if x > 0]
    if len(window) < max(5, period // 2):
        return None
    mean = sum(window) / len(window)
    var = sum((x - mean) ** 2 for x in window) / len(window)
    sd = var ** 0.5
    if sd <= 0:
        return None
    return (v[-1] - mean) / sd


def vwap(candles):
    """Session volume-weighted average price, or None without volume.

    Typical price (h+l+c)/3 weighted by bar volume, which is the standard
    construction and the one the benchmark is computed against.
    """
    if not has_volume(candles):
        return None
    # SESSION VWAP: today's bars only. The caller passes a multi-day window, and
    # a VWAP that averages in yesterday is a moving average with extra steps.
    day = str(candles[-1][0])[:10]
    today = [c for c in candles if str(c[0])[:10] == day] or candles
    num = den = 0.0
    for c in today:
        v = c[5] if len(c) > 5 else 0.0
        if v <= 0:
            continue
        num += ((c[2] + c[3] + c[4]) / 3.0) * v
        den += v
    return (num / den) if den > 0 else None


def vwap_side(candles, px):
    """+1 above VWAP, -1 below, 0 without a usable series.

    Not a signal on its own. It answers one question: is what we are about to
    buy on the side of the day's business that is winning?
    """
    w = vwap(candles)
    if w is None or px <= 0:
        return 0
    return 1 if px > w else (-1 if px < w else 0)


class VolumeTracker:
    """Turns the feed's cumulative day-volume counter into per-bar volume.

    The counter is monotonic within a session and resets at the open, so the
    per-bar figure is the forward difference. A reset or a rollover shows up as
    a negative difference; that bar is recorded as 0 rather than as a wildly
    negative number, because a wrong zero is ignorable and a wrong spike is not.
    """

    def __init__(self):
        self._last = {}

    def delta(self, name, cumulative):
        """Volume traded since the last call for this underlying."""
        cumulative = float(cumulative or 0.0)
        prev = self._last.get(name)
        self._last[name] = cumulative
        # prev <= 0 is "no quote yet", not "nothing traded yet": the first real
        # tick would otherwise book the whole day's volume into one minute (seen
        # on 24 Sep 2026: 2.3 million on the first bar), which wrecks VWAP.
        if prev is None or prev <= 0 or cumulative < prev:
            return 0.0
        return cumulative - prev
