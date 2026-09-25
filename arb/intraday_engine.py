"""The intraday scalping loop: read structure, buy one option, manage it out.

This replaces the arbitrage scan loop for `--strategy intraday`. The shape is
the same - one thread woken by the feed - but what it does on each wake is not:

    1. manage first, hunt second.  Open positions are reviewed against every
       tick before any new entry is considered. An exit missed by one second
       costs more than an entry missed entirely.
    2. one completed setup, one affordable contract.  A named playbook
       (arb/playbooks.py, via arb/confluence.py) gives the direction and the
       stop; a contract inside ATM +/- N must fit the purse. Extra agreeing
       reads grade the setup and size it - they no longer gate it.
    3. the stop is placed where the idea dies, not at a round number, and the
       target is a multiple of that distance.

Honest naming: nothing here is arbitrage. The scanners this file replaces
produced structures whose profit was fixed at entry; a long call is a bet on
direction that decays while you hold it.
"""
import threading
import time

from arb.instruments import now_ist
from arb.intraday import ContractSelector, plan_trade, structural_invalidation
from arb.positions import EXIT, Position, PositionBook
from arb.confluence import evaluate, grade_at_least
from arb.playbooks import unknown as unknown_playbooks
from arb.regime import atr, atr_ratio, premium_gate, regime_gate, session_phase
from arb.smc import BEAR, BULL, RANGE, analyse
from arb.costs import per_unit_cost_points
from arb import trailing


class _Ticket:
    """Minimal stand-in for an Opportunity so the existing brokers can fill it.

    PaperBroker and LiveBroker both take anything with `.legs` and `.qty`, so a
    single-leg order reuses their fill modelling, slippage and order plumbing
    rather than growing a second execution path that could drift from the first.
    """
    def __init__(self, legs, qty, strategy, description, expiry, T=0.0):
        self.legs = legs
        self.qty = qty
        self.strategy = strategy
        self.description = description
        self.expiry = expiry
        self.T = T
        self.gross_edge_points = 0.0
        self.est_cost_points = 0.0
        self.max_age_ms = 0.0
        self.min_depth_ratio = 1.0
        self.detail = {}


