"""One completed setup is a trade. Everything else grades it.

The rule this module used to enforce was "a named setup AND at least two
independent confirmations AND a decided higher timeframe AND no veto". Each of
those is defensible alone. Stacked, together with the regime gates in
arb/regime.py and the cost floor in arb/intraday.py:plan_trade, they multiplied
out to a probability of roughly zero: the engine ran for days without a single
entry. A filter that never fires cannot be tested, so it cannot be shown to
work, which makes it strictly worse than a looser one that can.

What changed, and why each change is safe:

  1. A PLAYBOOK IS THE TRADE.  A playbook (arb/playbooks.py) is already an
                 ordered sequence of agreeing facts - a sweep THEN a break of
                 structure, a break THEN a retest that held. Demanding unrelated
                 extras on top of it was counting the same agreement twice.
                 `min_confirmations` now counts the setup itself; 1 is the
                 default and means one completed playbook is enough.

  2. EXTRAS ARE A GRADE, NOT A GATE.  Order block, FVG, HTF zone, volume,
                 VWAP side and so on are still read, from different evidence as
                 before, and still reported. They decide the grade (A/B/C), and
                 the grade decides size and whether the setup may trade while
                 the engine is being careful after a losing streak.

  3. THE DAY TREND IS A PREFERENCE, NOT A PRECONDITION.  With-trend setups are
                 preferred. An undecided higher timeframe falls back to the
                 lower timeframe's own structure rather than refusing the whole
                 day, and reversal playbooks - which exist to catch turns - may
                 trade against it. Both are one config flag from the old rule.

  4. VETOES ONLY FOR THE EXTREME.  "Don't chase the top of the range" refuses
                 the top slice of the range now, not its entire upper half,
                 which had been refusing every break of structure by definition.

What was NOT loosened: the stop is still placed where the setup dies, the target
must still clear its own round-trip cost by `min_net_points`, and the daily loss
halt is untouched. Those decide whether a trade that fires is worth its
friction, and they are what keep a looser entry rule survivable.
"""
from dataclasses import dataclass, field

from arb.candlesticks import reaction
from arb.playbooks import blocked_by_level, detect
from arb.smc import BULL, BEAR, RANGE, analyse, resample
from arb.volume import relative_volume, vwap_side, has_volume

GRADES = ("A", "B", "C")


@dataclass
class Verdict:
    """The whole decision, in a form the dashboard and the journal can print."""
    side: int = RANGE
    day_trend: int = RANGE
    bias: int = RANGE                                   # what the setup was judged against
    bias_source: str = ""                               # "htf" | "ltf" | "vwap" | ""
    confirmations: list = field(default_factory=list)   # [(name, detail)]
    setups: list = field(default_factory=list)          # completed Setups, ordered
    grade: str = ""
    reversal: bool = False
    veto: str = ""
    reason: str = ""

    @property
    def count(self):
        return len(self.confirmations)

    @property
    def extras(self):
        """Confirmations that are NOT themselves a playbook."""
        return sum(1 for n, _ in self.confirmations if not n.startswith("play:"))

    @property
    def tradable(self):
        return self.side != RANGE and not self.veto

    def names(self):
        return [n for n, _ in self.confirmations]

    def plays(self):
        return [s.name for s in self.setups]

    def chain(self):
        """Every setup as its ordered step list, for the journal and dashboard."""
        return [s.describe() for s in self.setups]


def grade_at_least(grade, floor):
    """Is `grade` as good as `floor`? An empty floor or "C" accepts anything."""
    if not floor or floor not in GRADES:
        return True
    if grade not in GRADES:
        return False
    return GRADES.index(grade) <= GRADES.index(floor)


def day_trend(ltf_candles, L):
    """The higher-timeframe trend, folded from the 1-minute series.

    Built by folding the 1-minute series into higher-timeframe bars rather than
    subscribing to a second feed: five 1-minute candles ARE the 5-minute
    candle. Buckets missing a few minutes are tolerated up to `htf_min_fill`.

    Returns (side, Structure). RANGE means the higher timeframe has not decided.
    """
    htf = resample(ltf_candles, L.htf_factor,
                   min_fill=float(getattr(L, "htf_min_fill", 1.0)))
    if len(htf) < L.htf_min_bars:
        return RANGE, None
    st = analyse(htf, strength=L.htf_swing_strength, min_bars=L.htf_min_bars)
    return st.bias, st


