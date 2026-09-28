"""Open intraday positions: the stop, the target, the trail, and the square-off.

This is the part the arbitrage engine never had. A four-leg box needed no exit -
its profit was fixed at entry and collected at expiry. A naked long option has
no such property: it is worth what the market says, it decays every minute it is
held, and it must be closed before the session ends. Every rupee this strategy
makes or loses is decided here, not in the scanner.

Exit precedence, highest first - the order matters and is deliberate:

  1. square-off   the close is coming; nothing is carried overnight
  2. structure    the index closed past the level that framed the trade, so the
                  reason for being in it is gone, whatever the premium says
  3. stop         premium hit the stop (or the trailed stop)
  4. target       premium hit the 1:R target
  5. time stop    held too long for too little; the option is bleeding theta
"""
import threading
import time
from dataclasses import dataclass, field

from arb.instruments import now_ist


HOLD, EXIT = "hold", "exit"


@dataclass
class Position:
    trade_id: int
    symbol: str
    token: str
    strike: float
    opt: str
    side: int                    # +1 bought a call, -1 bought a put
    qty: int
    entry: float
    stop: float
    target: float
    risk_points: float
    index_invalidation: float
    opened_at: float
    expiry: str = ""
    underlying: str = ""
    segment: str = "NFO"
    initial_stop: float = 0.0
    # Round-trip friction already charged against this trade, in premium points
    # per unit. Carried on the position because breakeven is defined against it:
    # a stop at the raw fill is not a free trade, it is a trade that loses this.
    cost_points: float = 0.0
    trailed: bool = False
    mark: float = 0.0
    mae: float = 0.0             # worst premium seen (max adverse excursion)
    mfe: float = 0.0             # best premium seen
    detail: dict = field(default_factory=dict)
    # Index-side management (arb/trailing.py). The stop that follows swings and
    # the target read off liquidity both live on the index, where structure is.
    index_entry: float = 0.0
    index_risk: float = 0.0
    index_target: float = 0.0    # 0 = no index target (premium target governs)
    index_trailed: bool = False
    delta: float = 0.0
    entry_bar: str = ""          # iso of the bar the trade was entered on
    extensions: int = 0
    last_bar: str = ""           # last completed bar the trail was updated on
    # Cash stocks: what the broker blocks for this position (notional over the
    # intraday leverage). 0 for an option, whose premium IS the capital used.
    margin: float = 0.0
    # Exit bookkeeping: unfilled attempts (escalates to market), and what has
    # already been closed if the exit fills in pieces.
    exit_attempts: int = 0
    done_qty: int = 0
    done_gross: float = 0.0
    done_cost: float = 0.0
    done_value: float = 0.0

    def __post_init__(self):
        if not self.initial_stop:
            self.initial_stop = self.stop
        self.mark = self.mark or self.entry
        self.mae = self.mae or self.entry
        self.mfe = self.mfe or self.entry

    @property
    def r_multiple(self):
        """Profit measured in units of the risk originally taken."""
        if self.risk_points <= 0:
            return 0.0
        return (self.mark - self.entry) / self.risk_points

    def pnl_points(self):
        return self.mark - self.entry

    def pnl_inr(self):
        return (self.mark - self.entry) * self.qty


