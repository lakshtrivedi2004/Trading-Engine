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
import html
import json
import os
import threading
import time
from dataclasses import asdict, fields
from datetime import datetime

from arb.instruments import now_ist, parse_expiry
from arb.intraday import (ContractSelector, chain_delta, plan_trade,
                          structural_invalidation)
from arb.positions import EXIT, Position, PositionBook
from arb.confluence import evaluate, grade_at_least, regrade
from arb.playbooks import unknown as unknown_playbooks
from arb.regime import (atr, atr_ratio, premium_gate, regime_gate, run_in_direction,
                        session_phase, swept_against, trend_4h)
from arb.scanner import scan_cost_ratio
from arb.smc import BEAR, BULL, RANGE, analyse
from arb.costs import leg_cost, per_unit_cost_points
from arb import optionflow, trailing


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
    # exit_safety_fixes timings (review O4-O6, 2 Oct 2026).
    EXIT_BACKOFF = (0.5, 5.0)       # first and longest wait after a refused or unfilled exit
    EXIT_ALERT_AFTER = 5            # refused or unfilled exits in a row before one alert
    PENDING_ALERT_SEC = 30.0        # an exit or entry order unsettled this long is reported
    ENTRY_POLL_SEC = 2.0            # a pending entry order is looked at this often
    SQUAREOFF_RETRY_SEC = 30.0      # run()'s square-off keeps trying this long

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
        self.stocks = None              # arb/stockdesk.py:StockDesk, when enabled
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
        self.exit_all_requested = False
        self._pending_entries = []      # entry orders not known to be over (review O3, O4)
        self._alerted = set()           # keys of the one-off exit alerts already sent (O2, O6)
        self.last_trade_ts = 0.0
        self._symbol_cooldown = {}
        self.structures = {}            # underlying -> Structure, for the dashboard
        # For the constraints page (arb/constraints.py): when each instrument
        # was last looked at and last judged, and the stocks' own structure.
        # The page reads these; it never recomputes the heavy parts itself.
        self.evaluated_at = {}
        self.verdict_at = {}
        self.stock_structures = {}
        self.rejects = {}
        self.passes = 0
        self.records = []
        self._starved_since = {}        # underlying -> when its candles ran out
        self._starved_warned = {}
        # Discipline state (IntradayLimits "discipline" block).
        self.loss_streak = 0
        self.last_loss_ts = 0.0
        self._loss_side = 0             # side of the last losing index trade (F4)
        self._loss_side_ts = 0.0        # ...and when; stock losses never move it
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
        # The day's record survives a restart: the trades already closed come
        # back from the journal, and a position the last process died holding
        # (flat battery, crash) comes back from its own file.
        jdir = getattr(journal, "dir", None)
        self._open_path = os.path.join(jdir, "open_positions.json") if jdir else None
        self._open_saved_at = 0.0
        self.handover = False           # set by a paper restart: keep positions for the next process
        self._resume_trades()
        self._restore_open()

    # ── the day's record across restarts ────────────────────────────────────
    def _resume_trades(self):
        """Today's closed trades, from the journal the engine already writes.

        Without this a restart blanked the dashboard's trade list, the win and
        loss counts, and restarted trade ids at 1 - while the capital ledger
        (open_day above) kept the day's P&L. The journal is the record.
        """
        path = getattr(self.journal, "jsonl_path", None)
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as fh:
                recs = [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError) as e:
            self.log(f"[intraday] journal unreadable, today's trades not shown: {e}")
            return
        recs = [r for r in recs if "exit_reason" in r]
        if not recs:
            return
        self.records = recs
        self.wins = sum(1 for r in recs if float(r.get("net_inr") or 0.0) >= 0)
        self.losses = len(recs) - self.wins
        self.trade_id = max(int(r.get("trade_id") or 0) for r in recs)
        self.log(f"[intraday] today's journal: {len(recs)} closed trades "
                 f"(W{self.wins}/L{self.losses}) restored to the dashboard")

    def _save_open(self, force=False):
        """Open positions to disk: on every entry and exit, and every few
        seconds while one is open so a trailed stop is not lost. A clean stop
        squares off and leaves the file empty; anything left in it is a
        position the process died holding."""
        if not self._open_path:
            return
        now = time.time()
        if not force and now - self._open_saved_at < 5.0:
            return
        self._open_saved_at = now
        data = {"day": now_ist().strftime("%Y-%m-%d"),
                "mode": getattr(self.broker, "mode", "paper"),
                "positions": [asdict(p) for p in self.book.snapshot()]}
        try:
            tmp = self._open_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh, default=lambda o: o.item() if hasattr(o, "item") else str(o))
            os.replace(tmp, self._open_path)
        except Exception as e:
            self.log(f"[intraday] could not save open positions: {e}")

    def _restore_open(self):
        """Put back the positions the last process died holding.

        Paper only, and only on the same day: an intraday position cannot
        outlive its session. A live position is never restored blindly - the
        broker is the only one who knows if it is still open - so new entries
        halt until a human has looked, as for an entry whose status is unknown.
        """
        path = self._open_path
        if not path or not os.path.exists(path):
            return
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            self.log(f"[intraday] open-position file unreadable: {e}")
            return
        rows = data.get("positions") or []
        if not rows:
            return
        names = ", ".join(str(r.get("symbol", "?")) for r in rows)
        if data.get("day") != now_ist().strftime("%Y-%m-%d"):
            self.log(f"[intraday] {names} from {data.get('day')} was never closed by the "
                     f"engine; an intraday position does not outlive its day - not restored")
            self._save_open(force=True)
            return
        if getattr(self.broker, "mode", "paper") != "paper" or data.get("mode") != "paper":
            self.halted = True
            self.halt_reason = f"{names} was open when the engine stopped - check the Angel app"
            self.log(f"[intraday] HALTED: {self.halt_reason}; not restored")
            if self.notifier:
                self.notifier.send(f"🛑 <b>CHECK THE ANGEL APP</b>\n<code>{names}</code> was open "
                                   f"when the engine stopped. New entries halted.")
            return
        known = {f.name for f in fields(Position)}
        slots = getattr(getattr(self.feed, "store", None), "slot_of_token", {}) or {}
        for r in rows:
            pos = Position(**{k: v for k, v in r.items() if k in known})
            if str(pos.token) not in slots:
                self.log(f"[intraday] cannot restore {pos.symbol}: not subscribed this session")
                continue
            pos.detail = dict(pos.detail or {}, restored=True)
            self.book.add(pos)
            # Counted at entry in-session; the ledger only counts it at exit.
            self.trades_today += 1
            self.trade_id = max(self.trade_id, int(pos.trade_id))
            self.log(f"[intraday] RESTORED {pos.symbol} x{pos.qty} @ {pos.entry:.2f} "
                     f"(stop {pos.stop:.2f}, index stop {pos.index_invalidation:.1f}) - "
                     f"the engine stopped without closing it")
        self._save_open(force=True)

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

        With exact_trade_costs the figure is the exact BUY + SELL charge at this
        premium - the arithmetic _book_fill books when the trade closes. The
        older figure is the arbitrage scanner's upper bound (STT on both legs
        plus a slippage tick per leg), about Rs 121-124 a lot against the Rs
        94-99 actually booked on 1 Oct 2026. Fed into max_cost_frac_of_risk and
        the costs-inside-the-cap sizing, that overstatement refused nearly every
        trade the backtest takes (1 trade in 21 replayed sessions against ~8).
        """
        if getattr(self.L, "exact_trade_costs", False):
            q = max(1, int(qty))
            return (leg_cost(price, q, "BUY", "OPT", self.cm)
                    + leg_cost(price, q, "SELL", "OPT", self.cm)) / q
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
        spreads and the most delta per rupee. With expiry_trade_next, not the
        one expiring today: 0DTE premium decays fastest and the last half-hour
        is the day's most volatile (research/PAPERS.md 0k)."""
        mine = [b for b in self.bindings if b.underlying == underlying]
        if getattr(self.L, "expiry_trade_next", False):
            today = now_ist().date()
            mine = [b for b in mine if parse_expiry(b.expiry).date() != today] or mine
        return min(mine, key=lambda b: b.T, default=None)

    def _option_flow(self, b, cv, cand, und, rows):
        """What this premium really moves per index point, and what the other
        side of the chain says about the move (arb/optionflow.py).

        Returns (effective delta, its detail, CrossSide or None). Without a
        premium history both fall back quietly: the chain delta, and no read.
        """
        L = self.L
        eff, note = cand.delta, {"chain_delta": round(cand.delta, 3)}
        series = getattr(self.candles, "premium_series", None)
        spot = (getattr(self.feed, "spot_slots", {}) or {}).get(und)
        ks = [float(k) for k in cv.strikes]
        if series is None or spot is None or float(cand.strike) not in ks:
            return eff, note, None
        i = ks.index(float(cand.strike))
        mine = b.ce_slots if cand.opt == "CE" else b.pe_slots
        other = b.pe_slots if cand.opt == "CE" else b.ce_slots
        me, spot = int(mine[i]), int(spot)
        limit = max(int(L.realised_delta_bars), 3 * int(L.cross_side_bars)) + 1
        if getattr(L, "use_realised_delta", True):
            s = series([me, spot], limit=limit)
            eff, note = optionflow.effective_delta(cand.delta, cand.opt,
                                                   s[me], s[spot], L)
        if str(getattr(L, "cross_side_mode", "grade")) == "off":
            return eff, note, None
        j = optionflow.mirror_index(cv, i, cand.opt)
        if j < 0:
            return eff, note, optionflow.CrossSide(note="no matched-delta strike")
        mo = int(other[j])
        opp = "PE" if cand.opt == "CE" else "CE"
        dm = chain_delta(cv.pe_ask if opp == "PE" else cv.ce_ask, ks, j, opp)
        s = series([me, mo, spot], limit=limit)
        # The neighbours are asked for separately: one unquoted wing strike
        # must not blank the history the pair itself needs.
        nb = None
        if 0 < i < len(ks) - 1:
            lo, hi = int(mine[i - 1]), int(mine[i + 1])
            deep, far = (lo, hi) if cand.opt == "CE" else (hi, lo)
            w = series([deep, far], limit=int(L.cross_side_bars) + 1)
            nb = (w[deep], w[far]) if w[deep] else None
        cross = optionflow.read(BULL if cand.opt == "CE" else BEAR,
                                s[me], s[mo], s[spot], nb, (cand.delta, dm), L,
                                atr=atr(rows[:-1], period=L.atr_period))
        cross.detail["mirror_strike"] = ks[j]
        return eff, note, cross

    # ── exits ───────────────────────────────────────────────────────────────
    def _mark_of(self, pos):
        """The price we could leave at, in the position's own terms.

        A long leaves at the bid. A short stock leaves by buying at the ask, and
        is carried MIRRORED about its entry (2 x entry - ask), so that every
        rule written for a long - stop below, breakeven, R, the time stop -
        reads a short correctly without a second copy of the exit logic.
        """
        slot = self.feed.store.slot_of_token.get(str(pos.token))
        if slot is None:
            return 0.0
        if pos.opt == "EQ" and pos.side < 0:
            ask = float(self.feed.store.ask[slot])
            return 2.0 * pos.entry - ask if ask > 0 else 0.0
        return float(self.feed.store.bid[slot])

    def _underlying_px(self, pos):
        """Where the level that manages this trade is read: the index for an
        option, the stock's own last price for a stock."""
        if pos.opt != "EQ":
            return self._spot(pos.underlying)
        slot = self.feed.store.slot_of_token.get(str(pos.token))
        return float(self.feed.store.ltp[slot]) if slot is not None else 0.0

    def manual_stop(self, reason="manual stop (dashboard)"):
        """Operator-initiated halt: block new entries and flatten everything now.

        Runs through the exit_all_requested flag on the engine's own thread
        (manage(), next pass) rather than placing orders from the caller's
        thread, so it can never race the scan loop's own exits.
        """
        self.halted = True
        self.halt_reason = reason
        self.exit_all_requested = True
        self.log(f"[intraday] HALTED: {reason}")

    def manual_exit_all(self):
        """Flatten every open position at the next pass, without halting new entries."""
        self.exit_all_requested = True

    def manage(self):
        """Review every open position. Runs before any new entry is considered."""
        fixes = getattr(self.L, "exit_safety_fixes", False)
        if self._pending_entries:
            self._poll_entries()
        tried = set()
        if self.exit_all_requested:
            if not fixes:
                self.exit_all_requested = False
            for pos in self.book.snapshot():
                self.close(pos, "manual_exit_all")
                if fixes:
                    tried.add(pos.trade_id)
            if fixes and all(p.exit_blocked for p in self.book.snapshot()):
                # Kept until the book is empty (or holds only what a human must
                # close): one close() held by a pending exit or a back-off used
                # to use it up and leave the position open (review O9, 2 Oct 2026).
                self.exit_all_requested = False
        mins = self.book.time_to_close(self.cfg.session_end)
        for pos in self.book.snapshot():
            bid = self._mark_of(pos)
            idx = self._underlying_px(pos)
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
            if action == EXIT and pos.trade_id not in tried:
                self.close(pos, reason)
        if self.book.count():
            self._save_open()

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

    def _exit_leg(self, pos, px):
        eq = pos.opt == "EQ"
        short = eq and pos.side < 0
        return {"symbol": pos.symbol, "token": pos.token,
                "side": "BUY" if short else "SELL",
                "price": float(px), "qty": pos.qty, "instrument": "EQ" if eq else "OPT",
                "exchange": pos.segment, "underlying": pos.underlying,
                "strike": pos.strike, "opt": pos.opt, "depth": pos.qty,
                "age_ms": 0.0}

    def close(self, pos, reason):
        """Close the position: sell a long, buy back a short stock."""
        eq = pos.opt == "EQ"
        short = eq and pos.side < 0
        fixes = getattr(self.L, "exit_safety_fixes", False)
        px = self._mark_of(pos) or pos.mark
        if short:
            px = 2.0 * pos.entry - px          # un-mirror: the ask we buy at
        if fixes:
            if pos.exit_blocked:
                return None                     # past flat at the broker: hands off
            if pos.pending_exit_oid or pos.pending_exit_tag:
                # Never send an exit over one that may still fill.
                if not self._resolve_pending_exit(pos, reason, px):
                    return None
                if not self._in_book(pos):
                    return self.records[-1] if self.records else None
            if 0.0 < pos.exit_retry_at - time.time() <= self.EXIT_BACKOFF[1]:
                return None                     # backing off after a refusal or a miss (O5)
        leg = self._exit_leg(pos, px)
        if fixes or getattr(self.L, "order_tags", False):
            leg["kind"] = "X"                   # tags it, and spends the exits' order budget
        # A limit at the touch that keeps missing in a running market is how a
        # stop turns into a disaster. After exit_market_after unfilled tries,
        # get out at market.
        if getattr(self.L, "market_orders", False) or \
                pos.exit_attempts >= int(getattr(self.L, "exit_market_after", 3)):
            leg["market"] = True
        ticket = _Ticket([leg], pos.qty, "INTRADAY_EXIT",
                         f"exit {pos.symbol} ({reason})", pos.expiry)
        try:
            legs, errors = self.broker.place_structure(ticket, 1)
        except Exception as e:
            self.log(f"[intraday] EXIT FAILED {pos.symbol}: {e!r} - position still open")
            self._note("exit_error")
            if fixes and hasattr(self.broker, "open_orders"):
                # It may have raised after the order went: look for it by
                # symbol before anything else is sent.
                self._set_pending(pos, reason)
                self._exit_failed(pos, {}, [repr(e)])
            return None
        fill = float(legs[0].get("fill_price") or px)
        if fixes and (legs[0].get("final") is False or legs[0].get("place_exception")):
            # Not over at Angel - still working after its cancel, or the place
            # call raised - so it may yet fill. Remember it before anything else.
            self._set_pending(pos, reason, legs[0].get("order_id"), legs[0].get("uid"),
                              legs[0].get("tag"))
            self.log(f"[intraday] exit order for {pos.symbol} not final at Angel "
                     f"({pos.pending_exit_oid or 'no id'}, {legs[0].get('broker_status', '')}); "
                     f"no new exit until it is")
        if not legs[0].get("filled") and legs[0].get("broker_status") == "unknown":
            # The order's own status could not be read. Retrying blindly could
            # sell what is already sold - for a stock under MIS that is a new
            # short. Ask the broker what we hold instead.
            held = self.broker.net_position(pos.symbol) if hasattr(self.broker, "net_position") else None
            want = -pos.qty if short else pos.qty
            if held is None:
                self.halted, self.halt_reason = True, f"exit status unknown for {pos.symbol} - check the Angel app"
                self.log(f"[intraday] HALTED: {self.halt_reason}")
                if self.notifier:
                    self.notifier.send(f"🛑 <b>CHECK THE ANGEL APP</b>\nExit of <code>{pos.symbol}</code> "
                                       f"could not be confirmed. New entries halted.")
                return None
            if fixes and hasattr(self.broker, "order_rows"):
                # ...and only when both of its books agree: a SELL that filled
                # before its place call could answer is missing from a lagging
                # position book, and booking on that book alone has sold the
                # same position twice (review O2, 2 Oct 2026).
                held, why = self._held_agreed(pos.symbol, held=held)
                if held is None:
                    self.log(f"[intraday] {pos.symbol}: exit status unknown and {why}; "
                             f"nothing booked or sent until the books agree")
            if held is not None and held != want:
                # Some or all of it did go: book what went, at the price we sent.
                if fixes:
                    gone = self._held_gone(pos, held, short)
                    if gone is None:
                        return None             # wrong sign: book nothing, send nothing
                else:
                    gone = abs(want - held)
                legs[0].update(filled=True, qty=min(gone, pos.qty), fill_price=float(px))
                fill = float(px)
                self.log(f"[intraday] {pos.symbol}: exit status unknown; broker shows "
                         f"{held} held, so {gone} closed")
        if not legs[0].get("filled"):
            pos.exit_attempts += 1
            if fixes:
                self._exit_failed(pos, legs[0], errors)
            self.log(f"[intraday] exit not filled for {pos.symbol} "
                     f"(try {pos.exit_attempts}, {legs[0].get('broker_status', '')}); "
                     f"position still open, retrying"
                     + (" at MARKET" if pos.exit_attempts >= int(getattr(self.L, "exit_market_after", 3)) else ""))
            return None

        # What actually filled. A partial exit is booked now - it is realised
        # money and counts toward the daily loss halt - and the rest of the
        # position stays open and managed.
        q = max(1, min(int(legs[0].get("qty") or pos.qty), pos.qty))
        if fixes and (pos.pending_exit_oid or pos.pending_exit_tag):
            pos.pending_exit_booked, pos.pending_exit_booked_px = q, float(fill)
        return self._book_fill(pos, q, fill, reason, leg)

    def _in_book(self, pos):
        return any(p.trade_id == pos.trade_id for p in self.book.snapshot())

    def _halt_alert(self, reason, text):
        """Halt new entries and say so - once per reason, not every pass."""
        if self.halted and self.halt_reason == reason:
            return
        self.halted, self.halt_reason = True, reason
        self.log(f"[intraday] HALTED: {reason}")
        if self.notifier:
            self.notifier.send(f"🛑 <b>CHECK THE ANGEL APP</b>\n{text}")

    def _held_gone(self, pos, held, short):
        """How much of pos the broker no longer holds, from its net quantity
        `held`. None after a halt: held past flat (a naked position - nothing
        is sent for it again) or more held than the engine knows of."""
        want = -pos.qty if short else pos.qty
        if (held > 0) if short else (held < 0):
            pos.exit_blocked = f"broker shows {held}"
            self._halt_alert(f"NAKED POSITION in {pos.symbol}: broker shows {held} - check the Angel app",
                             f"<b>NAKED POSITION</b> in <code>{pos.symbol}</code>: the broker shows "
                             f"{held}. Nothing booked, nothing sent. New entries halted.")
            return None
        gone = (held - want) if short else (want - held)
        if gone < 0:
            self._halt_alert(f"broker holds MORE {pos.symbol} than the engine thinks "
                             f"({held} against {want}) - check the Angel app",
                             f"The broker holds <b>more</b> <code>{pos.symbol}</code> than the "
                             f"engine thinks ({held} against {want}). Nothing booked. "
                             f"New entries halted.")
            return None
        return gone

    def _clear_pending(self, pos):
        pos.pending_exit_oid = pos.pending_exit_uid = pos.pending_exit_tag = ""
        pos.pending_exit_booked, pos.pending_exit_booked_px = 0, 0.0
        pos.pending_exit_at, pos.pending_none_seen = 0.0, 0
        pos.pending_exit_since, pos.pending_exit_reason = 0.0, ""

    def _set_pending(self, pos, reason, oid="", uid="", tag=""):
        """Remember an exit order that may still fill; no other exit goes out
        until it is settled. "?" for the tag: no id came back, look for it by
        symbol. The reason is kept for a late fill (review O8), the time for a
        wait that runs long (review O6)."""
        pos.pending_exit_oid, pos.pending_exit_uid = str(oid or ""), str(uid or "")
        pos.pending_exit_tag = str(tag or "") or ("" if pos.pending_exit_oid else "?")
        pos.pending_exit_booked, pos.pending_exit_booked_px = 0, 0.0
        pos.pending_exit_at = pos.pending_exit_since = time.time()
        pos.pending_none_seen, pos.pending_exit_reason = 0, reason
        self._save_open(force=True)

    def _exit_failed(self, pos, leg, errors):
        """A refused or unfilled exit (exit_safety_fixes): the next try waits
        0.5 s, doubling to 5 s, and EXIT_ALERT_AFTER in a row are reported once.
        A refusal used to be re-sent on every tick, about 8 a second - Angel's
        1000-an-hour order budget gone in two minutes, and then every exit
        refused, the stop and the square-off too (review O5, 2 Oct 2026)."""
        pos.exit_fails += 1
        first, longest = self.EXIT_BACKOFF
        pos.exit_retry_at = time.time() + min(longest, first * 2 ** min(pos.exit_fails - 1, 16))
        if pos.exit_fails == self.EXIT_ALERT_AFTER:
            why = "; ".join(str(x) for x in errors or []) or str(leg.get("broker_status") or "not filled")
            self.log(f"[intraday] exit of {pos.symbol} refused or unfilled {pos.exit_fails} times "
                     f"in a row ({why}); still trying")
            if self.notifier:
                self.notifier.send(f"⚠️ <b>EXIT NOT GOING THROUGH</b>\n<code>{pos.symbol}</code>: "
                                   f"{pos.exit_fails} tries in a row refused or unfilled "
                                   f"(<code>{html.escape(why[:200])}</code>). Still trying every "
                                   f"few seconds - check the Angel app.")

    def _held_agreed(self, symbol, rows=None, held=None):
        """(held, why): what the broker holds in symbol, only when its two books
        agree - the position book's net, and BUY filled less SELL filled over
        ALL of today's order-book rows in it, completed ones included. Either
        alone has sent a second SELL over a filled one: an order Angel took
        before its place call timed out is complete, never a working row, and
        the position book can lag it (review O2, 2 Oct 2026). held is None,
        and why says what is wrong, while either cannot be read or they differ."""
        b = self.broker
        if rows is None:
            rows = b.order_rows(symbol) if hasattr(b, "order_rows") else None
        if held is None:
            held = b.net_position(symbol) if hasattr(b, "net_position") else None
        if rows is None or held is None:
            return None, f"the {'order' if rows is None else 'position'} book cannot be read"
        ob = b.signed_net(rows)
        if ob != held:
            return None, f"the position book shows {held} held and the order book {ob}"
        return held, ""

    def _agreed_or_wait(self, pos, rows=None):
        """_held_agreed for a pending exit: the agreed figure, or None after
        saying why. An unreadable book is reported at once (review O6); a
        disagreement that outlives PENDING_ALERT_SEC halts new entries (O2)."""
        held, why = self._held_agreed(pos.symbol, rows=rows)
        if held is not None:
            return held
        key = ("books", pos.trade_id, pos.pending_exit_since)
        if key not in self._alerted:
            self._alerted.add(key)
            self.log(f"[intraday] exit of {pos.symbol}: {why}; nothing booked or sent "
                     f"until they agree")
        if why.endswith("cannot be read"):
            self._pending_alert(pos, why)
        elif time.time() - pos.pending_exit_since >= self.PENDING_ALERT_SEC:
            self._halt_alert(f"position and order books disagree on {pos.symbol} - check the Angel app",
                             f"Exit of <code>{pos.symbol}</code> cannot be settled: {why}. "
                             f"No exit is sent until they agree. New entries halted.")
        return None

    def _pending_alert(self, pos, what):
        """One alert per pending exit: while it waits the software stop sends
        nothing, and that used to happen in silence (review O6, 2 Oct 2026)."""
        key = ("pending", pos.trade_id, pos.pending_exit_since)
        if key in self._alerted:
            return
        self._alerted.add(key)
        oid = pos.pending_exit_oid or "not found"
        self.log(f"[intraday] STOP ON HOLD: exit order {oid} for {pos.symbol}: {what}; "
                 f"no new exit until it is settled - check the Angel app")
        if self.notifier:
            self.notifier.send(f"⚠️ <b>CHECK THE ANGEL APP</b>\nExit order <code>{oid}</code> for "
                               f"<code>{pos.symbol}</code>: {html.escape(what)}. Until it is "
                               f"settled the software stop sends nothing.")

    def _resolve_pending_exit(self, pos, reason, px):
        """Settle an exit order not known to be over at Angel. True once no
        exit order of ours can still be working, with whatever it filled
        booked and both books agreeing on the rest; False while one may be -
        and then nothing new may be sent. What it filled books under the exit
        that sent it, not the call that finds it (review O8); a wait past
        PENDING_ALERT_SEC is reported once (review O6)."""
        if not pos.pending_exit_since:
            pos.pending_exit_since = time.time()
        done = self._settle_pending_exit(pos, pos.pending_exit_reason or reason, px)
        if not done:
            age = time.time() - pos.pending_exit_since
            if age >= self.PENDING_ALERT_SEC:
                self._pending_alert(pos, f"not settled after {age:.0f} s")
        return done

    def _settle_rest(self, pos, held, short, reason, px, what):
        """Both books agree the broker holds `held`: book what else of pos has
        gone, then let the next exit go (True), unless pos is blocked."""
        gone = self._held_gone(pos, held, short)
        if gone is None and pos.exit_blocked:
            return False
        self._clear_pending(pos)
        self._save_open(force=True)
        if gone:
            self.log(f"[intraday] {pos.symbol}: {what}; broker shows {held} held, "
                     f"so {gone} closed")
            self._book_fill(pos, min(gone, pos.qty), float(px), reason,
                            self._exit_leg(pos, px), extra={"exit_order": "reconciled"})
        return True

    def _settle_pending_exit(self, pos, reason, px, _again=True):
        b, now = self.broker, time.time()
        short = pos.opt == "EQ" and pos.side < 0
        if not pos.pending_exit_oid and pos.pending_exit_uid and _again \
                and hasattr(b, "order_state"):
            # The answer carried only a uniqueorderid: read the order by that
            # before looking for it by symbol (review O1).
            st = b.order_state(pos.pending_exit_uid, "")
            if st and st["oid"]:
                pos.pending_exit_oid = st["oid"]
                self._save_open(force=True)
        if not pos.pending_exit_oid:
            # The place call raised, or answered with no order id: find the
            # order by its tag, else by side among the ones still working.
            rows = b.order_rows(pos.symbol) if hasattr(b, "order_rows") else None
            if rows is None:
                # Cannot look for it, so cannot rule it out: nothing goes out.
                self._halt_alert(f"exit order for {pos.symbol} lost and the order book is "
                                 f"unreadable - check the Angel app",
                                 f"An exit order for <code>{pos.symbol}</code> may be working "
                                 f"and the order book cannot be read. No new exit is sent "
                                 f"until it can. New entries halted.")
                return False
            tag, side = pos.pending_exit_tag, "BUY" if short else "SELL"
            hit = next((r for r in rows if tag not in ("", "?") and r["tag"] == tag), None) \
                or next((r for r in rows if not r["final"] and r["side"] == side), None)
            if hit:
                if not _again:
                    return False
                pos.pending_exit_oid, pos.pending_exit_uid = hit["oid"], hit["uid"]
                pos.pending_none_seen = 0
                self._save_open(force=True)
                return self._settle_pending_exit(pos, reason, px, _again=False)
            # Nothing working - believed only after two reads 2 s apart, and
            # then only from what both books agree is held (review O2).
            if pos.pending_none_seen == 0 or now - pos.pending_exit_at >= 2.0:
                pos.pending_none_seen += 1
                pos.pending_exit_at = now
            if pos.pending_none_seen < 2:
                return False
            held = self._agreed_or_wait(pos, rows)
            if held is None:
                return False
            return self._settle_rest(pos, held, short, reason, px, "lost exit order")
        st = b.order_state(pos.pending_exit_uid, pos.pending_exit_oid) \
            if hasattr(b, "order_state") else None
        if st is None:
            pos.pending_none_seen += 1
            if pos.pending_none_seen >= 10:
                self._halt_alert(f"exit order {pos.pending_exit_oid} for {pos.symbol} unreadable "
                                 f"- check the Angel app",
                                 f"Exit order <code>{pos.pending_exit_oid}</code> for "
                                 f"<code>{pos.symbol}</code> cannot be read. No new exit is "
                                 f"sent over it. New entries halted.")
            return False
        pos.pending_none_seen = 0
        if not st["final"]:
            # The cancel is repeated less often the longer it waits (3 s, up to
            # 30 s): each one spends the order budget the stop needs (review O5).
            if now - pos.pending_exit_at >= min(30.0, max(3.0, (now - pos.pending_exit_since) / 4.0)):
                b.cancel_order(pos.pending_exit_oid, st["variety"] or "NORMAL")
                pos.pending_exit_at = now
                self.log(f"[intraday] exit order {pos.pending_exit_oid} for {pos.symbol} still "
                         f"{st['status'] or 'working'}; cancel sent")
            return False
        booked, booked_px = pos.pending_exit_booked, pos.pending_exit_booked_px
        new = st["filled"] - booked
        oid = pos.pending_exit_oid
        if new > 0:
            # Angel's average covers every fill of the order; the part booked
            # earlier went at booked_px, so the rest went at what makes the
            # whole order come out at the broker's average.
            avg = st["avg"] or float(px)
            late = (avg * st["filled"] - booked_px * booked) / new if booked else avg
            if not late > 0:
                late = avg
            self.log(f"[intraday] exit order {oid} for {pos.symbol} {st['status']} with "
                     f"{new} more filled @ {late:.2f} - booked")
            if new >= pos.qty:
                self._clear_pending(pos)
                self._save_open(force=True)
                self._book_fill(pos, pos.qty, late, reason,
                                self._exit_leg(pos, px), extra={"exit_order": "late_fill"})
                return True
            pos.pending_exit_booked, pos.pending_exit_booked_px = st["filled"], avg
            self._book_fill(pos, new, late, reason,
                            self._exit_leg(pos, px), extra={"exit_order": "late_fill"})
        # The order is over and some of the position is still open: nothing
        # more is sent for it until both books agree on what is held (O2).
        held = self._agreed_or_wait(pos)
        if held is None:
            return False
        return self._settle_rest(pos, held, short, reason, px,
                                 f"exit order {oid} {st['status']}")

    def _book_fill(self, pos, q, fill, reason, leg, extra=None):
        """Book q units of pos closed at fill. Returns the trade record once the
        whole position is gone, None after a partial fill."""
        from arb.costs import leg_cost
        eq = pos.opt == "EQ"
        short = eq and pos.side < 0
        inst = "EQ" if eq else "OPT"
        part_cost = (leg_cost(pos.entry, q, "SELL" if short else "BUY", inst, self.cm)
                     + leg_cost(fill, q, "BUY" if short else "SELL", inst, self.cm))
        part_points = (pos.entry - fill) if short else (fill - pos.entry)
        part_net = part_points * q - part_cost
        self.realised_pnl += part_net
        pos.exit_fails, pos.exit_retry_at = 0, 0.0      # it filled: no back-off (review O5)
        if hasattr(self.capital, "record"):
            try:
                self.capital.record(part_net, count=(q >= pos.qty))
            except TypeError:
                self.capital.record(part_net)
            except Exception as e:
                self.log(f"[capital] ledger write failed: {e}")
        pos.done_qty += q
        pos.done_gross += part_points * q
        pos.done_cost += part_cost
        pos.done_value += fill * q
        if q < pos.qty:
            left = pos.qty - q
            pos.margin *= left / pos.qty
            pos.qty = left
            self.log(f"[intraday] PARTIAL EXIT {pos.symbol}: {q} filled @ {fill:.2f}, "
                     f"{left} still open and managed")
            self._save_open(force=True)
            if getattr(self.L, "exit_safety_fixes", False):
                self._check_daily_halt()        # a partial is realised money too
            return None
        qty_total = pos.done_qty
        fill = pos.done_value / qty_total
        points = pos.done_gross / qty_total
        gross = pos.done_gross
        cost = pos.done_cost
        net = gross - cost
        pos.qty = qty_total

        self.book.remove(pos.trade_id)
        self._save_open(force=True)
        if net >= 0:
            self.wins += 1
            self.loss_streak = 0
            self.careful = False
        else:
            self.losses += 1
            self._on_loss()
            if pos.opt != "EQ":
                self._loss_side, self._loss_side_ts = pos.side, time.time()
        if reason in ("stop_loss", "structure_broken", "broker_stop") and net < 0:
            self._symbol_cooldown[pos.symbol] = time.time()

        rec = {
            "trade_id": pos.trade_id, "ts_ist": now_ist().isoformat(),
            "strategy": self._strategy_name(pos), "mode": getattr(self.broker, "mode", "paper"),
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
            "points": round(points, 2),
            "r_multiple": round(points / pos.risk_points, 2)
                          if pos.risk_points else 0.0,
            "gross_inr": round(gross, 2), "cost_inr": round(cost, 2),
            "net_inr": round(net, 2),
            "capital_inr": round(pos.margin or pos.entry * pos.qty, 2),
            # What left the account at entry, for the whole position: the premium
            # for an option, the margin for a leveraged stock. Not pos.margin,
            # which partial exits scale down to the last remaining piece.
            "invested_inr": round(pos.entry * pos.qty / (
                max(1.0, float(getattr(self.L, "stocks_leverage", 5.0))) if eq else 1.0), 2),
            "mae": round(pos.mae, 2), "mfe": round(pos.mfe, 2),
            "held_sec": round(time.time() - pos.opened_at, 1),
            "day_pnl": round(self.realised_pnl, 2),
            "capital": round(self.capital.capital, 2),
            "opportunity": {"underlying": pos.underlying, "expiry": pos.expiry,
                            "strategy": self._strategy_name(pos)},
            "legs": [dict(leg, fill_price=fill, filled=True)],
            **pos.detail,
            **(extra or {}),
        }
        self.records.append(rec)
        try:
            self.journal.record_trade(rec)
        except Exception as e:
            self.log(f"[intraday] journal write failed: {e}")

        tgt = self.capital.daily_target(self.L.daily_target_pct)
        self.log(f"[intraday] EXIT {pos.symbol} @ {fill:.2f} ({reason}) "
                 f"{points:+.2f}pt  net Rs {net:+,.0f}  "
                 f"day Rs {self.realised_pnl:+,.0f} / target Rs {tgt:,.0f}")
        if self.notifier:
            emoji = "✅" if net >= 0 else "🔴"
            pct = 100.0 * self.realised_pnl / self.capital.capital if self.capital.capital else 0
            self.notifier.send(
                f"{emoji} <b>EXIT</b> {pos.symbol}\n"
                f"{pos.entry:.2f} → {fill:.2f} ({points:+.2f} pt) · {reason}\n"
                f"Net <code>Rs {net:+,.0f}</code>\n"
                f"Day <code>Rs {self.realised_pnl:+,.0f}</code> "
                f"(<code>{pct:+.2f}%</code> of Rs {self.capital.capital:,.0f}, "
                f"target {self.L.daily_target_pct:g}%)")

        self._check_daily_halt()
        return rec

    def _check_daily_halt(self):
        if self.realised_pnl <= -abs(self.capital.capital * self.L.max_daily_loss_pct / 100.0):
            self.halted = True
            self.halt_reason = "daily loss limit"
            self.log(f"[intraday] HALTED: daily loss limit hit at Rs {self.realised_pnl:,.0f}")

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
        by_risk = int(risk_cap // max(plan.get("planned_risk_inr", plan["risk_inr"]), 1e-9))
        return max(0, min(want, by_cash, by_risk))

    def _costs_inside(self):
        return bool(getattr(self.L, "risk_includes_costs", False))

    def _extra_risk_points(self, px, spread, lot):
        """F5 (owner's 1 Oct 2026 review; rulebook section 5): what a stop-out
        costs on top of the premium stop, per unit - the round trip, and an exit
        that fills exit_slip_spreads spreads plus exit_slip_frac of the premium
        under the stop. On 1 Oct two stop-outs lost Rs 711 and Rs 730 against a
        Rs 699 cap because the Rs 97 round trip sat outside it."""
        L = self.L
        return (self._round_trip_points(px, lot)
                + float(getattr(L, "exit_slip_spreads", 1.0)) * float(spread or 0.0)
                + float(getattr(L, "exit_slip_frac", 0.01)) * float(px))

    # ── entries ─────────────────────────────────────────────────────────────
    def _gates(self):
        """Everything that must be true before an entry is even considered."""
        L = self.L
        if self.halted:
            return f"halted: {self.halt_reason}"
        if self._pending_entries:
            return "entry_pending"              # the last entry may yet fill (review O4)
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

    def _closed_rows(self, name, rows):
        """Decide on COMPLETED bars only, and only just after one closes.

        Owner's 1 Oct 2026 review: live passed the still-forming bar to the
        playbooks, so a rejection wick 23 seconds into a minute was traded as a
        rejection candle, and tools/backtest.py - which only ever sees closed
        bars - had tested a different rule. With decide_on_closed_bars the
        forming bar is dropped, and the decision is taken only within
        entry_window_sec of the bar's close: the confirmation is the close, the
        entry is the next tick, and a setup not taken then is not chased later.
        Returns (rows, reason); reason is "" when this is the moment to act.
        """
        L = self.L
        if not getattr(L, "decide_on_closed_bars", False) or not rows:
            return rows, ""
        live = (getattr(self.candles, "live", None) or {}).get(name) if self.candles else None
        if live and rows[-1][0] == live[0]:
            rows = rows[:-1]
        if not rows:
            return rows, "no_closed_bar"
        from arb.candles import SECONDS
        secs = SECONDS.get(getattr(self.candles, "interval", "1m"), 60)
        try:
            closed_at = datetime.fromisoformat(rows[-1][0]).timestamp() + secs
        except ValueError:
            return rows, ""
        if time.time() - closed_at > float(getattr(L, "entry_window_sec", 15.0)):
            return rows, "waiting_for_bar_close"
        return rows, ""

    def _late_or_trapped(self, rows, side, spot):
        """F1's chase guard and the F2/F3 filters, on completed bars. "" = go."""
        L = self.L
        a = atr(rows[:-1], period=L.atr_period) if len(rows) > L.atr_period + 1 else 0.0
        chase = float(getattr(L, "max_chase_atr", 0.0) or 0.0)
        if getattr(L, "decide_on_closed_bars", False) and chase > 0 and a > 0 \
                and rows and abs(spot - rows[-1][4]) > chase * a:
            return "price_moved_since_close"
        n = float(getattr(L, "max_run_atr", 0.0) or 0.0)
        if n > 0 and run_in_direction(rows, side, bars=int(getattr(L, "run_bars", 30)),
                                      period=L.atr_period) >= n:
            return "move_already_run"
        if getattr(L, "sweep_against_veto", False):
            why = swept_against(rows, side)
            if why:
                return f"against_{why}"
        return ""

    def _same_idea_blocked(self, side):
        """F4: after a losing exit, no new trade on the same side on any index
        for same_side_cooldown_min - NIFTY, BANKNIFTY and SENSEX are one market."""
        m = float(getattr(self.L, "same_side_cooldown_min", 0.0) or 0.0)
        return bool(m) and side == self._loss_side and \
            time.time() - self._loss_side_ts < 60.0 * m

    def hunt(self):
        # The stock scan runs on its own clock, whatever the gates say: the
        # dashboard should show what is in play even while a position is open.
        desk = self.stocks
        if desk is not None and desk.due():
            try:
                desk.pick(index_moves=self._index_moves(),
                          cost_ratio=self._stock_cost_ratio)
            except Exception as e:
                self.log(f"[stocks] scan failed: {e!r}")
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
            rows, wait = self._closed_rows(und, rows)
            if wait:
                # Waiting for the next close is the normal state between bars,
                # not a refusal: counting it buried the real reasons on the
                # dashboard (about 3,000 a day against a few hundred real ones).
                if wait != "waiting_for_bar_close":
                    self._note(wait)
                continue
            st = analyse(rows, strength=self.L.swing_strength,
                         min_bars=self.L.min_structure_bars)
            self.structures[und] = st
            self.evaluated_at[und] = time.time()

            # Regime decides whether the tape is moving enough for any read to
            # survive its own round trip, and whether we are in a part of the
            # session where continuation is a real effect rather than bid-ask
            # bounce. Cheapest refusal available, so it runs first.
            veto = regime_gate(rows, self.L, name=und)
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
            self.verdict_at[und] = time.time()
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
            n4 = int(getattr(self.L, "trend_4h_bars", 0) or 0)
            if n4:
                tr = trend_4h(self.candles.get(und, limit=3000) if self.candles else [], n4)
                if tr != v.side:
                    self._note("4h_trend_unknown" if tr == 0 else "against_4h_trend")
                    continue

            late = self._late_or_trapped(rows, v.side, spot)
            if late:
                self._note(late)
                continue
            if self._same_idea_blocked(v.side):
                self._note("same_side_after_loss")
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
            max_delta_fn = None
            if max_delta is not None and self._costs_inside():
                lot = max(cv.lot_size, 1)
                max_delta_fn = lambda px, spr, r=idx_risk, lot=lot: (
                    cap_inr / lot - self._extra_risk_points(px, spr, lot)) / r
            cand = self.selector.select(cv, spot, v.side, available,
                                        max_age_ms=400.0, min_depth_mult=1.0,
                                        max_delta=max_delta, max_delta_fn=max_delta_fn)
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
            # What the premium really moves per index point, and whether the
            # other side of the chain believes the move (the owner's rules,
            # research/PAPERS.md 0h).
            eff_delta, eff_note, cross = self._option_flow(b, cv, cand, und, rows)
            mode = str(getattr(self.L, "cross_side_mode", "grade"))
            if cross is not None and mode == "gate" and (
                    cross.against or (getattr(self.L, "cross_side_veto_crush", False)
                                      and cross.detail.get("vol_crush"))):
                self._note(f"cross_side:{cross.verdict}")
                continue
            if cross is not None and cross.confirms and mode in ("grade", "gate"):
                v.confirmations.append(("cross_side", cross.note))
                regrade(v, self.L)
            # The deepest setup's own invalidation is a better stop than the
            # generic swing: it is the price that particular model says should
            # not trade again. plan_trade ignores it if it sits on the wrong
            # side of spot.
            lead = v.setups[0] if v.setups else None
            plan = plan_trade(cand, st, v.side, self.L, cost_pts,
                              invalidation=(lead.invalidation if lead else None),
                              max_risk_inr=cap_inr,
                              extra_risk_points=(self._extra_risk_points(
                                  cand.ask, cand.detail.get("spread", 0.0), cand.lot_size)
                                  if self._costs_inside() else 0.0),
                              atr=atr(rows[:-1], period=self.L.atr_period))
            if plan is None:
                self._note("no_viable_stop_or_target")
                continue
            if plan.get("planned_risk_inr", plan["risk_inr"]) > cap_inr + 1e-6:
                self._note("risk_too_large")
                continue
            lots = self._lots_for(v.grade, cand, plan)
            if lots <= 0:
                self._note("risk_too_large")
                continue
            plan["index_entry"] = spot
            plan["entry_bar"] = rows[-1][0] if rows else ""
            plan["reward_delta"] = eff_delta
            plan["premium_per_point"] = eff_note
            plan["cross_side"] = cross.as_dict() if cross is not None else None
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
                # At the premium's measured points-per-point, not the chain
                # delta: a call on a rally earns less than its delta says.
                reward_prem = reach * eff_delta
                if reward_prem - cost_pts < self.L.min_net_points:
                    self._note("move_too_small_for_costs")
                    continue
                plan["reward_points"] = reward_prem
                plan["reward_inr"] = reward_prem * cand.lot_size
            if self.enter(cand, plan, st, zone, v, lots=lots):
                key = (und, lead_name, v.side)
                self.playbook_count[key] = self.playbook_count.get(key, 0) + 1
            return
        # Nothing on the indices: the stocks in play get the same rules.
        if desk is not None and desk.active:
            self._hunt_stocks(available)

    def _unknown_entry(self, leg, symbol, adopt=None, side=1):
        """An entry whose status could not be read may still have filled, and a
        position the engine does not know about has no stop. Stop opening new
        ones until a human has looked. With adopt_unknown_entry (options), what
        the broker shows held is taken on as a position first, so the software
        stop manages it in the meantime."""
        if leg.get("broker_status") != "unknown":
            return
        fixes = getattr(self.L, "exit_safety_fixes", False)
        if fixes and hasattr(self.broker, "order_rows"):
            # Followed until it is known: by its own status once it has an id,
            # else from both of the broker's books. On the position book alone
            # a BUY that filled while the book lagged was dropped - no position,
            # no halt, no alert (review O1, O3, O4, 2 Oct 2026).
            rec = self._pend_entry(leg, symbol, adopt, side)
            try:
                state = self._follow_entry(rec)
            except Exception as e:              # never out of hunt(); manage() looks again
                self.log(f"[intraday] pending entry {symbol}: {e!r}")
                state = "unreadable"
            if state == "working":
                self._halt_alert(f"entry order for {symbol} may still be working at Angel "
                                 f"- check the Angel app",
                                 f"Entry in <code>{symbol}</code> could not be confirmed and "
                                 f"may still be working (cancel sent). New entries halted.")
            elif state == "unreadable":
                self._halt_alert(f"entry status unknown for {symbol} (the broker's books cannot "
                                 f"be read) - check the Angel app",
                                 f"Entry in <code>{symbol}</code> could not be confirmed and the "
                                 f"broker's books cannot be read. New entries halted.")
            return
        working = False
        if fixes and leg.get("final") is False and hasattr(self.broker, "cancel_working"):
            # The order may still be live at Angel (the place call raised, or
            # its status could not be read) - as a DAY order it could fill an
            # hour from now. Pull anything working in the symbol first.
            rows = self.broker.cancel_working([symbol])
            working = rows is None or bool(rows)
            if working:
                self.log(f"[intraday] entry order for {symbol} may still be working: "
                         + ("order book unreadable" if rows is None else
                            f"cancel sent for {', '.join(r['oid'] for r in rows)}"))
        held = self.broker.net_position(symbol) if hasattr(self.broker, "net_position") else None
        if held == 0:
            if working:
                self._halt_alert(f"entry order for {symbol} may still be working at Angel "
                                 f"- check the Angel app",
                                 f"Entry in <code>{symbol}</code> could not be confirmed and "
                                 f"may still be working (cancel sent). New entries halted.")
            return
        self._take_on(symbol, held, adopt)

    def _take_on(self, symbol, held, adopt, note=""):
        """The broker shows `held` from an entry the engine did not see fill:
        with adopt_unknown_entry an option is adopted under the software stop;
        either way new entries halt until a human has looked."""
        adopted = False
        if adopt is not None and held is not None and held > 0 \
                and getattr(self.L, "adopt_unknown_entry", False):
            try:
                adopted = adopt(held)
            except Exception as e:
                self.log(f"[intraday] could not adopt {symbol}: {e!r}")
        shows = f"broker shows {held}" + (f", {note}" if note else "")
        self.halted = True
        self.halt_reason = f"entry status unknown for {symbol} ({shows}) - check the Angel app"
        if adopted:
            self.halt_reason += "; adopted under software stop"
        self.log(f"[intraday] HALTED: {self.halt_reason}")
        if self.notifier:
            self.notifier.send(f"🛑 <b>CHECK THE ANGEL APP</b>\nEntry in <code>{symbol}</code> could not "
                               f"be confirmed ({shows}). New entries halted; "
                               + (f"adopted under software stop." if adopted else
                                  f"MIS square-off will close anything left."))

    def _entry_working(self, leg, symbol, adopt=None, side=1):
        """exit_safety_fixes: an entry still working at Angel after its cancel
        (a DAY order) can fill later into a position nobody manages."""
        if getattr(self.L, "exit_safety_fixes", False) and leg.get("final") is False \
                and leg.get("broker_status") != "unknown":
            if leg.get("order_id") and hasattr(self.broker, "cancel_order"):
                self.broker.cancel_order(leg["order_id"], "NORMAL")
            self._halt_alert(f"entry order {leg.get('order_id')} for {symbol} still working at "
                             f"Angel after its cancel - check the Angel app",
                             f"Entry order <code>{leg.get('order_id')}</code> for "
                             f"<code>{symbol}</code> is still working after its cancel. "
                             f"New entries halted.")
            if hasattr(self.broker, "order_state"):
                # One cancel and a halt, then nothing looked at it again: a
                # later fill sat at Angel with no software stop. manage() now
                # follows it to the end (review O4, 2 Oct 2026).
                self._pend_entry(leg, symbol, adopt, side)

    def _pend_entry(self, leg, symbol, adopt, side):
        """Remember an entry order not known to be over at Angel: manage()
        looks at it every ENTRY_POLL_SEC until it is (review O4)."""
        rec = {"symbol": symbol, "oid": str(leg.get("order_id") or ""),
               "uid": str(leg.get("uid") or ""), "side": "BUY" if side > 0 else "SELL",
               "sign": 1 if side > 0 else -1, "adopt": adopt,
               "booked": int(leg.get("qty") or 0) if leg.get("filled") else 0,
               "since": time.time(), "next_at": 0.0, "cancel_at": 0.0,
               "clean": 0, "alerted": False}
        self._pending_entries.append(rec)
        return rec

    def _drop_entry(self, rec):
        self._pending_entries = [r for r in self._pending_entries if r is not rec]

    def _poll_entries(self):
        """Look again at each pending entry order, each at most every
        ENTRY_POLL_SEC; never raises out of manage() (review O4)."""
        now = time.time()
        for rec in list(self._pending_entries):
            if 0.0 < rec["next_at"] - now <= self.ENTRY_POLL_SEC:
                continue
            try:
                self._follow_entry(rec)
            except Exception as e:
                self.log(f"[intraday] pending entry {rec['symbol']}: {e!r}")

    def _engine_held(self, symbol):
        """What the engine itself holds in symbol, signed like the broker's net."""
        return sum(-p.qty if p.opt == "EQ" and p.side < 0 else p.qty
                   for p in self.book.snapshot() if p.symbol == symbol)

    def _follow_entry(self, rec):
        """One look at a pending entry order. "done" once it is over: what it
        filled is taken on (_take_on) and the record dropped. Otherwise
        "working" (cancel re-sent), "unreadable", or "waiting" - with no id
        and nothing seen, it is believed refused only after two clean reads
        ENTRY_POLL_SEC apart, as an exit is."""
        b, now, sym = self.broker, time.time(), rec["symbol"]
        rec["next_at"] = now + self.ENTRY_POLL_SEC
        if not rec["oid"] and rec["uid"]:
            st = b.order_state(rec["uid"], "")
            if st and st["oid"]:
                rec["oid"] = st["oid"]
        if rec["oid"]:
            st = b.order_state(rec["uid"], rec["oid"])
            if st is None:
                self._entry_stall(rec, now, "its status cannot be read")
                return "unreadable"
            if not st["final"]:
                if now - rec["cancel_at"] >= min(30.0, max(3.0, (now - rec["since"]) / 4.0)):
                    b.cancel_order(rec["oid"], st["variety"] or "NORMAL")
                    rec["cancel_at"] = now
                self._entry_stall(rec, now, f"still {st['status'] or 'working'} after its cancel")
                return "working"
            self._drop_entry(rec)
            more = st["filled"] - rec["booked"]
            if more > 0:
                self.log(f"[intraday] entry order {rec['oid']} for {sym} {st['status']} with "
                         f"{more} filled that the engine did not see")
                self._take_on(sym, rec["sign"] * more, rec["adopt"],
                              note=f"order {rec['oid']} {st['status']}")
            return "done"
        rows = b.order_rows(sym)
        work = next((r for r in rows or [] if not r["final"] and r["side"] == rec["side"]), None)
        if work:
            rec["oid"], rec["uid"], rec["cancel_at"] = work["oid"], work["uid"], now
            b.cancel_order(work["oid"], work["variety"] or "NORMAL")
            self.log(f"[intraday] entry order for {sym} found working ({work['oid']}); cancel sent")
            return "working"
        held = b.net_position(sym)
        if rows is None or held is None:
            self._entry_stall(rec, now, "the order or position book cannot be read")
            return "unreadable"
        mine = self._engine_held(sym)
        ob, held = b.signed_net(rows) - mine, held - mine
        if ob != held and ob * rec["sign"] <= 0:
            self._halt_alert(f"entry status unknown for {sym} (position and order books "
                             f"disagree) - check the Angel app",
                             f"Entry in <code>{sym}</code> could not be confirmed: the position "
                             f"book shows {held} and the order book {ob}. New entries halted.")
            return "waiting"
        if ob:
            # The order book shows a fill before the position book does (O3).
            self._drop_entry(rec)
            self._take_on(sym, ob, rec["adopt"], note="" if ob == held else f"position book {held}")
            return "done"
        rec["clean"] += 1
        if rec["clean"] >= 2:
            self._drop_entry(rec)
            return "done"
        return "waiting"

    def _entry_stall(self, rec, now, what):
        """One alert for an entry order that stays unsettled (review O4, O6)."""
        if rec["alerted"] or now - rec["since"] < self.PENDING_ALERT_SEC:
            return
        rec["alerted"] = True
        oid = rec["oid"] or "not found"
        self.log(f"[intraday] entry order {oid} for {rec['symbol']} {what} after "
                 f"{now - rec['since']:.0f} s - check the Angel app")
        if self.notifier:
            self.notifier.send(f"⚠️ <b>CHECK THE ANGEL APP</b>\nEntry order <code>{oid}</code> for "
                               f"<code>{rec['symbol']}</code>: {html.escape(what)}. Whatever it "
                               f"fills is taken on when it is seen; no new entry until then.")

    def _adopt_more(self, pos, more):
        """More of a part-filled entry filled after its cancel: the position it
        opened grows by that much, under the same stop (review O4)."""
        if more <= 0 or not self._in_book(pos):
            return False
        pos.qty += int(more)
        pos.detail["adopted_more"] = int(pos.detail.get("adopted_more") or 0) + int(more)
        self._save_open(force=True)
        self.log(f"[intraday] ADOPTED {more} more {pos.symbol} into trade {pos.trade_id}: "
                 f"now x{pos.qty}")
        return True

    def _adopt_entry(self, cand, plan, structure, zone, verdict, held):
        """The position an unknown-status entry turned out to be, at the quote
        (the real fill is not known), so the software stop has something to
        manage."""
        lots = max(1, int(held) // max(1, int(cand.lot_size)))
        pos = self._option_position(cand, plan, structure, zone, verdict, lots,
                                    int(held), float(cand.ask))
        pos.detail.update(adopted=True, entry_estimated=True)
        self.book.add(pos)
        self._save_open(force=True)
        self.log(f"[intraday] ADOPTED {cand.symbol} x{held} @ ~{pos.entry:.2f} (status unknown; "
                 f"stop {pos.stop:.2f})")
        return True

    # ── stocks (arb/stockdesk.py) ───────────────────────────────────────────
    @staticmethod
    def _strategy_name(pos):
        if pos.opt == "EQ":
            return "INTRADAY_EQ_" + ("LONG" if pos.side > 0 else "SHORT")
        return f"INTRADAY_{pos.opt}"

    def _index_moves(self):
        """Each index's move since the previous close, for relative strength."""
        out = {}
        for und in {b.underlying for b in self.bindings}:
            rows = self.candles.get(und, limit=400) if self.candles else []
            today = now_ist().strftime("%Y-%m-%d")
            prev = [r for r in rows if r[0][:10] < today]
            px = self._spot(und)
            if prev and px > 0:
                out[und] = px / prev[-1][4] - 1
        return out

    def _stock_cost_ratio(self, sym):
        """The scan's tradability figure for one stock (arb/scanner.py:
        scan_cost_ratio) from its live quote and one-minute bars. None until it
        has a two-sided quote and enough bars for an ATR."""
        slot = self.stocks.slots.get(sym) if self.stocks is not None else None
        if slot is None or not self.candles:
            return None
        store = self.feed.store
        bid, ask = float(store.bid[slot]), float(store.ask[slot])
        rows = self.candles.get(sym, limit=self.L.atr_period + 2)
        # Stocks only have live bars from the session's start, so at the 09:30
        # first pick there are ~15: use what exists rather than wait for 20.
        period = min(int(self.L.atr_period), len(rows) - 2)
        if period < 10:
            return None
        a = atr(rows[:-1], period=period)
        return scan_cost_ratio(bid, ask, a, self.capital.capital, self.L, self.cm)

    def _hunt_stocks(self, available):
        """The index decision layer, unchanged, on the stocks in play.

        Cash shares, intraday: a long is bought, a short is sold first. The
        size is the fewest of (risk cap / stop distance) and (free capital x
        leverage / price), and the trade must pay its round trip - fees both
        ways, a tick of slippage each way - by stocks_min_net_inr at the target.
        """
        L = self.L
        store = self.feed.store
        cap_inr = self.capital.capital * L.max_risk_pct_per_trade / 100.0
        lev = float(getattr(L, "stocks_leverage", 5.0))
        for pick in list(self.stocks.active):
            sym = pick.symbol
            info = self.stocks.universe.get(sym) or {}
            slot = self.stocks.slots.get(sym)
            if slot is None:
                continue
            rows = self.candles.get(sym, limit=400) if self.candles else []
            self.evaluated_at[sym] = time.time()
            rows, wait = self._closed_rows(sym, rows)
            if wait:
                if wait != "waiting_for_bar_close":
                    self._note(f"stock:{wait}")
                continue
            if len(rows) < L.min_structure_bars:
                self._note("stock_warming_up")
                continue
            veto = regime_gate(rows, L, name="STOCKS")
            if veto:
                self._note(f"stock:{veto}")
                continue
            px = float(store.ltp[slot]) or rows[-1][4]
            v = evaluate(rows, px, L, strike_step=0.0)
            self.verdicts[sym] = v
            self.verdict_at[sym] = time.time()
            if not v.tradable:
                self._note("stock:" + (v.veto or ("no_setup" if not v.setups else "confluence")))
                continue
            if v.side < 0 and not getattr(L, "stocks_allow_short", True):
                self._note("stock:short_disabled")
                continue
            if self.careful and not grade_at_least(v.grade, getattr(L, "grade_after_streak", "B")):
                self._note("grade_too_low_after_streak")
                continue
            if time.time() - self._symbol_cooldown.get(info.get("eq_symbol", sym), -1e9) \
                    < L.symbol_cooldown_sec:
                self._note("symbol_cooldown")
                continue
            st = analyse(rows, strength=L.swing_strength, min_bars=L.min_structure_bars)
            self.stock_structures[sym] = st
            lead = v.setups[0] if v.setups else None
            inv = structural_invalidation(st, v.side, lead.invalidation if lead else None)
            ref = st.closes[-1] if st.closes else px
            risk = abs(ref - inv) if inv else 0.0
            a = atr(rows[:-1], period=L.atr_period)
            if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
                self._note("stock:no_viable_stop")
                continue
            bid, ask = float(store.bid[slot]), float(store.ask[slot])
            if bid <= 0 or ask <= 0:
                self._note("stock:no_quote")
                continue
            fill_px = ask if v.side > 0 else bid
            qty = min(int(cap_inr // risk), int(max(0.0, available) * lev // fill_px))
            from arb.costs import leg_cost

            def round_trip(q):
                return (leg_cost(fill_px, q, "BUY", "EQ", self.cm)
                        + leg_cost(fill_px, q, "SELL", "EQ", self.cm)
                        + 2 * self.cm.slippage_points() * q + (ask - bid) * q)
            if self._costs_inside() and qty > 0:
                # The largest size whose stop-out, costs included, fits the cap.
                # Cost rises with size, so a bisection finds it exactly.
                lo_q, hi_q = 0, qty
                while lo_q < hi_q:
                    mid = (lo_q + hi_q + 1) // 2
                    if risk * mid + round_trip(mid) <= cap_inr:
                        lo_q = mid
                    else:
                        hi_q = mid - 1
                qty = lo_q
            if qty <= 0:
                self._note("stock:risk_too_large")
                continue
            rt = round_trip(qty)
            if rt > L.max_cost_frac_of_risk * risk * qty:
                self._note("stock:cost_too_large_for_stop")
                continue
            tgt = trailing.initial_target(rows, v.side, px, risk, L, 0.0)
            reach = abs(tgt - px) if tgt else L.reward_risk * risk
            if reach < float(getattr(L, "min_target_r", 1.0)) * risk:
                self._note("stock:target_not_achievable")
                continue
            if reach * qty - rt < float(getattr(L, "stocks_min_net_inr", 100.0)):
                self._note("stock:move_too_small_for_costs")
                continue
            if self._enter_stock(sym, info, slot, v, st, px, inv, risk, tgt, qty, fill_px, rt, rows, pick):
                return

    def _enter_stock(self, sym, info, slot, v, st, px, inv, risk, tgt, qty, quote, rt, rows, pick):
        L = self.L
        side = "BUY" if v.side > 0 else "SELL"
        leg = {"symbol": info.get("eq_symbol", sym), "token": info.get("eq_token"),
               "side": side, "price": quote, "qty": qty, "instrument": "EQ",
               "exchange": "NSE", "underlying": sym, "strike": 0.0, "opt": "EQ",
               "depth": qty, "age_ms": 0.0, "market": bool(getattr(L, "market_orders", False))}
        if getattr(L, "order_tags", False) or getattr(L, "exit_safety_fixes", False):
            leg["kind"] = "E"                   # an entry: held to its share of the order budget (O5)
        ticket = _Ticket([leg], qty, f"INTRADAY_EQ", f"{side.lower()} {sym}", "")
        try:
            legs, _ = self.broker.place_structure(ticket, 1)
        except Exception as e:
            self.log(f"[intraday] stock entry failed: {e!r}")
            self._note("entry_error")
            return False
        self._entry_working(legs[0], leg["symbol"], side=v.side)
        if not legs[0].get("filled"):
            self._unknown_entry(legs[0], leg["symbol"], side=v.side)
            self._note("entry_not_filled")
            return False
        fill = float(legs[0]["fill_price"])
        qty = int(legs[0].get("qty") or qty)     # what the broker actually filled
        self.trade_id += 1
        self.trades_today += 1
        self.last_trade_ts = time.time()
        # Premium-space fields carry the stock in the position's own terms: a
        # short is mirrored about its entry (see _mark_of), so the stop sits
        # below entry for both sides and the target is the index-style level.
        pos = Position(
            trade_id=self.trade_id, symbol=info.get("eq_symbol", sym),
            token=str(info.get("eq_token")), strike=0.0, opt="EQ", side=v.side,
            qty=qty, entry=fill, stop=fill - risk, target=fill * 100.0,
            risk_points=risk, index_invalidation=inv, opened_at=time.time(),
            underlying=sym, segment="NSE", cost_points=rt / qty,
            detail={"entry_reason": v.reason, "confirmations": v.names(),
                    "playbooks": v.plays(), "setup_chain": v.chain(),
                    "grade": v.grade, "reversal": v.reversal,
                    "structure": st.last_event, "instrument": "EQ",
                    "direction": "long" if v.side > 0 else "short",
                    "in_play": {"score": round(pick.score, 3), "rvol": round(pick.rvol, 2),
                                "atr_pct": round(100 * pick.atr_pct, 2),
                                "move_pct": round(100 * pick.move, 2),
                                "rs": round(100 * pick.rs, 2), "reasons": pick.reasons},
                    "cost_round_trip_inr": round(rt, 2)})
        pos.margin = fill * qty / max(1.0, float(getattr(L, "stocks_leverage", 5.0)))
        pos.index_entry, pos.index_risk, pos.index_target = px, risk, float(tgt or 0.0)
        pos.delta, pos.entry_bar = 1.0, rows[-1][0] if rows else ""
        self.book.add(pos)
        self._save_open(force=True)
        word = "BUY" if v.side > 0 else "SHORT"
        self.log(f"[intraday] ENTRY {word} {sym} x{qty} @ {fill:.2f} = Rs {fill * qty:,.0f} "
                 f"(margin Rs {pos.margin:,.0f}) | grade {v.grade} {' + '.join(v.plays())} | "
                 f"stop {inv:.2f} target {pos.index_target:.2f} | risk Rs {risk * qty:,.0f} "
                 f"cost Rs {rt:,.0f} | in play: rvol {pick.rvol:.1f}, ATR {100 * pick.atr_pct:.1f}%")
        if self.notifier:
            self.notifier.send(
                f"🔵 <b>{word}</b> <code>{sym}</code> (stock, intraday)\n"
                f"Entry <code>{fill:.2f}</code> x {qty} = <code>Rs {fill * qty:,.0f}</code>\n"
                f"Why <code>{' + '.join(v.plays())}</code> · grade <code>{v.grade}</code>\n"
                f"Stop <code>{inv:.2f}</code> · Target <code>{pos.index_target:.2f}</code>\n"
                f"Risk <code>Rs {risk * qty:,.0f}</code> · cost <code>Rs {rt:,.0f}</code>")
        return True

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

    def _option_position(self, cand, plan, structure, zone, verdict, lots, qty, fill):
        """The Position for an option entry filled at `fill` (enter, _adopt_entry)."""
        L = self.L
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
                    "premium_per_point": plan.get("premium_per_point"),
                    "cross_side": plan.get("cross_side"),
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
            est = abs(pos.index_target - pos.index_entry) \
                * float(plan.get("reward_delta") or pos.delta) \
                if pos.index_target > 0 else 0.0
            pos.detail["index_target"] = round(pos.index_target, 2)
            pos.detail["target_premium_est"] = round(fill + est, 2) if est else None
        return pos

    def enter(self, cand, plan, structure, zone, verdict=None, lots=1):
        L = self.L
        leg = {"symbol": cand.symbol, "token": cand.token, "side": "BUY",
               "price": cand.ask, "qty": cand.lot_size, "instrument": "OPT",
               "exchange": cand.segment, "underlying": cand.underlying,
               "strike": cand.strike, "opt": cand.opt, "depth": cand.depth,
               "age_ms": cand.age_ms, "market": bool(getattr(L, "market_orders", False))}
        if getattr(L, "order_tags", False) or getattr(L, "exit_safety_fixes", False):
            leg["kind"] = "E"                   # an entry: held to its share of the order budget (O5)
        ticket = _Ticket([leg], cand.lot_size, f"INTRADAY_{cand.opt}",
                         f"buy {cand.symbol}", cand.expiry)
        lots = max(1, int(lots))
        try:
            legs, errors = self.broker.place_structure(ticket, lots)
        except Exception as e:
            self.log(f"[intraday] entry failed: {e!r}")
            self._note("entry_error")
            return False

        def adopt(held):
            return self._adopt_entry(cand, plan, structure, zone, verdict, held)
        if not legs[0].get("filled"):
            self._entry_working(legs[0], cand.symbol, adopt=adopt)
            self._unknown_entry(legs[0], cand.symbol, adopt=adopt)
            self._note("entry_not_filled")
            return False
        qty = int(legs[0].get("qty") or cand.lot_size * lots)

        fill = float(legs[0]["fill_price"])
        pos = self._option_position(cand, plan, structure, zone, verdict, lots, qty, fill)
        self.book.add(pos)
        self._save_open(force=True)
        # Part filled and still working after its cancel: more of it may fill (O4).
        self._entry_working(legs[0], cand.symbol,
                            adopt=lambda more: self._adopt_more(pos, more) or adopt(more))

        why = " + ".join(verdict.plays()) if verdict and verdict.plays() \
            else structure.last_event
        grade = verdict.grade if verdict else ""
        self.log(f"[intraday] ENTRY BUY {cand.symbol} @ {fill:.2f} x{qty} "
                 f"= Rs {fill * qty:,.0f} | grade {grade} {why} "
                 f"delta {cand.delta:.2f} (moves {plan.get('reward_delta') or cand.delta:.2f}/pt) "
                 f"offset {cand.offset:+d} | cross-side "
                 f"{(plan.get('cross_side') or {}).get('verdict', 'off')} | "
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
        # Never leave the desk with a position on it - unless this is a paper
        # restart (./run.sh sends SIGUSR1): then the position is handed to the
        # next process, which restores it from open_positions.json, instead of
        # being cut by the restart. Live always squares off.
        if self.handover and getattr(self.broker, "mode", "paper") == "paper" \
                and self.book.count():
            self._save_open(force=True)
            self.log(f"[intraday] restart: {self.book.count()} open position(s) "
                     f"handed to the next engine, not squared off")
        elif getattr(self.L, "exit_safety_fixes", False):
            self._square_off()
        else:
            for pos in self.book.snapshot():
                self.close(pos, "squareoff")
        self._running.clear()

    def _square_off(self):
        """exit_safety_fixes: run()'s square-off keeps trying for
        SQUAREOFF_RETRY_SEC - a close() held by a pending exit or a back-off
        used to end it after one pass - and then says what is left at the
        broker, in the log and on Telegram (review O6, 2 Oct 2026)."""
        end = time.time() + self.SQUAREOFF_RETRY_SEC
        while True:
            try:
                if self._pending_entries:
                    self._poll_entries()
                for pos in self.book.snapshot():
                    if not pos.exit_blocked:
                        self.close(pos, "squareoff")
            except Exception as e:
                self.log(f"[intraday] square-off error: {e!r}")
            left = [p for p in self.book.snapshot() if not p.exit_blocked]
            if not left or not (0.0 <= end - time.time() <= self.SQUAREOFF_RETRY_SEC):
                break
            time.sleep(0.5)
        left = self.book.snapshot()
        if left or self._pending_entries:
            what = ", ".join(f"{p.symbol} x{p.qty}" + (f" ({p.exit_blocked})" if p.exit_blocked else "")
                             for p in left)
            if self._pending_entries:
                what += ("; " if what else "") + "entry order(s) not settled in " + \
                    ", ".join(r["symbol"] for r in self._pending_entries)
            self.log(f"[intraday] SQUARE-OFF INCOMPLETE: still open at the broker: {what} "
                     f"- close it in the Angel app")
            self._save_open(force=True)
            if self.notifier:
                self.notifier.send(f"🛑 <b>SQUARE-OFF INCOMPLETE</b>\nStill open: "
                                   f"<code>{html.escape(what)}</code>. Close it in the Angel app "
                                   f"(MIS auto square-off is the last resort).")

    def start(self, **kw):
        self._thread = threading.Thread(target=self.run, kwargs=kw,
                                        name="intraday", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=None):
        """Stop the loop and wait for its square-off. With exit_safety_fixes
        the wait covers SQUAREOFF_RETRY_SEC plus one slow exit (confirm
        timeout, cancel, 3 s more, paced reads) per open position: 10 s let
        run_bot exit mid square-off on a daemon thread (review O11)."""
        if timeout is None:
            timeout = 10.0
            if getattr(self.L, "exit_safety_fixes", False):
                slow = float(getattr(self.broker, "confirm_timeout", 6.0) or 6.0) + 9.0
                timeout = max(45.0, self.SQUAREOFF_RETRY_SEC + slow * max(1, self.book.count()))
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
            "stocks": self.stocks.summary() if self.stocks is not None else None,
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