def _bias(trend, ltf, candles, spot, L):
    """What direction setups are judged against, and where that came from."""
    if trend != RANGE:
        return trend, "htf"
    if str(getattr(L, "day_trend_mode", "fallback")) != "fallback":
        return RANGE, ""
    if ltf.bias != RANGE:
        return ltf.bias, "ltf"
    if has_volume(candles):
        s = vwap_side(candles, spot)
        if s:
            return (BULL if s > 0 else BEAR), "vwap"
    return RANGE, ""


def _extras(ltf, htf, candles, spot, side, L):
    """Independent reads agreeing with `side`. Each is drawn from different
    evidence - swing geometry, gaps, wick behaviour, traded volume, the bar's
    own shape - so two of them agreeing is genuinely two facts."""
    conf = []
    if ltf.bias == side and ltf.last_event:
        conf.append(("structure", f"{ltf.last_event} on the lower timeframe"))

    zone = ltf.active_zone(spot, side)
    if zone is not None:
        conf.append(("order_block", f"{zone.kind} {zone.low:.1f}-{zone.high:.1f}"))

    gap = ltf.active_fvg(spot, side)
    if gap is not None:
        conf.append(("fair_value_gap", f"unfilled {gap.low:.1f}-{gap.high:.1f}"))

    if ltf.sweep == side and ltf.sweep != RANGE:
        conf.append(("liquidity_sweep",
                     f"level {ltf.sweep_level:.1f} taken and reclaimed"))

    # The higher timeframe's own trend is NOT an extra: a with-trend setup was
    # only looked for because of it, so counting it again grades every such
    # setup up by one for free. Its zones are separate evidence and do count.
    if htf is not None:
        hz = htf.active_zone(spot, side)
        if hz is not None:
            conf.append(("htf_order_block",
                         f"{hz.kind} {hz.low:.1f}-{hz.high:.1f} on the higher timeframe"))

    if has_volume(candles):
        rv = relative_volume(candles, period=L.volume_period)
        if rv is not None and rv >= L.min_volume_z:
            conf.append(("volume", f"{rv:+.1f} sigma participation"))
        s = vwap_side(candles, spot)
        if s and ((s > 0 and side == BULL) or (s < 0 and side == BEAR)):
            conf.append(("vwap", "on the right side of the session VWAP"))

    # Candlesticks confirm a level; they never nominate one.
    if zone is not None or gap is not None:
        cs, name = reaction(candles)
        if cs == side and name:
            conf.append(("candlestick", f"{name} at the zone"))
    return conf


def _grade(extras, L):
    if extras >= int(getattr(L, "grade_a_extras", 3)):
        return "A"
    if extras >= int(getattr(L, "grade_b_extras", 1)):
        return "B"
    return "C"


