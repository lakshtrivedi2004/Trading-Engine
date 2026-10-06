"""Configuration: credentials, cost model, risk limits, engine tuning."""
import json
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env_file(path):
    """Parse a .env file leniently (the project's file has stray JSON/comments in it)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("{"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key.isidentifier() and val:
                out[key] = val
    return out


@dataclass
class Credentials:
    api_key: str = ""
    api_secret: str = ""
    client_id: str = ""
    pin: str = ""
    totp_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @classmethod
    def load(cls, env_path=None):
        env_path = env_path or os.path.join(ROOT, ".angel.env")
        e = _load_env_file(env_path)
        e.update({k: v for k, v in os.environ.items() if k.startswith(("ANGEL", "ANGLE", "TELEGRAM"))})

        def pick(*names):
            for n in names:
                if e.get(n):
                    return e[n]
            return ""

        return cls(
            api_key=pick("ANGEL_API_KEY", "ANGLE_ONE_API"),
            api_secret=pick("ANGEL_API_SECRET", "ANGLE_ONE_API_SECRET"),
            client_id=pick("ANGEL_CLIENT_ID", "ANGLE_ONE_ID"),
            pin=pick("ANGEL_PIN", "ANGEL_PASSWORD", "ANGLE_ONE_PIN"),
            totp_secret=pick("ANGEL_TOTP_SECRET", "ANGLE_ONE_TOTP"),
            telegram_bot_token=pick("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=pick("TELEGRAM_CHAT_ID"),
        )

    def missing(self):
        req = {"api_key": self.api_key, "client_id": self.client_id,
               "pin": self.pin, "totp_secret": self.totp_secret}
        return [k for k, v in req.items() if not v]


@dataclass
class CostModel:
    """Indian F&O statutory costs. Every rate is a fraction of turnover unless noted.

    Defaults follow .agents/AGENTS.md. They are deliberately on the conservative
    (expensive) side: an overstated cost can only make us skip a trade, while an
    understated one manufactures edge that is not there.
    """
    brokerage_per_order: float = 20.0
    opt_stt_sell: float = 0.00125
    opt_exchange_txn: float = 0.0005
    opt_stamp_buy: float = 0.00003
    fut_stt_sell: float = 0.0002
    fut_exchange_txn: float = 0.000019
    fut_stamp_buy: float = 0.00002
    sebi_fee: float = 0.000001
    gst_rate: float = 0.18
    ipft: float = 0.000005
    # Cash equity, intraday (MIS). STT 0.025% on the sell side only, stamp
    # 0.003% on the buy side, NSE cash transaction charge 0.00307%. Angel's
    # intraday brokerage since 17 Nov 2025 is the lower of Rs 20 and 0.1% of the
    # order, with a Rs 5 minimum (research/PAPERS.md 0i).
    eq_stt_sell: float = 0.00025
    eq_exchange_txn: float = 0.0000307
    eq_stamp_buy: float = 0.00003
    eq_brokerage_frac: float = 0.001
    eq_brokerage_min: float = 5.0
    # Execution friction, in price points per leg, charged on top of the quoted
    # price we cross. Calibrated from live fills by research.py.
    slippage_ticks_per_leg: float = 1.0
    tick_size: float = 0.05

    @classmethod
    def load(cls, path=None):
        path = path or os.path.join(ROOT, "config", "costs.json")
        if os.path.exists(path):
            with open(path) as fh:
                return cls(**json.load(fh))
        return cls()

    def save(self, path=None):
        path = path or os.path.join(ROOT, "config", "costs.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.__dict__, fh, indent=2)

    def slippage_points(self):
        return self.slippage_ticks_per_leg * self.tick_size


@dataclass
class RiskLimits:
    # The trading book. Capital deployed per structure is derived from its own
    # payoff (see arb/capital.py), never assumed.
    total_capital: float = 10_000.0           # Rs 10 thousand
    # The trading band, in percent PER YEAR of contract value: the structure's
    # net edge, expressed as an annualised excess yield on the index notional it
    # controls (S x lot). For a box or parity trade this is exactly how far the
    # market's implied financing rate sits from fair - the number the rulebook
    # calls "implied carry vs repo". A per-trade 2-5% on margin is unreachable
    # on liquid index options (it needs a 26-point edge on a weekly box, at the
    # plausibility cap); 2-5% per year is a real dislocation and still rare.
    # The upper bound is a sanity cap, not a preference: a deviation above it is
    # a bad print or a frozen quote far more often than free money.
    min_excess_return_pa: float = 2.0
    max_excess_return_pa: float = 5.0
    # Annualising a two-hour tenor turns a tick into a triple-digit yield, so the
    # tenor used for the band is floored here: near expiry the edge must be worth
    # at least what a full day of carry at the band would pay.
    min_tenor_days: float = 1.0
    # The stake: the same Rs 6,000 of broker margin on every trade, and never
    # more. That is the whole deployable book (total_capital x
    # max_capital_utilisation), so the stake and the utilisation guard agree
    # rather than contradict each other. Lots are the most whole lots whose
    # margin fits inside it, then capped by the depth actually displayed at the
    # touch - never by what we wish were there. A structure whose single lot
    # needs more than the stake is not traded at all. Each day's profit is also
    # reported as a return on this stake, since it is the amount actually put at
    # work.
    #
    # NOTE at Rs 10,000 of capital this stake admits no index structure at all:
    # one lot of a NIFTY box blocks ~Rs 84,000 of margin, so _size() computes
    # zero lots and every opportunity is rejected as too big for the stake.
    stake_per_trade: float = 6_000.0
    max_lots_per_trade: int = 12
    max_open_structures: int = 3
    # Coarse sanity guard only; capital utilisation is the real control. A
    # single futures leg on NIFTY already carries ~Rs 15 lakh of notional.
    max_gross_notional: float = 8_000_000.0
    max_daily_loss: float = 25_000.0
    max_trades_per_day: int = 40
    trade_cooldown_sec: float = 2.0
    # A dislocation that is still on the screen after we traded it is usually the
    # same one, not a second opportunity. Re-arming on the identical set of legs
    # is how a single stale quote turns into a run of duplicate trades.
    structure_cooldown_sec: float = 45.0
    # Several distinct structures can all lean on one dislocated contract. Each
    # is genuinely an arbitrage if the quote is real - and all of them lose
    # together if it was a bad print. Trade one and stand down on that contract.
    instrument_cooldown_sec: float = 20.0
    # A leg is only tradable if the displayed size covers this multiple of ours.
    min_depth_multiple: float = 1.0
    # Every leg of a structure must have been quoted within this window, else the
    # structure is stale and the "edge" is an artifact of asynchronous prices.
    max_quote_age_ms: float = 400.0
    # Reject absurd edges outright: on liquid index options an edge this large is
    # far more likely to be a bad print or a frozen quote than free money.
    max_plausible_edge_points: float = 25.0
    # Absolute floor, secondary to the return band above. A structure tying up
    # Rs 3,250 hits 2% at Rs 65, so a Rs 150 floor would silently raise the real
    # minimum to 4.6% and make the configured band a fiction.
    require_positive_net_inr: float = 50.0
    # Never deploy more than this share of the book at once.
    max_capital_utilisation: float = 0.60
    # "Invested" means what Angel One actually blocks, taken from its own margin
    # calculator (SPAN + exposure, net of hedge benefit) - not the structure's
    # theoretical settlement obligation. A one-lot NIFTY box that owes Rs 13,000
    # at expiry has Rs 83,656 of margin blocked against it (measured 11 Sep 2026).
    # The scanner uses a per-chain table calibrated from the API so there is no
    # HTTP call in the detection path; the exact figure for the legs actually
    # traded is fetched right after the fill and recorded on the trade.
    margin_product: str = "CARRYFORWARD"      # NRML: structures are held to expiry
    margin_refresh_sec: float = 600.0         # re-calibrate the per-chain table
    margin_call_spacing_sec: float = 1.5      # the margin endpoint is rate limited
    # Until the table is calibrated (or if the API is down), assume this share of
    # notional per short option unit on a hedged structure. Angel's figure for a
    # fully hedged NIFTY box came to ~2.75% per short unit; 3% is on the safe side.
    margin_fallback_pct_per_short_unit: float = 0.03


@dataclass
class Underlying:
    """One index and where its contracts live.

    The three differ in more than name: SENSEX options trade on BSE's BFO segment
    (websocket exchange type 4) while the NSE indices use NFO (type 2), and lot
    size and strike step differ per index. Nothing may assume NIFTY's values.
    """
    name: str
    spot_token: str
    spot_exchange: str = "NSE"          # for the REST LTP call
    spot_exchange_type: int = 1         # websocket: 1 NSE_CM, 3 BSE_CM
    segment: str = "NFO"                # scrip master exch_seg, and the order exchange
    option_exchange_type: int = 2       # websocket: 2 NSE_FO, 4 BSE_FO
    num_expiries: int = 3
    strikes_each_side: int = 10
    enabled: bool = True


DEFAULT_UNDERLYINGS = [
    # NIFTY: weekly Tuesday expiries, lot 65, 50-point strikes. Only its monthly
    # carries a future, so put-call parity is limited to that expiry.
    dict(name="NIFTY", spot_token="99926000", spot_exchange="NSE",
         spot_exchange_type=1, segment="NFO", option_exchange_type=2),
    # BANKNIFTY: monthly only since weeklies were withdrawn, lot 30, 100-point
    # strikes. Every option expiry has a matching future, so parity works on all.
    dict(name="BANKNIFTY", spot_token="99926009", spot_exchange="NSE",
         spot_exchange_type=1, segment="NFO", option_exchange_type=2,
         num_expiries=2),
    # SENSEX: BSE, weekly Thursday expiries, lot 20, 100-point strikes.
    dict(name="SENSEX", spot_token="99919000", spot_exchange="BSE",
         spot_exchange_type=3, segment="BFO", option_exchange_type=4),
    # FINNIFTY: monthly only (weeklies withdrawn same as BANKNIFTY), lot 60.
    dict(name="FINNIFTY", spot_token="99926037", spot_exchange="NSE",
         spot_exchange_type=1, segment="NFO", option_exchange_type=2,
         num_expiries=2),
    # MIDCPNIFTY: monthly only, lot 120, the most volatile of the five.
    dict(name="MIDCPNIFTY", spot_token="99926074", spot_exchange="NSE",
         spot_exchange_type=1, segment="NFO", option_exchange_type=2,
         num_expiries=2),
]


@dataclass
class EngineConfig:
    underlyings: list = field(default_factory=lambda:
                              [dict(u) for u in DEFAULT_UNDERLYINGS])
    num_strikes_each_side: int = 10
    # Skip contracts in their final minutes. The arithmetic still holds at expiry,
    # but a partial fill there cannot be unwound before settlement, so the
    # "risk-free" part stops being true exactly when it matters.
    min_hours_to_expiry: float = 1.0
    risk_free_rate: float = 0.065
    # Use the market's own cross-sectional implied box rate instead of a fixed
    # repo assumption; falls back to risk_free_rate until enough boxes are seen.
    use_implied_rate: bool = True
    # Plausible band for a market-implied financing rate. A reading outside this
    # is treated as a broken measurement, not a discovery, and the engine falls
    # back to risk_free_rate.
    implied_rate_floor: float = 0.02
    implied_rate_cap: float = 0.15
    scan_interval_ms: float = 0.0
    trading_mode: str = "paper"   # paper | live
    strategies: list = field(default_factory=lambda: [
        "vertical", "box", "butterfly", "pcp_futures",
    ])
    record_ticks: bool = True
    session_start: str = "09:15"
    session_end: str = "15:15"

    @classmethod
    def load(cls, path=None):
        path = path or os.path.join(ROOT, "config", "engine.json")
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in data.items() if k in known})
        return cls()

    def active_underlyings(self):
        out = []
        for u in self.underlyings:
            d = dict(u)
            if not d.pop("enabled", True):
                continue
            d.setdefault("strikes_each_side", self.num_strikes_each_side)
            out.append(Underlying(**d))
        return out

    def save(self, path=None):
        path = path or os.path.join(ROOT, "config", "engine.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.__dict__, fh, indent=2)


@dataclass
class IntradayLimits:
    """Limits for the directional intraday option-buying strategy.

    None of the arbitrage band lives here. `min_excess_return_pa` /
    `max_excess_return_pa` in RiskLimits asked whether a risk-free structure was
    priced 2-5% off fair per year; a naked long option has no fair price to be
    off by, so that test is meaningless for this strategy and is not applied.
    What replaces it is an explicit stop, a reward-to-risk floor, and a cap on
    how much of the purse one trade may lose.
    """
    # The purse. Grows: the day's realised P&L is folded back in at the close
    # (arb/capital_book.py), so sizing compounds.
    starting_capital: float = 10_000.0
    # The day's goal, as a percent of capital at the open. A GOAL - never a gate.
    # Reaching it does not stop trading, because a day that runs further is a
    # better day, and halting at the target would cap exactly the sessions that
    # pay for the losing ones.
    daily_target_pct: float = 10.0

    # ── what may be bought ──────────────────────────────────────────────────
    # Only strikes this far either side of the money. Beyond it the option is a
    # lottery ticket: cheap because it will most likely expire worthless.
    max_strike_offset: int = 5
    # Delta floor, measured off the chain (dPremium/dStrike), not a model. Below
    # this the contract barely responds to the index and the structure read that
    # justified the trade cannot express itself in the premium.
    min_delta: float = 0.15
    # A spread wider than this is a loss taken at entry: we pay the ask and can
    # only leave at the bid.
    max_spread_points: float = 3.0
    # Option BUYING only, for now. The sell side is written and wired throughout
    # (execution, costs, margin all already handle SELL legs - the arbitrage
    # engine used them); it is refused here and only here, so enabling short
    # premium later is this one flag plus a margin check.
    allow_selling: bool = False

    # ── the trade ───────────────────────────────────────────────────────────
    # Minimum 1:2. The stop is placed where structure says the idea is wrong,
    # and the target is this multiple of that distance - never the other way
    # round, which would be picking a target and hoping.
    reward_risk: float = 2.0
    # At +1R, the stop goes to entry: the trade is then free.
    trail_trigger_r: float = 1.0
    # ...but "entry" is not free. Getting out costs what getting in cost, so a
    # stop parked exactly at the fill still books a loss of the full round trip.
    # With this on, breakeven means entry PLUS the round-trip cost already
    # charged against the trade - the first level at which the trade genuinely
    # owes nothing. Mesfin (2026) is the case for taking friction literally:
    # eleven of fourteen intraday signal families failed for no reason other
    # than a gross edge smaller than their own round-trip cost.
    breakeven_covers_costs: bool = True
    # After breakeven, ratchet the stop this far behind the best price seen.
    # Used only by trail_mode = "premium".
    trail_step_r: float = 1.0

    # ── trailing and targets on the index chart (arb/trailing.py) ──────────
    # How the stop follows once the trade reaches trail_trigger_r:
    #   "structure"  to the last opposite swing formed after entry (the most
    #                recent higher low on a long, lower high on a short), never
    #                worse than breakeven + costs - the owner's rule
    #   "atr"        chandelier: best price since entry minus k ATR
    #   "hybrid"     whichever of the two is more protective
    #   "premium"    the older ratchet on the option price (trail_step_r)
    trail_mode: str = "structure"
    trail_swing_strength: int = 2
    # Swings for the trail are read on this multiple of the 1-minute bar.
    trail_timeframe: int = 1
    trail_pad_atr: float = 0.10
    # The trail may never sit closer than this to the price, or it stops the
    # trade out on the very bar that set it.
    trail_min_gap_atr: float = 0.25
    chandelier_atr_mult: float = 3.0
    # An opposing engulfing / rejection candle or a CHoCH against the trade
    # after 1:1 tightens the trail to the nearer (strength-1) swing.
    tighten_on_reversal: bool = True
    # Target: "structure" = next liquidity level (swing, PDH/PDL, opening
    # range, major round number) between min_target_r and max_target_r, exited
    # just before it; "fixed" = reward_risk x R; "trail" = no target at all.
    # "momentum" (default, the owner's rule): the move the index has been making
    # easily - the target_percentile of its best excursion over the next
    # target_horizon_bars, across recent sessions - scaled by participation
    # (volume now against its session normal). A trade whose achievable move
    # does not pay min_target_r x its stop is skipped, not stretched.
    target_mode: str = "momentum"
    # Matched to the holding window (time_stop_min = 40): a target the market
    # typically reaches within 30 minutes is one this trade has time to reach.
    target_horizon_bars: int = 30
    target_percentile: float = 50.0
    target_lookback_bars: int = 750
    target_volume_recent_bars: int = 5
    target_volume_power: float = 0.5
    target_volume_min: float = 0.75
    target_volume_max: float = 1.5
    # "Easily achievable" beats "big": 1:1 minimum, and the swing trail plus
    # target extension let the runners run past it.
    min_target_r: float = 1.0
    max_target_r: float = 4.0
    target_front_run_atr: float = 0.10
    # At the target: "extend" if the last bar is still an impulse in the
    # trade's direction (move to the next level, lock the stop within
    # extend_giveback_r of the old target), otherwise take it. "exit" always takes it.
    on_target: str = "extend"
    # Owner's trailing rule (1 Oct 2026): trail to each new higher low (lower
    # high on a short) from the first one after entry, not only after +1R.
    trail_from_first_swing: bool = False
    # Never set a target closer than this many R; 0 leaves the momentum target
    # as it is. Distinct from min_target_r, which SKIPS a trade instead.
    target_floor_r: float = 0.0
    max_target_extensions: int = 2
    extend_min_r: float = 1.0
    extend_giveback_r: float = 0.75
    extend_body_atr: float = 0.5
    extend_close_pos: float = 0.3
    # A stop may never be further than this share of the premium paid. A deep
    # structural stop on a cheap option would otherwise risk the whole ticket.
    max_risk_frac_of_premium: float = 0.50
    # The move must be worth making after BOTH sides of the cost are paid. This
    # is the "10-15 points a trade" floor, enforced net rather than gross.
    min_net_points: float = 10.0
    # The round-trip cost may be at most this share of the premium risked.
    # 0.10 keeps only trades whose stop is wide enough that friction is a
    # small part of the bet. 0 disables.
    # 0.20, not 0.10: with stops capped at 5% of capital (~Rs 490 on Rs 10k),
    # a Rs 68-84 round trip is 14-17% of the risk on every index, so 0.10
    # would refuse every trade. The research band is 10-20%.
    max_cost_frac_of_risk: float = 0.20

    # ── risk ────────────────────────────────────────────────────────────────
    # Most of the purse one trade may lose, as a percent of the day's opening
    # capital. The owner asked for 5% and then for "whatever increases the win
    # rate". On 7 real sessions (research/PAPERS.md 0d) the win rate was 51% at
    # 5%, 54% at 8%, and no higher at 10-25%; below 5% it collapsed (41% at
    # 4%). 8 is the smallest cap that reaches the best win rate. On the 3
    # held-out days every cap from 5 to 25 scored the same 48%.
    max_risk_pct_per_trade: float = 8.0
    # How the cap is met WITHOUT moving the chart's stop:
    #   "delta"  buy a lower-delta strike, so the same index stop costs fewer
    #            rupees (risk = index stop x delta x lot). Below min_delta the
    #            trade is skipped. The owner's choice (24 Sep 2026).
    #   "skip"   take only setups whose stop fits at the usual strike
    #   "cap"    tighten the stop to fit - NOT recommended, see plan_trade
    stop_policy: str = "delta"
    # ...but never tighter than this many index ATRs: inside one bar's noise a
    # stop is hit by the spread's own wobble. 0 disables.
    min_stop_atr: float = 1.0
    # The day stops here. Not a target - a hard halt.
    max_daily_loss_pct: float = 15.0
    max_open_positions: int = 1
    max_trades_per_day: int = 15
    # Live exits: an exit is a limit IOC at the touch; after this many unfilled
    # attempts it goes at MARKET, because a stop that cannot fill is no stop.
    exit_market_after: int = 3
    trade_cooldown_sec: float = 60.0
    # Do not re-enter the same strike straight after being stopped out of it.
    symbol_cooldown_sec: float = 300.0

    # ── discipline: the trading psychology, written as rules ────────────────
    # Every discretionary scalping course teaches the same three things and
    # every discretionary trader breaks them on a bad day. A bot can't break
    # them, which is most of the edge a bot has over its author.
    #
    # No revenge trade. After a losing exit, wait this long before the next
    # entry, on any instrument - the global cooldown is for ordinary spacing.
    cooldown_after_loss_sec: float = 180.0
    # Losing streak. This many consecutive losses pauses new entries for
    # loss_streak_pause_min; a win resets the count. It is a pause, not a halt:
    # the day's hard stop is max_daily_loss_pct and nothing else.
    max_consecutive_losses: int = 3
    loss_streak_pause_min: float = 30.0
    # After a pause lifts, only setups at or above this grade may trade until
    # the next win ("A", "B" or "C"; "C" means any). Size down or be choosier
    # after losses - never size up to win it back.
    grade_after_streak: str = "B"
    # Lots per trade by setup grade. 1 everywhere means flat sizing. The lot
    # count is always further capped by capital and max_risk_pct_per_trade, so
    # a grade can only size up into room that the risk limit already allows.
    grade_sizing: bool = True
    lots_grade_a: int = 2
    lots_grade_b: int = 1
    lots_grade_c: int = 1
    # Time stop. A scalp that has done nothing for this long is a thesis that
    # did not happen, and a long option pays theta for every minute of it.
    # 0 disables. Only exits trades that are below +time_stop_min_r.
    # 40 rather than 20: at 20 minutes this closed ~70% of all trades before
    # the swing trail or the target could act (7 real sessions, research/
    # PAPERS.md section 0b). Option time decay is mostly overnight (Muravyev &
    # Ni 2020), so an intraday hold is not bleeding as fast as it looks.
    time_stop_min: float = 40.0
    time_stop_min_r: float = 0.3

    # ── regime (arb/regime.py) ──────────────────────────────────────────────
    # Structure says which way; regime says whether the tape is moving enough
    # for that to be worth paying friction for. Both must agree. See
    # research/PAPERS.md for what each of these is drawn from.
    use_regime_gate: bool = True
    # Current 20-bar ATR against its own trailing median, capped [0.5, 2.0].
    # Below this the expected move is too small to clear the round trip - the
    # exact failure mode behind eleven of Mesfin's fourteen dead signals.
    # The owner's "never trade sideways", read as "the index must be moving
    # harder than its own normal". On a year of 1-minute data on all three
    # indices with ob_fvg_retest (research/PAPERS.md 0g) every value from 1.4
    # to 1.8 was positive in both the in-sample and held-out months; 1.5 is
    # the middle of that plateau and the best (+Rs 10,485, 49% win, ~110
    # trades a year). 1.2 was -Rs 4,453. Few trades: this waits for real moves.
    min_atr_ratio: float = 1.5
    atr_period: int = 20
    atr_baseline_bars: int = 200
    # What to do while the baseline is still too short to judge - the first
    # half hour after a cold start with no history. "Unknown" is not "quiet",
    # and refusing it made every restart a dead half hour. The structure and
    # cost checks downstream still apply in full. False restores the refusal.
    trade_unknown_regime: bool = True
    # The middle of the session. Gao et al. (JFE 2018) locate intraday
    # predictability in the first and last half-hours; Heston et al. (JF 2010)
    # find the between-times are dominated by transient liquidity imbalance and
    # bid-ask bounce, which a buyer of premium pays for and does not collect.
    #   "strict"  trade midday only when the tape is moving harder than its own
    #             normal (atr ratio >= midday_min_atr_ratio) - trend days
    #   "block"   no midday entries at all (the old behaviour: ~4 of 6 hours off)
    #   "off"     midday is treated like any other time
    midday_mode: str = "strict"
    midday_min_atr_ratio: float = 1.0
    # Deprecated: kept so an old config file still loads. True maps to
    # midday_mode="block".
    skip_midday: bool = False
    # The LAST COMPLETED bar must close in the outer this-much of its own range.
    # 0 disables. It used to be applied to the still-forming live bar, which
    # flips every few seconds, and every playbook already demands its own
    # trigger candle - so it is off by default and available if wanted.
    min_close_position: float = 0.0

    # Never trade sideways (the owner's rule, research/PAPERS.md 0e/0f). The
    # index side is min_atr_ratio above. The efficiency-ratio test (net move /
    # path over sideways_bars, against a random walk's 1/sqrt(n)) is kept as
    # an option but off: on a year of data it lost MORE than no filter in
    # both periods. premium_rise_bars (the contract's own premium must have
    # risen past the round trip over that many minutes) is also off: it fights
    # pullback entries - a retest buys while the premium dips - and on a year
    # of data (delta x index proxy) it halved ob_fvg_retest and turned it from
    # break-even to a loss. Re-test on recorded premiums before switching on.
    # 0 disables either.
    sideways_bars: int = 0
    min_trend_efficiency: float = 1.0
    premium_rise_bars: int = 0

    # ── the premium's own movement, and the other side (arb/optionflow.py) ──
    # The owner's rules (research/PAPERS.md 0h). One index point moves an ATM
    # premium ~0.5-0.6, and on a rally a call earns less than its chain delta
    # because index IV falls as the index rises (Hull & White 2017). Recorded
    # books: calls realised ~0.74 of chain delta, puts ~0.94. Target and cost checks use the
    # contract's measured points-per-point on the last realised_delta_bars
    # minutes, shrunk toward the prior by R^2 and history length, and never
    # above the chain delta. Stops and sizing keep the chain delta: losses
    # realised in full (~1.0x) where gains did not (~0.6x over 30 minutes).
    use_realised_delta: bool = True
    realised_delta_bars: int = 30
    realised_delta_prior_ce: float = 0.8
    realised_delta_prior_pe: float = 1.0
    realised_delta_floor: float = 0.3
    # Cross-side read: the put at the same |delta| as the call being bought
    # (or the reverse) must mirror the move, and the neighbouring strikes must
    # be in line. Over cross_side_bars minutes:
    #   "off"    not read
    #   "log"    read and journalled only
    #   "grade"  (default) a confirming read counts as an extra and can raise
    #            the grade; a contradicting one is journalled, not refused
    #   "gate"   also refuse fake_move / options_against (and a vol crush
    #            with cross_side_veto_crush)
    # Not a gate by default: on two recorded sessions the options' lead had the
    # predicted sign (+0.15 to +0.24 corr with the next 10 minutes) at t ~ 1-1.5,
    # which is a hint, not evidence. Re-test with tools/crossside_replay.py.
    cross_side_mode: str = "grade"
    cross_side_bars: int = 10
    # How big the options-vs-index disagreement must be, in index ATRs (1m),
    # before it means anything, and an absolute floor in index points.
    cross_side_lean_atr: float = 0.5
    cross_side_min_points: float = 0.0
    # A matched strangle falling by this share of the move is writers crushing
    # volatility; it never confirms a long option.
    cross_side_crush: float = 0.3
    cross_side_veto_crush: bool = False
    # Slack, as a share of our strike's own change, before a strike counts as
    # out of line with its two neighbours.
    cross_side_ladder_tol: float = 0.5

    # ── confluence (arb/confluence.py) ──────────────────────────────────────
    # How many reads must agree before a trade is taken, COUNTING THE SETUP
    # ITSELF. 1 means one completed, ordered playbook is a trade on its own -
    # a playbook is already a sequence of several agreeing facts (e.g. sweep,
    # THEN break of structure), so demanding unrelated extras on top of it was
    # double-counting and, stacked with every other gate, left the system with
    # no trades at all. Extra agreeing reads are still collected and reported:
    # they set the setup GRADE (A/B/C) and, with grade_sizing, the lot count.
    min_confirmations: int = 1
    # Grade thresholds, in confirmations beyond the setup itself.
    grade_a_extras: int = 3
    grade_b_extras: int = 1
    # The lowest grade that may trade at all. "C" = any completed setup. Raise
    # to "B" to demand one independent extra read - the dial between "trades
    # often" and "trades only the cleaner setups", without re-stacking gates.
    min_grade: str = "C"
    # The day trend is read on this multiple of the base interval. 5 x 1m = 5m,
    # 12 bars = one hour of history. It was 15m x 20 bars = FIVE hours of
    # history before the engine could form an opinion, which on a restart
    # without the historical API meant no trend - and so no trade - all day.
    htf_factor: int = 5
    htf_swing_strength: int = 2
    htf_min_bars: int = 12
    # A higher-timeframe bucket is emitted once it holds this share of its
    # minutes. Live bars are built from ticks, and one quiet minute used to
    # delete the whole 15-minute bar it belonged to. The still-forming bucket
    # is never emitted whatever this says.
    htf_min_fill: float = 0.6
    # When the higher timeframe has not decided yet:
    #   "fallback"  use the lower timeframe's own structure as the bias (and
    #               VWAP side where volume exists). Most intraday scalpers read
    #               bias off 5m/15m structure plus VWAP; a day whose HTF is
    #               still undecided is not a day with no direction.
    #   "strict"    no trade (the old behaviour)
    day_trend_mode: str = "fallback"
    # Reversal playbooks (a sweep of a pool everybody watches, then a break the
    # other way) are by definition AGAINST the prevailing trend at the moment
    # they fire. Refusing every counter-trend setup refused the one model built
    # for turns. With this on, the listed playbooks may trade against the
    # higher timeframe; everything else must still go with it.
    allow_reversals: bool = True
    reversal_playbooks: tuple = ("sweep_bos", "choch_bos")
    # Buy in discount, sell in premium - never chase an uptrend at the top of
    # its own dealing range. Applied as "not in the extreme": refuse a long in
    # the top `premium_veto_frac` of the range and a short in the bottom. The
    # old rule refused the whole upper HALF, which contradicts break-of-
    # structure entries - a BOS up closes above the last high, i.e. in premium,
    # every single time - so choch_bos could essentially never fire.
    require_discount_entry: bool = True
    premium_veto_frac: float = 0.15
    # Playbooks that buy strength by design and are exempt from the veto above.
    breakout_playbooks: tuple = ("choch_bos", "ib_breakout")
    # Participation, in standard deviations above this bar's own recent mean.
    # Sourced from the front FUTURE on the index, because an index has no volume
    # of its own (arb/volume.py).
    min_volume_z: float = 1.0
    volume_period: int = 20

    # ── playbooks (arb/playbooks.py) ────────────────────────────────────────
    # A trade needs a NAMED SETUP - an ordered sequence such as "change of
    # character, then break of structure" - not merely several unrelated things
    # being true at the same moment. Setting this False restores the older
    # flat-count behaviour and is not recommended: a count is order-blind, and
    # order is most of what distinguishes a setup from a coincidence.
    require_playbook: bool = True
    # How many named setups must have completed. One is a setup; raising this
    # to 2 demands two different models agree on the same bar, which is far
    # stricter and will cut the trade count hard - choose it deliberately, and
    # remember that a smaller sample is also a weaker basis for believing any
    # of this works.
    min_setups: int = 1
    # Which playbooks are live. Empty means every registered one. Names come
    # from arb/playbooks.py:PLAYBOOKS, and an unrecognised name is reported at
    # startup rather than silently ignored.
    playbooks: tuple = ()
    # Kept for older configs; with min_confirmations = 1 it has no effect.
    setups_that_stand_alone: int = 2
    # How recent a structural event may be and still count as part of a live
    # sequence. On a 1-minute series 40 bars is roughly the last three quarters
    # of an hour - long enough for CHoCH-then-BOS to complete, short enough
    # that the first leg has not become history.
    sequence_window_bars: int = 40
    # ...but a setup whose LAST step is a break (choch_bos) is only an entry for
    # this many bars after that break. The sequence window says how far apart
    # the steps may be; this says how stale the trigger may be. 0 disables.
    trigger_fresh_bars: int = 5
    # The breaking bar's body, in ATR, for choch_bos. 0 disables.
    displacement_body_atr: float = 0.8
    # How far back a liquidity sweep may have completed. This must be WIDER
    # than the confirmation window, not narrower: in this model the sweep comes
    # first and the break of structure follows it, so by the time the setup is
    # complete the sweep is necessarily the older of the two events. Too tight
    # a value here silently makes the whole model unfireable.
    sweep_lookback_bars: int = 25
    # How far beyond the swept wick / the zone edge a structural stop sits, in
    # ATR. Zero puts the stop exactly on the level, where it will be tagged by
    # the touch it is meant to survive.
    sweep_stop_pad_atr: float = 0.05
    zone_stop_pad_atr: float = 0.10
    # The opening range: India gaps, so the first half hour is a real auction
    # and the range it prints is where the day's brackets sit. This is the NSE
    # analogue of the "Asia range" the imported London/NY models swept.
    opening_range_minutes: int = 30
    # When the session opens, for the opening-range window. Mirrors
    # Config.session_start and is overwritten from it at engine start-up, so
    # the exchange clock stays a single source of truth and this default only
    # applies to callers that construct the limits on their own.
    session_start: str = "09:15"

    # ── initial balance breakout (arb/playbooks.py:ib_breakout) ────────────
    # Umar Punjabi's opening-balance model. 0 minutes means "use
    # opening_range_minutes", so the two cannot drift apart by accident.
    ib_minutes: int = 0
    # Bars since the first close beyond the balance within which the break is
    # still "fresh". After that only the retest variant can fire.
    ib_entry_window_bars: int = 10
    # How far beyond the edge price may already be, in ATR, and still be bought.
    ib_max_extension_atr: float = 1.5
    ib_retest_tol_atr: float = 0.3
    ib_first_break_only: bool = True
    # Where the idea dies: "mid" = the balance midpoint (his "point of no
    # return", and half the distance of "far"), "far" = the opposite edge,
    # "edge" = just back inside the broken edge (tightest, most stop-outs).
    ib_stop: str = "mid"
    # No new balance breaks after this time; "" disables.
    ib_last_entry: str = "13:00"

    # ── VWAP pullback (arb/playbooks.py:vwap_pullback) ─────────────────────
    vwap_touch_atr: float = 0.25
    vwap_touch_bars: int = 5
    vwap_min_session_bars: int = 15

    # The lower timeframe pointing against a BREAKOUT setup vetoes it. Retest
    # setups are exempt - a retest is a pullback, and on the lower timeframe a
    # pullback is a counter move by definition.
    ltf_must_agree: bool = True
    # Per playbook and side, at most this many entries a day. A model that has
    # already been tried twice today in the same direction is not a new idea.
    # 0 disables.
    max_per_playbook_per_day: int = 2

    # ── fibonacci pullback ──────────────────────────────────────────────────
    # The impulse leg must be at least this many ATRs or the retracement zone
    # is narrower than the option spread, and a stop inside the spread is not
    # a stop.
    fib_min_leg_atr: float = 1.5
    fib_zone_start: float = 0.50      # the golden pocket, shallow edge
    fib_zone_end: float = 0.618       # deep edge
    fib_stop: float = 0.79            # beyond the pocket, the leg is not holding

    # ── psychological levels (arb/levels.py) ────────────────────────────────
    # "At" a round number, in ATR units - the tolerance has to scale with the
    # tape or it means different things at different times of day.
    psych_tolerance_atr: float = 0.35
    # Which round-number grid counts as psychological. The grid is DERIVED, not
    # tabulated: the spacing chosen is the one nearest this many ATRs, so the
    # same rule gives NIFTY its 50s, BANKNIFTY its 250s and a 250-rupee stock
    # its 5s, and a new instrument needs no entry anywhere. Scaled to the move
    # being traded rather than to a single bar, because a level only matters if
    # price can plausibly reach it inside the trade's horizon.
    psych_span_atr: float = 4.0
    # A level only counts once price has already respected it this many times.
    psych_min_touches: int = 2
    psych_history_bars: int = 180
    # Headroom: refuse a trade whose target has a MAJOR round number inside it.
    # Osler (2003) finds take-profit orders cluster at round numbers, which is
    # what makes price stall into one - so a 2R target with a major level at
    # 0.7R is a target that has to fight through other people's exits.
    use_level_headroom: bool = True
    min_headroom_atr: float = 0.75

    # ── structure ───────────────────────────────────────────────────────────
    candle_interval: str = "1m"
    swing_strength: int = 2
    min_structure_bars: int = 25
    # Only trade a retracement into a zone, not the breakout candle itself.
    # Any completed playbook satisfies this: a setup is its own reason and
    # nominates its own stop. It only bites when require_playbook is off.
    require_order_block: bool = True

    # ── session ─────────────────────────────────────────────────────────────
    # Nothing is carried overnight: every position is closed this many minutes
    # before session_end, whatever it is worth.
    squareoff_minutes: float = 1.0
    # No new position inside this window - not enough time left to reach target.
    no_new_trades_minutes: float = 15.0
    # Let the open settle before reading structure off it.
    warmup_minutes: float = 15.0
    # How long an index may go without usable candles before the engine says so
    # rather than quietly refusing every bar. A dead feed and a calm market look
    # identical from inside the gates; only the clock distinguishes them.
    candle_starvation_warn_sec: float = 120.0

    # ── stocks in play (arb/scanner.py, research/PAPERS.md 0i) ──────────────
    # Cash shares, intraday (MIS), both directions. Not stock options: one lot
    # of a stock option or future controls Rs 3-17 lakh, options are monthly
    # only and settle by physical delivery, so at this purse the cash market is
    # the only way to trade a stock at all. Off until tools/stock_backtest.py
    # shows the scanner's picks beating random picks after costs, in both
    # periods.
    stocks_enabled: bool = False
    stocks_allow_short: bool = True
    # SEBI peak-margin rules: at least 20% upfront, so 5x is the ceiling. The
    # broker may give less on a given stock, and a live order above its limit
    # is refused, not resized.
    stocks_leverage: float = 5.0
    # Per-trade risk cap for a STOCK trade, % of capital, costs inside. 0 = the
    # same cap as the indices (max_risk_pct_per_trade). Owner, 6 Oct 2026:
    # "7% or below, the same as the index" - never above
    # STOCKS_RISK_CEILING_PCT whatever the file says.
    stocks_max_risk_pct: float = 0.0
    # The scan: how many names to trade, when to pick them, how often to re-pick.
    stocks_top_k: int = 5
    stocks_pick_at: str = "09:30"
    stocks_rescan_min: float = 30.0
    # Per-minute spike watch over every subscribed stock (arb/stockspike.py,
    # owner 6 Oct 2026): "on" adds a spiking stock to the picks for
    # stocks_spike_hold_min (traded by the same gates, stops and risk cap as
    # any pick), "shadow" only logs it, anything else is off. Spike = mean true
    # range of the last stocks_spike_fast closed 1-minute bars over the median
    # of up to stocks_spike_base_bars before them.
    stocks_spike_watch: str = "off"
    stocks_spike_ratio: float = 5.0          # 2.5 fired 1,000-2,500 times a day on real bars; 5 fires 11-34
    stocks_spike_fast: int = 5
    stocks_spike_base_bars: int = 120
    stocks_spike_k: int = 3
    stocks_spike_hold_min: float = 15.0
    # NSE's live top gainers/losers (F&O), refreshed this often (minutes; 0 = off).
    stocks_movers_min: float = 5.0
    # The stock's own option (CE long / PE short) priced off NSE's chain for
    # every stock setup: "shadow" logs and journals what it would buy, or why
    # it cannot (one lot above capital, loss at the stop above the risk cap).
    # Never sends an order; anything else is off.
    stocks_options: str = "off"
    # Universe filters. Liquidity is what makes a scalp possible at all: a
    # stock that trades Rs 50 crore a day has a one-tick spread on a Rs 50k
    # order; a thin one does not.
    stocks_min_price: float = 100.0
    stocks_min_turnover_cr: float = 50.0
    stocks_min_atr_pct: float = 1.5
    # Relative volume floor (1.0 = a normal day by this time). Zarattini,
    # Barbon & Aziz: per-trade R was -0.02 below 1.0 and +0.08 above it.
    stocks_min_rvol: float = 1.0
    # Score weights on cross-sectional ranks: ATR%, relative volume, move from
    # the previous close in ATRs, range so far in ATRs.
    stocks_w_atr: float = 1.0
    stocks_w_rvol: float = 1.0
    stocks_w_move: float = 0.5
    stocks_w_range: float = 0.5
    # Relative volume is scored over this many recent minutes, not since the
    # open, so a morning spike that has gone quiet rotates out at the next
    # rescan. Falls back to since-open until the desk has this much history.
    # 0 = since the open only.
    stocks_rvol_window_min: float = 15.0
    # Tradability. The scan prices each stock's round trip at a stop of
    # stocks_scan_stop_atr one-minute ATRs against the entry gate's
    # max_cost_frac_of_risk (arb/scanner.py:scan_cost_ratio). Above
    # stocks_max_cost_ratio the name is dropped - costs alone would refuse it -
    # and below it cheaper names score higher (weight stocks_w_cost).
    # 2.0 ATRs is a judgment call for a typical structural stop, not a fitted
    # value; watch stock:cost_too_large_for_stop in the rejects to tune it.
    stocks_scan_stop_atr: float = 2.0
    stocks_max_cost_ratio: float = 1.0
    stocks_w_cost: float = 1.0
    # A stock trade must clear its round trip by at least this many rupees at
    # the target (the equity analogue of min_net_points), and a spread
    # allowance (fraction of price) is charged in the replay.
    stocks_min_net_inr: float = 100.0
    stocks_spread_frac: float = 0.0003

    # ── 30 Sep 2026 settings (research/PAPERS.md 0k) ──────────────────────────
    # Trading capital is min(capital_ceiling, the account's real equity): a
    # profit stays in the account but is not traded, a loss shrinks what is.
    # 0 = no ceiling (the day's P&L compounds, the old behaviour).
    capital_ceiling: float = 0.0
    # Pace gate per index: {"NIFTY": [first_half, second_half], ...}, halves
    # split at atr_half_split. A listed index uses these instead of
    # min_atr_ratio and the midday rule; "STOCKS" covers the stock desk.
    atr_by_index: dict = field(default_factory=dict)
    atr_half_split: str = "12:15"
    # Enter only with the last N completed 4-hour index bars (09:15-13:14,
    # 13:15-close). 0 = off. Daily 3-5 day trends tested worse (0k).
    trend_4h_bars: int = 0
    # On an index's expiry day buy the NEXT expiry, not the 0DTE contract.
    expiry_trade_next: bool = False
    # Entries and exits as marketable limits: the touch +/- protection, IOC.
    # A plain MARKET order is refused for API orders on NSE F&O
    # (NSE/FAOP/69296) and by Angel on BFO, so this is the fill-now order.
    market_orders: bool = False
    market_protection_pct: float = 2.5
    # ./run.sh_real holds a systemd-inhibit sleep/lid lock for as long as the
    # LIVE engine runs (./run.sh always does for paper). Off = no lock.
    live_block_sleep: bool = False

    # ── the 1 Oct 2026 live-loss review (owner-approved fixes) ─────────────
    # F1. Decide on COMPLETED bars only, within entry_window_sec of the close,
    # and skip when price has already moved max_chase_atr from that close. Live
    # used to read the still-forming bar, so it traded a rule tools/backtest.py
    # never tested (a 23-second-old "rejection wick" on 1 Oct).
    decide_on_closed_bars: bool = False
    entry_window_sec: float = 15.0
    max_chase_atr: float = 0.5
    # F2. No entry once the index has already run max_run_atr ATRs in the
    # trade's direction over the last run_bars completed bars. 0 disables.
    max_run_atr: float = 0.0
    run_bars: int = 30
    # F3. No put right after a reclaimed sell-side sweep, no call after a
    # buy-side one (arb/regime.py:swept_against).
    sweep_against_veto: bool = False
    # F4. After a losing index trade, no new trade on the same side on any index
    # for this many minutes. 0 disables.
    same_side_cooldown_min: float = 0.0
    # F5. Count the round trip and the expected exit slippage (exit_slip_spreads
    # spreads + exit_slip_frac of the premium) INSIDE max_risk_pct_per_trade,
    # so a stop-out loses at most the cap. Stocks: fees, slippage and spread.
    risk_includes_costs: bool = False
    # Price a trade's round trip with the exact BUY + SELL charges (the cost
    # the journal books), not the arbitrage scanner's worst-case bound. See
    # IntradayEngine._round_trip_points.
    exact_trade_costs: bool = False
    # Last-half-hour momentum (Gao et al. 2018; Baltussen et al. 2021), owner's
    # momentum request of 5 Oct 2026. At the 14:59 close, if the index has moved
    # lhm_adr_frac x ADR from the previous close and the 4-hour trend agrees,
    # buy in that direction with a stop lhm_stop_atr ATRs away, out at the
    # square-off. A year of NIFTY (research_out/momentum/l1_feasible_result.txt):
    # 84 trades, +26,040 / +13,007, t 2.4 - after ~80 variants, so unproven.
    # "off" | "shadow" (log what it would have done, send nothing).
    last_half_hour: str = "off"
    lhm_indices: tuple = ("NIFTY",)
    lhm_entry_at: str = "15:00"
    lhm_adr_frac: float = 0.5
    lhm_adr_days: int = 10
    lhm_stop_atr: float = 1.5
    lhm_risk_pct: float = 4.0
    # Noise-area breakout (Zarattini, Aziz & Barbon 2024), owner's approval of
    # 5 Oct 2026 for SHADOW only: on the nb_grid_min grid between nb_lo and
    # nb_hi (bar end time), a close outside max(open, prev close) x (1 + nb_vm x
    # sigma(t)) / min(...) x (1 - ...), sigma from the last nb_sigma_days
    # sessions (need nb_sigma_min_days; fetched once a day, arb/noise_sigma.py);
    # stop at the band (or nb_minr ATRs), trailed on the grid. A year with the
    # 4h trend + pace gates (research/noise_breakout_result.txt): n=65,
    # +9,385 / +14,108 - but it loses out of sample on a 5- or 4-session sigma.
    # "off" | "shadow" (log and journal what it would do, send nothing). Any
    # other value, "on" included, is off: real orders need a separate approval.
    noise_breakout: str = "off"
    nb_indices: tuple = ("NIFTY", "BANKNIFTY", "SENSEX")
    nb_vm: float = 1.0
    nb_minr: float = 1.5
    nb_grid_min: int = 30
    nb_lo: str = "09:45"
    nb_hi: str = "14:45"
    nb_sigma_days: int = 14
    nb_sigma_min_days: int = 10
    nb_history_days: int = 28           # calendar days fetched (Angel ONE_MINUTE: <= 30 a call)
    nb_gap_hold_sec: float = 180.0      # a missing minute holds the shadow trail this long, then is marked
    exit_slip_spreads: float = 1.0
    exit_slip_frac: float = 0.01
    # Exit safety (Phase A C3). With exit_safety_fixes an exit order that is
    # not known final at Angel (still working, or the place call raised) is
    # remembered on the position, and no second exit is sent until it is
    # resolved; the unknown-status reconcile reads the sign of what is held;
    # cancels use the order's own variety; a partial fill can trip the daily
    # loss halt. Off = today's behaviour.
    exit_safety_fixes: bool = False
    # Send an "ordertag" (TE...) with every order, so the start guard can tell
    # the engine's leftovers from orders placed by hand. Off until a probe
    # shows Angel accepts it.
    order_tags: bool = False
    # Duration of the limit orders the live broker sends: "IOC" (as before)
    # or "DAY". Angel bars IOC for algo orders; a DAY order still working
    # after confirm_timeout is cancelled by the broker. Paper ignores it.
    order_duration: str = "IOC"
    # An entry whose status could not be read but which the broker shows
    # held is adopted as a position under the software stop (entries stay
    # halted). Off = halt only, with nothing protecting it. Options only.
    adopt_unknown_entry: bool = False
    # Place, modify and cancel calls held to this many a second, together
    # (Angel: 9/s combined per client). 0 = no gate.
    order_rate_per_sec: float = 0.0

    @classmethod
    def load(cls, path=None):
        path = path or os.path.join(ROOT, "config", "intraday.json")
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            known = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in data.items() if k in known})
        return cls()

    def save(self, path=None):
        path = path or os.path.join(ROOT, "config", "intraday.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.__dict__, fh, indent=2)


STOCKS_RISK_CEILING_PCT = 7.0


def stock_risk_pct(L):
    """The per-trade risk cap for a stock trade, % of capital: stocks_max_risk_pct
    when set, else max_risk_pct_per_trade; never above 7% (owner, 6 Oct 2026)."""
    pct = float(getattr(L, "stocks_max_risk_pct", 0.0) or 0.0) or float(L.max_risk_pct_per_trade)
    return min(pct, STOCKS_RISK_CEILING_PCT)