class IntradayEngine:
    def __init__(self, feed, bindings, cfg, cost_model, limits, broker, journal,
                 capital_book, candles, notifier=None, log=print):
        self.feed = feed
        self.bindings = bindings
        self.cfg = cfg
        self.cm = cost_model
        self.L = limits
        self.broker = broker
        self.journal = journal
        self.capital = capital_book
        self.candles = candles
        self.notifier = notifier
        self.log = log

        self.book = PositionBook(limits, log=log)
        self.selector = ContractSelector(limits, log=log)
        self._running = threading.Event()
        self._thread = None
        self.trade_id = 0
        self.verdicts = {}              # underlying -> the last Verdict computed
        self.trades_today = 0
        self.realised_pnl = 0.0
        self.wins = self.losses = 0
        self.halted = False
        self.halt_reason = ""
        self.last_trade_ts = 0.0
        self._symbol_cooldown = {}
        self.structures = {}            # underlying -> Structure, for the dashboard
        self.rejects = {}
        self.passes = 0
        self.records = []
        self._starved_since = {}        # underlying -> when its candles ran out
        self._starved_warned = {}
        # Discipline state (IntradayLimits "discipline" block).
        self.loss_streak = 0
        self.last_loss_ts = 0.0
        self.paused_until = 0.0
        self.careful = False            # after a streak: only good grades until a win
        self.playbook_count = {}        # (underlying, playbook, side) -> entries today
        # The exchange clock lives on the session config; the playbook layer
        # reads it off the limits. Copy it once here so there is one source of
        # truth rather than two defaults that can drift apart.
        start = getattr(cfg, "session_start", None)
        if start:
            self.L.session_start = start
        # Resume the day from the ledger: a restart must not reset the day's
        # P&L, the trade count, or - above all - the daily loss halt.
        if hasattr(capital_book, "open_day"):
            try:
                folded, _ = capital_book.open_day()
                if folded:
                    self.log(f"[capital] yesterday's Rs {folded:+,.2f} folded in; "
                             f"today's capital Rs {capital_book.capital:,.2f}")
                self.realised_pnl = capital_book.day_pnl
                self.trades_today = capital_book.day_trades
                if self.trades_today:
                    self.log(f"[capital] resuming today: {self.trades_today} trades, "
                             f"Rs {self.realised_pnl:+,.2f} realised")
                limit = abs(capital_book.capital * self.L.max_daily_loss_pct / 100.0)
                if self.realised_pnl <= -limit:
                    self.halted = True
                    self.halt_reason = "daily loss limit (resumed)"
            except Exception as e:
                self.log(f"[capital] ledger unavailable: {e}")
        stale = unknown_playbooks(self.L)
        if stale:
            self.log(f"[intraday] config names unknown playbooks: "
                     f"{', '.join(stale)} - they are not running")

    # ── helpers ─────────────────────────────────────────────────────────────
    def _note(self, reason):
        self.rejects[reason] = self.rejects.get(reason, 0) + 1

    def _spot(self, name):
        slot = (getattr(self.feed, "spot_slots", {}) or {}).get(name)
        if slot is not None:
            px = float(self.feed.store.ltp[slot])
            if px > 0:
                return px
        return 0.0

    def _round_trip_points(self, price, qty):
        """Cost of getting in AND out, in premium points per unit.

        The arbitrage engine charged one side because it held to settlement.
        An intraday scalp pays brokerage, exchange fees, GST and slippage twice,
        and a target set on the one-sided figure is a target that does not exist.
        """
        return 2.0 * per_unit_cost_points(price, 1, 0, qty, self.cm)

    @staticmethod
    def _strike_step(binding):
        """The strike interval this expiry actually uses, or 0.0.

        Read off the ladder rather than assumed: weeklies and monthlies do not
        always share a step, and the value floors the psychological-level grid
        (arb/levels.py). The median gap is used so one missing strike in the
        chain cannot halve the answer.
        """
        try:
            ks = sorted(float(k) for k in binding.strikes)
        except Exception:
            return 0.0
        gaps = sorted(b - a for a, b in zip(ks, ks[1:]) if b > a)
        if not gaps:
            return 0.0
        return gaps[len(gaps) // 2]

    def _binding_for(self, underlying):
        """Nearest live expiry for this index - the one with the tightest
        spreads and the most delta per rupee."""
        best = None
        for b in self.bindings:
            if b.underlying != underlying:
                continue
            if best is None or b.T < best.T:
                best = b
        return best

    # ── exits ───────────────────────────────────────────────────────────────
    def _mark_of(self, pos):
        slot = self.feed.store.slot_of_token.get(str(pos.token))
        if slot is None:
            return 0.0
        return float(self.feed.store.bid[slot])

    def manage(self):
        """Review every open position. Runs before any new entry is considered."""
        mins = self.book.time_to_close(self.cfg.session_end)
        for pos in self.book.snapshot():
            bid = self._mark_of(pos)
            idx = self._spot(pos.underlying)
            self._trail(pos, idx)
            action, reason = self.book.review(pos, bid, index_px=idx,
                                              minutes_to_close=mins)
            if action == EXIT and reason == "target":
                rows = self.candles.get(pos.underlying, limit=400) if self.candles else []
                b = self._binding_for(pos.underlying)
                note = trailing.try_extend(pos, rows, idx, self.L,
                                           self._strike_step(b) if b else 0.0)
                if note:
                    self.log(f"[intraday] {pos.symbol} {note}")
                    continue
            if reason in ("trailed_to_breakeven", "trail_advanced"):
                self.log(f"[intraday] {pos.symbol} {reason} -> stop {pos.stop:.2f} "
                         f"({pos.r_multiple:+.2f}R)")
            if action == EXIT:
                self.close(pos, reason)

    def _trail(self, pos, idx):
        """Move the index stop along the swings, once per completed bar."""
        if not self.candles or pos.index_entry <= 0:
            return
        rows = self.candles.get(pos.underlying, limit=400)
        if len(rows) < 3 or rows[-2][0] == pos.last_bar:
            return
        pos.last_bar = rows[-2][0]
        note = trailing.update(pos, rows, self.L, index_px=idx)
        if note:
            self.log(f"[intraday] {pos.symbol} {note}")

    def close(self, pos, reason):
        """Sell the position back. A long option is closed by selling it."""
        bid = self._mark_of(pos) or pos.mark
        leg = {"symbol": pos.symbol, "token": pos.token, "side": "SELL",
               "price": float(bid), "qty": pos.qty, "instrument": "OPT",
               "exchange": pos.segment, "underlying": pos.underlying,
               "strike": pos.strike, "opt": pos.opt, "depth": pos.qty,
               "age_ms": 0.0}
        ticket = _Ticket([leg], pos.qty, "INTRADAY_EXIT",
                         f"exit {pos.symbol} ({reason})", pos.expiry)
        try:
            legs, errors = self.broker.place_structure(ticket, 1)
        except Exception as e:
            self.log(f"[intraday] EXIT FAILED {pos.symbol}: {e!r} - position still open")
            self._note("exit_error")
            return None
        fill = float(legs[0].get("fill_price") or bid)
        if not legs[0].get("filled"):
            self.log(f"[intraday] exit not filled for {pos.symbol}; will retry next tick")
            return None

        from arb.costs import leg_cost
        cost = (leg_cost(pos.entry, pos.qty, "BUY", "OPT", self.cm)
                + leg_cost(fill, pos.qty, "SELL", "OPT", self.cm))
        gross = (fill - pos.entry) * pos.qty
        net = gross - cost

        self.book.remove(pos.trade_id)
        self.realised_pnl += net
        if hasattr(self.capital, "record"):
            try:
                self.capital.record(net)
            except Exception as e:
                self.log(f"[capital] ledger write failed: {e}")
        if net >= 0:
            self.wins += 1
            self.loss_streak = 0
            self.careful = False
        else:
            self.losses += 1
            self._on_loss()
        if reason in ("stop_loss", "structure_broken") and net < 0:
            self._symbol_cooldown[pos.symbol] = time.time()

        rec = {
            "trade_id": pos.trade_id, "ts_ist": now_ist().isoformat(),
            "strategy": f"INTRADAY_{pos.opt}", "mode": getattr(self.broker, "mode", "paper"),
            "symbol": pos.symbol, "strike": pos.strike, "opt": pos.opt,
            "underlying": pos.underlying, "expiry": pos.expiry, "qty": pos.qty,
            "entry": round(pos.entry, 2), "exit": round(fill, 2),
            "exit_reason": reason,
            "stop": round(pos.stop, 2), "initial_stop": round(pos.initial_stop, 2),
            "target": round(pos.target, 2), "trailed": pos.trailed,
            "index_entry": round(pos.index_entry, 2),
            "index_stop_final": round(pos.index_invalidation, 2),
            "index_target": round(pos.index_target, 2),
            "index_trailed": pos.index_trailed, "extensions": pos.extensions,
            "points": round(fill - pos.entry, 2),
            "r_multiple": round((fill - pos.entry) / pos.risk_points, 2)
                          if pos.risk_points else 0.0,
            "gross_inr": round(gross, 2), "cost_inr": round(cost, 2),
            "net_inr": round(net, 2),
            "capital_inr": round(pos.entry * pos.qty, 2),
            "mae": round(pos.mae, 2), "mfe": round(pos.mfe, 2),
            "held_sec": round(time.time() - pos.opened_at, 1),
            "day_pnl": round(self.realised_pnl, 2),
            "capital": round(self.capital.capital, 2),
            "opportunity": {"underlying": pos.underlying, "expiry": pos.expiry,
                            "strategy": f"INTRADAY_{pos.opt}"},
            "legs": [dict(leg, fill_price=fill, filled=True)],
            **pos.detail,
        }
        self.records.append(rec)
        try:
            self.journal.record_trade(rec)
        except Exception as e:
            self.log(f"[intraday] journal write failed: {e}")

        tgt = self.capital.daily_target(self.L.daily_target_pct)
        self.log(f"[intraday] EXIT {pos.symbol} @ {fill:.2f} ({reason}) "
                 f"{fill - pos.entry:+.2f}pt  net Rs {net:+,.0f}  "
                 f"day Rs {self.realised_pnl:+,.0f} / target Rs {tgt:,.0f}")
        if self.notifier:
            emoji = "✅" if net >= 0 else "🔴"
            pct = 100.0 * self.realised_pnl / self.capital.capital if self.capital.capital else 0
            self.notifier.send(
                f"{emoji} <b>EXIT</b> {pos.symbol}\n"
                f"{pos.entry:.2f} → {fill:.2f} ({fill - pos.entry:+.2f} pt) · {reason}\n"
                f"Net <code>Rs {net:+,.0f}</code>\n"
                f"Day <code>Rs {self.realised_pnl:+,.0f}</code> "
                f"(<code>{pct:+.2f}%</code> of Rs {self.capital.capital:,.0f}, "
                f"target {self.L.daily_target_pct:g}%)")

        if self.realised_pnl <= -abs(self.capital.capital * self.L.max_daily_loss_pct / 100.0):
            self.halted = True
            self.halt_reason = "daily loss limit"
            self.log(f"[intraday] HALTED: daily loss limit hit at Rs {self.realised_pnl:,.0f}")
        return rec

    def _on_loss(self):
        """The rules every scalping course teaches and every tired trader breaks.

        No revenge trade: a loss starts its own cooldown. A streak pauses the
        desk and, when it resumes, only better-graded setups may trade until
        the next win. None of this halts the day - the daily loss limit does
        that and nothing else.
        """
        L = self.L
        self.loss_streak += 1
        self.last_loss_ts = time.time()
        cap = int(getattr(L, "max_consecutive_losses", 0) or 0)
        if cap and self.loss_streak >= cap:
            mins = float(getattr(L, "loss_streak_pause_min", 30.0))
            self.paused_until = time.time() + 60.0 * mins
            self.careful = True
            self.loss_streak = 0
            self.log(f"[intraday] {cap} losses in a row - pausing new entries "
                     f"{mins:g} min; then grade {getattr(L, 'grade_after_streak', 'B')}"
                     f"+ only until a win")
            if self.notifier:
                self.notifier.send(f"⏸ <b>PAUSE</b> {cap} losses in a row. "
                                   f"No entries for {mins:g} min.")

    def _lots_for(self, grade, cand, plan):
        """Lots by setup grade, then cut to what capital and risk allow."""
        L = self.L
        want = 1
        if getattr(L, "grade_sizing", False):
            want = int(getattr(L, f"lots_grade_{(grade or 'c').lower()}", 1) or 1)
        want = max(1, want)
        free = self.capital.capital - self.book.deployed()
        by_cash = int(free // max(cand.lot_cost, 1e-9))
        risk_cap = self.capital.capital * L.max_risk_pct_per_trade / 100.0
        by_risk = int(risk_cap // max(plan["risk_inr"], 1e-9))
        return max(0, min(want, by_cash, by_risk))

    # ── entries ─────────────────────────────────────────────────────────────
    def _gates(self):
        """Everything that must be true before an entry is even considered."""
        L = self.L
        if self.halted:
            return f"halted: {self.halt_reason}"
        if self.book.count() >= L.max_open_positions:
            return "position_open"
        if self.trades_today >= L.max_trades_per_day:
            return "max_trades_per_day"
        if time.time() - self.last_trade_ts < L.trade_cooldown_sec:
            return "cooldown"
        if time.time() < self.paused_until:
            return "paused_after_losing_streak"
        if time.time() - self.last_loss_ts < float(getattr(L, "cooldown_after_loss_sec", 0.0)):
            return "cooldown_after_loss"
        mins = self.book.time_to_close(self.cfg.session_end)
        if mins <= L.no_new_trades_minutes:
            return "too_close_to_squareoff"
        open_at = now_ist().replace(hour=9, minute=15, second=0, microsecond=0)
        if (now_ist() - open_at).total_seconds() / 60.0 < L.warmup_minutes:
            return "warmup"
        return ""

    def hunt(self):
        gate = self._gates()
        if gate:
            self._note(gate)
            return

        available = self.capital.capital - self.book.deployed()
        for und in sorted({b.underlying for b in self.bindings}):
            b = self._binding_for(und)
            if b is None:
                continue
            # The higher timeframe needs enough history to fold into 15-minute
            # bars, so this window is sized for the day trend, not the entry.
            rows = self.candles.get(und, limit=400) if self.candles else []
            # A structure engine with no candles is not a cautious engine, it is
            # a blind one, and every downstream gate will refuse for reasons
            # that sound like ordinary market conditions. Say it out loud, once
            # per index per outage, or a dead data feed reads as a quiet day.
            self._check_starved(und, rows)
            st = analyse(rows, strength=self.L.swing_strength,
                         min_bars=self.L.min_structure_bars)
            self.structures[und] = st

            # Regime decides whether the tape is moving enough for any read to
            # survive its own round trip, and whether we are in a part of the
            # session where continuation is a real effect rather than bid-ask
            # bounce. Cheapest refusal available, so it runs first.
            veto = regime_gate(rows, self.L)
            if veto:
                self._note(veto)
                continue

            spot = self._spot(und) or (st.closes[-1] if st.closes else 0.0)
            if spot <= 0:
                self._note("no_spot")
                continue

            # The decision itself: one completed playbook, judged against the
            # day's bias, graded by whatever else agrees. See arb/confluence.py.
            v = evaluate(rows, spot, self.L, strike_step=self._strike_step(b))
            self.verdicts[und] = v
            if not v.tradable:
                self._note(v.veto or ("no_setup" if not v.setups else
                                      f"confluence:{v.count}/{self.L.min_confirmations}"))
                continue
            if self.careful and not grade_at_least(
                    v.grade, getattr(self.L, "grade_after_streak", "B")):
                self._note("grade_too_low_after_streak")
                continue
            cap = int(getattr(self.L, "max_per_playbook_per_day", 0) or 0)
            lead_name = v.setups[0].name if v.setups else ""
            if cap and self.playbook_count.get((und, lead_name, v.side), 0) >= cap:
                self._note(f"playbook_cap:{lead_name}")
                continue

            # Only buy the retracement into the order block, never the breakout
            # candle itself: entering on the impulse puts the stop a full leg
            # away, which is the same trade with a quarter of the reward-to-risk.
            zone = st.active_zone(spot, v.side)
            # A setup that nominates its own entry zone - the golden pocket, the
            # reclaimed pool - has already answered the question this gate asks,
            # which is "are we buying a retracement rather than the breakout".
            # Demanding an order block on top of it would refuse the sweep and
            # fibonacci models for failing a test they were never taking.
            # Any completed playbook satisfies this: it nominates its own stop,
            # and "is this a retracement" is a question each playbook answers
            # for itself. The check only bites with require_playbook off.
            if self.L.require_order_block and zone is None and not v.setups:
                self._note("not_in_order_block")
                continue

            cv = b.view(self.feed.store, time.perf_counter_ns(), time.time())
            # Delta sizing: find the chart's stop first, then the most delta
            # that keeps a loss there inside the per-trade cap.
            max_delta = None
            cap_inr = self.capital.capital * self.L.max_risk_pct_per_trade / 100.0
            lead0 = v.setups[0] if v.setups else None
            if str(getattr(self.L, "stop_policy", "delta")) == "delta":
                inv = structural_invalidation(st, v.side,
                                              lead0.invalidation if lead0 else None)
                ref = st.closes[-1] if st.closes else spot
                idx_risk = abs(ref - inv) if inv else 0.0
                if idx_risk <= 0:
                    self._note("no_viable_stop_or_target")
                    continue
                max_delta = cap_inr / (idx_risk * max(cv.lot_size, 1))
            cand = self.selector.select(cv, spot, v.side, available,
                                        max_age_ms=400.0, min_depth_mult=1.0,
                                        max_delta=max_delta)
            if cand is None:
                self._note(self.selector.last_reject or "no_contract")
                continue
            if time.time() - self._symbol_cooldown.get(cand.symbol, -1e9) \
                    < self.L.symbol_cooldown_sec:
                self._note("symbol_cooldown")
                continue

            cost_pts = self._round_trip_points(cand.ask, cand.lot_size)
            # Our P&L is the premium's chart, not the index's: it must be trending too.
            slot = self.feed.store.slot_of_token.get(str(cand.token))
            prem = getattr(self.candles, "premium", None)
            veto = premium_gate(prem(slot) if prem and slot is not None else [],
                                cost_pts, self.L)
            if veto:
                self._note(veto)
                continue
            # The deepest setup's own invalidation is a better stop than the
            # generic swing: it is the price that particular model says should
            # not trade again. plan_trade ignores it if it sits on the wrong
            # side of spot.
            lead = v.setups[0] if v.setups else None
            plan = plan_trade(cand, st, v.side, self.L, cost_pts,
                              invalidation=(lead.invalidation if lead else None),
                              max_risk_inr=cap_inr,
                              atr=atr(rows[:-1], period=self.L.atr_period))
            if plan is None:
                self._note("no_viable_stop_or_target")
                continue
            if plan["risk_inr"] > cap_inr + 1e-6:
                self._note("risk_too_large")
                continue
            lots = self._lots_for(v.grade, cand, plan)
            if lots <= 0:
                self._note("risk_too_large")
                continue
            plan["index_entry"] = spot
            plan["entry_bar"] = rows[-1][0] if rows else ""
            tgt = trailing.initial_target(rows, v.side, spot, plan["index_risk"],
                                          self.L, self._strike_step(b))
            plan["index_target"] = tgt
            if tgt and str(getattr(self.L, "target_mode", "momentum")) != "fixed":
                # The target is what the market is making; the question now is
                # whether that pays for this stop and for the round trip.
                reach = abs(tgt - spot)
                if reach < float(getattr(self.L, "min_target_r", 1.5)) * plan["index_risk"]:
                    self._note("target_not_achievable")
                    continue
                reward_prem = reach * plan["delta"]
                if reward_prem - cost_pts < self.L.min_net_points:
                    self._note("move_too_small_for_costs")
                    continue
                plan["reward_points"] = reward_prem
                plan["reward_inr"] = reward_prem * cand.lot_size
            if self.enter(cand, plan, st, zone, v, lots=lots):
                key = (und, lead_name, v.side)
                self.playbook_count[key] = self.playbook_count.get(key, 0) + 1
            return

    def _check_starved(self, und, rows):
        """Warn when an index has gone without usable candles for too long.

        The threshold is in seconds rather than passes because the loop spins on
        every tick: a busy tape would otherwise trip the warning in a moment and
        a dead one might never reach the count.
        """
        need = self.L.min_structure_bars
        ok = len(rows) >= need
        if ok:
            if self._starved_since.pop(und, None) is not None:
                self._starved_warned.pop(und, None)
                self.log(f"[intraday] {und}: candles restored ({len(rows)} bars)")
            return
        now = time.time()
        first = self._starved_since.setdefault(und, now)
        grace = float(getattr(self.L, "candle_starvation_warn_sec", 120.0))
        if now - first >= grace and not self._starved_warned.get(und):
            self._starved_warned[und] = True
            self.log(f"[intraday] NO DATA: {und} has {len(rows)}/{need} candles "
                     f"after {now - first:.0f}s. Nothing can trade without "
                     f"structure - check the historical feed and the spot tick.")
            if self.notifier:
                self.notifier.send(
                    f"\u26a0\ufe0f <b>NO DATA</b> {und}\n"
                    f"{len(rows)}/{need} candles. The engine is blind and will "
                    f"not trade until the feed returns.")

    def enter(self, cand, plan, structure, zone, verdict=None, lots=1):
        L = self.L
        leg = {"symbol": cand.symbol, "token": cand.token, "side": "BUY",
               "price": cand.ask, "qty": cand.lot_size, "instrument": "OPT",
               "exchange": cand.segment, "underlying": cand.underlying,
               "strike": cand.strike, "opt": cand.opt, "depth": cand.depth,
               "age_ms": cand.age_ms}
        ticket = _Ticket([leg], cand.lot_size, f"INTRADAY_{cand.opt}",
                         f"buy {cand.symbol}", cand.expiry)
        lots = max(1, int(lots))
        try:
            legs, errors = self.broker.place_structure(ticket, lots)
        except Exception as e:
            self.log(f"[intraday] entry failed: {e!r}")
            self._note("entry_error")
            return False
        if not legs[0].get("filled"):
            self._note("entry_not_filled")
            return False
        qty = int(legs[0].get("qty") or cand.lot_size * lots)

        fill = float(legs[0]["fill_price"])
        # Re-anchor stop and target on the price actually paid, not the quote the
        # plan was drawn on. Slipping in and keeping the old stop silently makes
        # the risk bigger than the one that was approved.
        risk = plan["risk_points"]
        self.trade_id += 1
        self.trades_today += 1
        self.last_trade_ts = time.time()

        pos = Position(
            trade_id=self.trade_id, symbol=cand.symbol, token=cand.token,
            strike=cand.strike, opt=cand.opt,
            side=1 if cand.opt == "CE" else -1,
            qty=qty, entry=fill,
            stop=fill - risk, target=fill + L.reward_risk * risk,
            risk_points=risk, index_invalidation=plan["index_invalidation"],
            opened_at=time.time(), expiry=cand.expiry,
            underlying=cand.underlying, segment=cand.segment,
            cost_points=plan["cost_points_round_trip"],
            detail={"entry_reason": (verdict.reason if verdict else structure.reason),
                    "confirmations": (verdict.names() if verdict else []),
                    "playbooks": (verdict.plays() if verdict else []),
                    "setup_chain": (verdict.chain() if verdict else []),
                    "day_trend": (verdict.day_trend if verdict else 0),
                    "bias_source": (verdict.bias_source if verdict else ""),
                    "grade": (verdict.grade if verdict else ""),
                    "reversal": (verdict.reversal if verdict else False),
                    "lots": lots,
                    "structure": structure.last_event,
                    "order_block": [zone.low, zone.high] if zone else None,
                    "delta": round(cand.delta, 3),
                    "cheapness_pt": round(cand.cheapness, 3),
                    "strike_offset": cand.offset,
                    "spread": round(cand.detail.get("spread", 0.0), 2),
                    "cost_round_trip_pt": round(plan["cost_points_round_trip"], 3),
                    "index_at_entry": round(structure.closes[-1], 2)
                                      if structure.closes else 0.0})
        # Index-side management. With an index target the premium target is
        # parked out of reach, so the level the market printed decides the
        # exit, not a fixed multiple of an option price whose delta is moving.
        pos.index_entry = float(plan.get("index_entry") or 0.0)
        pos.index_risk = float(plan.get("index_risk") or 0.0)
        pos.delta = float(plan.get("delta") or cand.delta)
        pos.entry_bar = str(plan.get("entry_bar") or "")
        mode = str(getattr(L, "target_mode", "structure"))
        if mode != "fixed" and pos.index_entry > 0:
            pos.index_target = float(plan.get("index_target") or 0.0)
            pos.target = fill * 100.0
            est = abs(pos.index_target - pos.index_entry) * pos.delta \
                if pos.index_target > 0 else 0.0
            pos.detail["index_target"] = round(pos.index_target, 2)
            pos.detail["target_premium_est"] = round(fill + est, 2) if est else None
        self.book.add(pos)

        why = " + ".join(verdict.plays()) if verdict and verdict.plays() \
            else structure.last_event
        grade = verdict.grade if verdict else ""
        self.log(f"[intraday] ENTRY BUY {cand.symbol} @ {fill:.2f} x{qty} "
                 f"= Rs {fill * qty:,.0f} | grade {grade} {why} "
                 f"delta {cand.delta:.2f} offset {cand.offset:+d} | "
                 f"stop {pos.stop:.2f} target {self._target_text(pos)} "
                 f"(risk Rs {plan['risk_inr'] * lots:,.0f} : "
                 f"reward Rs {plan['reward_inr'] * lots:,.0f})")
        if self.notifier:
            self.notifier.send(
                f"🔵 <b>BUY</b> <code>{cand.symbol}</code>\n"
                f"Entry <code>{fill:.2f}</code> x {qty} "
                f"= <code>Rs {fill * qty:,.0f}</code>\n"
                f"Why <code>{why}</code> · grade <code>{grade}</code> · "
                f"delta <code>{cand.delta:.2f}</code>\n"
                f"Stop <code>{pos.stop:.2f}</code> · "
                f"Target <code>{self._target_text(pos)}</code>\n"
                f"Risk <code>Rs {plan['risk_inr'] * lots:,.0f}</code> · "
                f"Reward <code>Rs {plan['reward_inr'] * lots:,.0f}</code>")
        return True

    @staticmethod
    def _target_text(pos):
        if pos.index_target > 0:
            r = abs(pos.index_target - pos.index_entry) / pos.index_risk \
                if pos.index_risk else 0.0
            return f"index {pos.index_target:.1f} ({r:.1f}R)"
        if pos.target >= pos.entry * 50:
            return "trail only"
        return f"{pos.target:.2f}"

    # ── loop ────────────────────────────────────────────────────────────────
    def run(self, stop_predicate=None):
        self._running.set()
        while self._running.is_set():
            if stop_predicate and stop_predicate():
                break
            self.feed.tick_event.wait(timeout=0.25)
            self.feed.tick_event.clear()
            self.passes += 1
            try:
                self.manage()
                self.hunt()
            except Exception as e:
                self.log(f"[intraday] loop error: {e!r}")
        # Never leave the desk with a position on it.
        for pos in self.book.snapshot():
            self.close(pos, "squareoff")
        self._running.clear()

    def start(self, **kw):
        self._thread = threading.Thread(target=self.run, kwargs=kw,
                                        name="intraday", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=10.0):
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=timeout)

    def summary(self):
        cap = self.capital.capital
        return {
            "capital": round(cap, 2),
            "day_pnl": round(self.realised_pnl, 2),
            "day_pct": round(100.0 * self.realised_pnl / cap, 3) if cap else 0.0,
            "target_inr": round(self.capital.daily_target(self.L.daily_target_pct), 2),
            "target_pct": self.L.daily_target_pct,
            "target_hit": self.realised_pnl >= self.capital.daily_target(self.L.daily_target_pct),
            "trades": self.trades_today, "wins": self.wins, "losses": self.losses,
            "open": self.book.count(), "halted": self.halted,
            "halt_reason": self.halt_reason,
            "discipline": {"loss_streak": self.loss_streak,
                           "paused_for_sec": max(0.0, round(self.paused_until - time.time(), 0)),
                           "careful": self.careful},
            "rejects": dict(sorted(self.rejects.items(), key=lambda kv: -kv[1])[:10]),
            "structures": {k: {"bias": v.bias, "event": v.last_event,
                               "reason": v.reason,
                               "atr_ratio": round(self._atr_ratio(k), 2)}
                           for k, v in self.structures.items()},
            "session_phase": session_phase(),
            "confluence": {k: {"side": v.side, "day_trend": v.day_trend,
                               "count": v.count, "need": self.L.min_confirmations,
                               "grade": v.grade, "bias_source": v.bias_source,
                               "reversal": v.reversal,
                               "names": v.names(), "veto": v.veto,
                               "reason": v.reason, "playbooks": v.plays(),
                               "setups": v.chain()}
                           for k, v in self.verdicts.items()},
        }

    def _atr_ratio(self, underlying):
        """Live volatility regime for the dashboard: 1.0 is this index's own
        normal, below `min_atr_ratio` is a tape too quiet to pay for itself."""
        rows = self.candles.get(underlying, limit=250) if self.candles else []
        return atr_ratio(rows, period=self.L.atr_period,
                         baseline_bars=self.L.atr_baseline_bars)