def evaluate(ltf_candles, spot, L, strike_step=0.0):
    """Find the setup, pick its side, apply the few vetoes left, grade it.

    `strike_step` is the exchange's own strike interval for this instrument. It
    floors the psychological-level grid (arb/levels.py).
    """
    v = Verdict()

    if not ltf_candles or len(ltf_candles) < L.min_structure_bars:
        v.reason = "not enough bars"
        return v

    trend, htf = day_trend(ltf_candles, L)
    v.day_trend = trend
    ltf = analyse(ltf_candles, strength=L.swing_strength,
                  min_bars=L.min_structure_bars)

    bias, src = _bias(trend, ltf, ltf_candles, spot, L)
    v.bias, v.bias_source = bias, src

    # ── 1. setups ───────────────────────────────────────────────────────────
    # With the bias first. Then, if allowed, reversal playbooks against it -
    # those exist to catch the turn, and a turn is counter-trend by definition.
    # With no bias at all, a setup is allowed to name its own direction, but
    # two setups pointing opposite ways on one bar is a coin toss, not a trade.
    reversal_names = tuple(getattr(L, "reversal_playbooks", ()) or ())
    allow_rev = bool(getattr(L, "allow_reversals", True))
    need_playbook = bool(getattr(L, "require_playbook", True))

    if bias != RANGE:
        with_bias = detect(ltf_candles, ltf, spot, bias, L, strike_step)
        against = (detect(ltf_candles, ltf, spot, -bias, L, strike_step,
                          only=reversal_names)
                   if allow_rev and reversal_names else [])
        if with_bias:
            side, setups = bias, with_bias
        elif against:
            side, setups, v.reversal = -bias, against, True
        else:
            side, setups = bias, []
    else:
        ups = detect(ltf_candles, ltf, spot, BULL, L, strike_step)
        dns = detect(ltf_candles, ltf, spot, BEAR, L, strike_step)
        if ups and dns:
            v.reason = "setups on both sides - no clear direction"
            return v
        if not ups and not dns:
            v.reason = ("no direction yet (no recent break of structure, no "
                        "decided higher timeframe) and no setup on either side")
            return v
        side, setups = (BULL, ups) if ups else (BEAR, dns)

    v.setups = setups
    if need_playbook and not setups:
        conf = _extras(ltf, htf, ltf_candles, spot, side, L)
        v.confirmations = conf
        v.reason = ("no completed setup"
                    + (f" ({len(conf)} loose reads: "
                       f"{', '.join(n for n, _ in conf)})" if conf else ""))
        return v

    # ── 2. vetoes, for the side actually chosen ─────────────────────────────
    # The lower timeframe fighting a breakout is a real warning. It is NOT a
    # warning on a retest setup - a retest is a pullback, and on the lower
    # timeframe a pullback looks exactly like a counter move. That veto used to
    # refuse the very entry the retest playbooks describe.
    has_zone = any(s.zone for s in setups)
    if (bool(getattr(L, "ltf_must_agree", True)) and not has_zone
            and ltf.bias != RANGE and ltf.bias != side):
        v.veto = "counter_trend"
        v.reason = "lower timeframe is fighting the setup"
        return v

    # Don't chase the very top (long) or bottom (short) of the dealing range.
    # Breakout playbooks are exempt: a break of the balance high IS the top of
    # the range, so this veto would refuse them every time. They carry their
    # own anti-chase rules instead (trigger freshness, max extension in ATR).
    breakout = set(getattr(L, "breakout_playbooks", ()) or ())
    all_breakouts = bool(setups) and all(s.name in breakout for s in setups)
    if (L.require_discount_entry and not all_breakouts
            and ltf.range_high > ltf.range_low):
        frac = (spot - ltf.range_low) / (ltf.range_high - ltf.range_low)
        edge = float(getattr(L, "premium_veto_frac", 0.15))
        if side == BULL and frac > 1.0 - edge:
            v.veto = "premium_not_discount"
            v.reason = "long at the very top of the range"
            return v
        if side == BEAR and frac < edge:
            v.veto = "discount_not_premium"
            v.reason = "short at the very bottom of the range"
            return v

    wall = blocked_by_level(spot, side, ltf_candles, L, strike_step)
    if wall:
        v.veto = wall
        v.reason = "no room to the next major level before the target"
        return v

    # ── 3. confirmations and grade ──────────────────────────────────────────
    # An extra that restates a step of the setup is the same fact twice: a
    # choch_bos already IS a lower-timeframe break of structure. Those are
    # dropped so the grade counts only evidence the setup did not already use.
    used = {step for s in setups for step, _ in s.steps}
    restates = {"structure": {"break_of_structure", "change_of_character"},
                "liquidity_sweep": {"liquidity_sweep"},
                "order_block": {"order_block"},
                "fair_value_gap": {"fair_value_gap"},
                "vwap": {"vwap_side"}}
    conf = [(f"play:{s.name}", s.describe()) for s in setups]
    conf += [(n, d) for n, d in _extras(ltf, htf, ltf_candles, spot, side, L)
             if not (restates.get(n, set()) & used)]
    v.confirmations = conf
    # Two different playbooks agreeing is worth an extra of its own.
    extras = v.extras + max(0, len(setups) - 1)
    v.grade = _grade(extras, L)

    floor = str(getattr(L, "min_grade", "C") or "C")
    if not grade_at_least(v.grade, floor):
        v.reason = f"grade {v.grade} below the {floor} floor"
        return v

    need = int(L.min_confirmations)
    if len(conf) < need:
        v.reason = (f"{len(conf)}/{need} confirmations"
                    + (f" ({', '.join(n for n, _ in conf)})" if conf else ""))
        return v

    v.side = side
    plays = ", ".join(s.describe() for s in setups) or "none"
    tag = " reversal" if v.reversal else ""
    v.reason = f"grade {v.grade}{tag}, {len(conf)} reads [{plays}]"
    return v