class PositionBook:
    """Every open position, and the rule that decides when each one leaves."""

    def __init__(self, limits, log=lambda *_: None):
        self.limits = limits
        self.log = log
        self.open = {}                  # trade_id -> Position
        self.closed = []
        self._lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def add(self, pos):
        with self._lock:
            self.open[pos.trade_id] = pos
        return pos

    def remove(self, trade_id):
        with self._lock:
            return self.open.pop(trade_id, None)

    def count(self):
        with self._lock:
            return len(self.open)

    def deployed(self):
        """Rupees currently tied up at entry cost."""
        with self._lock:
            return sum(p.margin or p.entry * p.qty for p in self.open.values())

    def snapshot(self):
        with self._lock:
            return list(self.open.values())

    # ── the exit decision ───────────────────────────────────────────────────
    def review(self, pos, bid, index_px=None, now=None, minutes_to_close=None):
        """One position against one fresh quote. Returns (action, reason).

        The mark is the BID, never the mid: the bid is the only price we could
        actually leave at, and marking a long at the mid flatters every open
        trade by half the spread.
        """
        L = self.limits
        now = now or time.time()
        if bid and bid > 0:
            pos.mark = float(bid)
            pos.mae = min(pos.mae, pos.mark)
            pos.mfe = max(pos.mfe, pos.mark)

        if minutes_to_close is not None and minutes_to_close <= L.squareoff_minutes:
            return EXIT, "squareoff"

        # Structure invalidation. Checked before the premium stop because the
        # premium can hold up on vol while the reason for the trade is already
        # gone, and that is how a scalp turns into a bag held to expiry.
        if index_px and pos.index_invalidation > 0:
            # Once trailed, this level is the swing the trade is locked behind -
            # often already in profit - and the exit is a trail, not a failure.
            why = "trail_stop" if pos.index_trailed else "structure_broken"
            if pos.side > 0 and index_px < pos.index_invalidation:
                return EXIT, why
            if pos.side < 0 and index_px > pos.index_invalidation:
                return EXIT, why

        # Index target (arb/trailing.py:initial_target). The engine gets first
        # refusal to EXTEND it on momentum before this exits.
        if index_px and pos.index_target > 0:
            if (pos.side > 0 and index_px >= pos.index_target) or \
                    (pos.side < 0 and index_px <= pos.index_target):
                return EXIT, "target"

        if pos.mark <= pos.stop:
            return EXIT, "trailed_stop" if pos.trailed else "stop_loss"
        if pos.mark >= pos.target:
            return EXIT, "target"

        # Time stop. A scalp that has gone nowhere is a thesis that did not
        # happen, and a long option pays theta for every minute it sits. Only a
        # trade that has not earned its keep is cut; a runner is left alone.
        limit = float(getattr(L, "time_stop_min", 0.0) or 0.0)
        if limit > 0 and now - pos.opened_at >= 60.0 * limit \
                and pos.r_multiple < float(getattr(L, "time_stop_min_r", 0.3)):
            return EXIT, "time_stop"

        # Trail to breakeven once the trade has paid for its own risk. Breakeven
        # is entry plus the round trip, not entry: leaving at the fill price
        # still hands over brokerage, STT, exchange fees, GST and the spread
        # twice, so a stop parked there converts a scratch into a small loss
        # every time it is hit. Moving it any further than that would be a
        # tighter stop than the structure justifies, which is a different
        # mistake - so the level is cost-covered breakeven and nothing beyond.
        if not pos.trailed and pos.r_multiple >= L.trail_trigger_r:
            free = pos.entry + (pos.cost_points
                                if getattr(L, "breakeven_covers_costs", False)
                                else 0.0)
            # Never let the new stop land at or above the live mark: that is an
            # instant exit disguised as a trail, and at +1R we want the trade
            # left alone to run, not closed by its own protection.
            pos.stop = min(free, pos.mark) if free < pos.mark else pos.entry
            pos.trailed = True
            return HOLD, "trailed_to_breakeven"

        # After breakeven, ratchet the stop up behind the best price seen so a
        # trade that runs does not hand the whole move back.
        if pos.trailed and L.trail_step_r > 0 and \
                str(getattr(L, "trail_mode", "structure")) == "premium":
            ratchet = pos.mfe - L.trail_step_r * pos.risk_points
            if ratchet > pos.stop:
                pos.stop = ratchet
                return HOLD, "trail_advanced"

        return HOLD, ""

    def time_to_close(self, end_hhmm="15:15"):
        """Minutes until the intraday square-off deadline."""
        try:
            hh, mm = (int(x) for x in end_hhmm.split(":"))
        except Exception:
            hh, mm = 15, 15
        n = now_ist()
        close = n.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return (close - n).total_seconds() / 60.0
