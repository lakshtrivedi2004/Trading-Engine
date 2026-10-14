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
from arb.confluence import Verdict, evaluate, grade_at_least, regrade
from arb.playbooks import Setup, unknown as unknown_playbooks
from arb.regime import (atr, atr_ratio, premium_gate, regime_gate, run_in_direction,
                        session_phase, swept_against, trend_4h)
from arb.scanner import scan_cost_ratio
from arb.config import stock_risk_pct
from arb.smc import BEAR, BULL, analyse
from arb.costs import leg_cost, per_unit_cost_points
from arb.fileio import write_json
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


class _LhmFrame:
    """What enter() and plan_trade read off a Structure, for a last_half_hour
    entry: the decision bar's close and the rule's own index stop. The
    momentum rule has no swing structure; this is its whole frame."""
    def __init__(self, close, stop, reason):
        self.closes = [float(close)]
        self._stop = float(stop)
        self.reason = reason
        self.last_event = "last_half_hour"

    def invalidation(self, side):
        return self._stop


class IntradayEngine:
    # exit_safety_fixes timings (review O4-O6, 2 Oct 2026).
    EXIT_BACKOFF = (0.5, 5.0)       # first and longest wait after a refused exit
    EXIT_MISS_GAP = 1.0             # wait after an exit Angel took that did not fill (O-ADV-3)
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
        self._unaffordable_said = set()  # (day, underlying) already alerted
        # Live only (set by run_bot): () -> (funds, detail) from the account.
        # Sizing then trades min(configured, the account's money now).
        self.funds_reader = None
        self._funds_read_at = 0.0
        self._funds_fail_said = False
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
                opening = getattr(capital_book, "opening_capital", capital_book.capital)
                limit = abs(opening * self.L.max_daily_loss_pct / 100.0)
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
        self._resume_loss_state(recs)
        self._resume_discipline(recs)

    def _resume_discipline(self, recs):
        """The loss-streak pause, the careful-after-a-streak grade floor and the
        per-playbook daily cap, from today's journal. Kept only in memory, a
        restart cleared all three: a paused desk resumed at once and a playbook
        at its cap could trade again (review, 13 Oct 2026). One trade may have
        several journal rows (partial exits); each counts once, by trade_id."""
        L = self.L
        trades = {}
        for r in recs:
            t = trades.setdefault(r.get("trade_id"), dict(r, net=0.0))
            t["net"] += float(r.get("net_inr") or 0.0)
            t["ts_ist"] = r.get("ts_ist") or t.get("ts_ist")
        cap = int(getattr(L, "max_consecutive_losses", 0) or 0)
        mins = float(getattr(L, "loss_streak_pause_min", 30.0))
        for t in trades.values():
            side = {"CE": BULL, "PE": BEAR}.get(str(t.get("opt") or "").upper()) \
                or {"long": BULL, "short": BEAR}.get(str(t.get("direction") or ""))
            lead = (t.get("playbooks") or [""])[0]
            if side and lead:
                k = (t.get("underlying"), lead, side)
                self.playbook_count[k] = self.playbook_count.get(k, 0) + 1
            if t["net"] >= 0:
                self.loss_streak, self.careful = 0, False
                continue
            self.loss_streak += 1
            if cap and self.loss_streak >= cap:
                try:
                    at = datetime.fromisoformat(str(t.get("ts_ist"))).timestamp()
                except (ValueError, TypeError):
                    at = time.time()
                self.paused_until = max(self.paused_until, min(at, time.time()) + 60.0 * mins)
                self.careful, self.loss_streak = True, 0

    def _resume_loss_state(self, recs):
        """The loss cooldown and F4's same-side block, from today's journal.

        Both were set only in memory at the losing exit, so a restart a few
        minutes after a losing PE allowed another PE at once and reset the
        cooldown_after_loss timer (review D-L1, 2 Oct 2026). last_loss_ts comes
        from the last losing trade of any kind; _loss_side and _loss_side_ts
        from the last losing INDEX OPTION trade only (a bought CE is long the
        index, a bought PE short), exactly as _book_fill sets them.

        Only with F4 on (same_side_cooldown_min > 0): with every flag at its
        default a restart keeps its old behaviour and starts with no loss
        state (review D-ADV-1, 5 Oct 2026).
        """
        if float(getattr(self.L, "same_side_cooldown_min", 0.0) or 0.0) <= 0:
            return
        now = time.time()

        def when(r):
            try:
                t = datetime.fromisoformat(str(r.get("ts_ist") or "")).timestamp()
            except (ValueError, TypeError):
                return None
            return min(t, now)          # a stamp ahead of the clock counts from now

        for r in reversed(recs):
            if float(r.get("net_inr") or 0.0) < 0 and when(r) is not None:
                self.last_loss_ts = max(self.last_loss_ts, when(r))
                break
        for r in reversed(recs):
            side = {"CE": BULL, "PE": BEAR}.get(str(r.get("opt") or "").upper())
            if side and float(r.get("net_inr") or 0.0) < 0 and when(r) is not None:
                self._loss_side, self._loss_side_ts = side, when(r)
                break

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
            write_json(self._open_path, data, default=lambda o: o.item() if hasattr(o, "item") else str(o))
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
            if pos.exit_blocked:
                self._flatten_naked(pos)
                continue
            bid = self._mark_of(pos)
            idx = self._underlying_px(pos)
            if pos.exit_rules != "lhm":         # last_half_hour: the index stop never moves
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
            rows = None
            if fixes and hasattr(self.broker, "order_rows"):
                # ...and only when both of its books agree: a SELL that filled
                # before its place call could answer is missing from a lagging
                # position book, and booking on that book alone has sold the
                # same position twice (review O2, 2 Oct 2026). Fills of an entry
                # order not taken on yet are no part of this position (O-ADV-1).
                held, why, rows = self._agreed_net(pos.symbol, held=held)
                if held is None:
                    self.log(f"[intraday] {pos.symbol}: exit status unknown and {why}; "
                             f"nothing booked or sent until the books agree")
            if held is not None and held != want:
                # Some or all of it did go: book what went, at the price we sent.
                fill = float(px)
                if fixes:
                    gone = self._held_gone(pos, held, short)
                    if gone is None:
                        return None             # wrong sign: book nothing, send nothing
                    fill, oids = self._gone_price(pos, rows, min(gone, pos.qty), short, px)
                    self._know(oids)
                else:
                    gone = abs(want - held)
                legs[0].update(filled=True, qty=min(gone, pos.qty), fill_price=fill)
                self.log(f"[intraday] {pos.symbol}: exit status unknown; broker shows "
                         f"{held} held, so {gone} closed")
        if not legs[0].get("filled"):
            pos.exit_attempts += 1
            if fixes:
                # Angel took it and it did not fill (an IOC that missed): a
                # short fixed wait, not the refusals' doubling (O-ADV-3).
                missed = str(legs[0].get("broker_status") or "").lower().startswith("cancel")
                self._exit_failed(pos, legs[0], errors, missed=missed)
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

    IP_PAUSE_SEC = 300.0
    FUNDS_REFRESH_SEC = 30.0         # live: how often the account's funds are re-read while flat

    def _ip_refused(self, legs):
        """Angel refused the entry because this machine's public IP is not
        registered for the API (AG7002). Nothing was placed. Pause new entries
        for IP_PAUSE_SEC instead of re-sending every pass (7 Oct 09:55-09:58:
        five refusals in three minutes), and say what to do, once an hour."""
        if not (legs and legs[0].get("ip_refused")):
            return False
        self._note("ip_not_registered")
        self.paused_until = max(self.paused_until, time.time() + self.IP_PAUSE_SEC)
        if time.time() - getattr(self, "_ip_alert_at", 0.0) > 3600:
            self._ip_alert_at = time.time()
            self.log("[intraday] ANGEL REFUSES ORDERS FROM THIS IP (AG7002, not a registered IP): "
                     "register it in SmartAPI or switch to the registered network. "
                     f"New entries paused {self.IP_PAUSE_SEC / 60:.0f} min at a time; exits still try.")
            if self.notifier:
                self.notifier.send("🛑 <b>ORDERS REFUSED: IP NOT REGISTERED</b>\nAngel (AG7002) refuses "
                                   "orders from this network's IP. Register it in SmartAPI or switch "
                                   "network. New entries paused.")
        return True

    def _halt_alert(self, reason, text):
        """Halt new entries and say so - once per reason, not every pass."""
        if self.halted and self.halt_reason == reason:
            return
        self.halted, self.halt_reason = True, reason
        self.log(f"[intraday] HALTED: {reason}")
        if self.notifier:
            self.notifier.send(f"🛑 <b>CHECK THE ANGEL APP</b>\n{text}")

    NAKED_CONFIRM_SEC = 2.0

    def _flatten_naked(self, pos):
        """Owner's rule (1 Oct 2026, research_out/phase_a/owner_decisions.md Q7):
        once two net-position reads at least 2 s apart agree the broker is past
        flat, and no order of ours is working in the symbol, send ONE
        risk-reducing order for exactly that quantity, then stay halted and
        alert. Any doubt - an unreadable book, reads that disagree - sends nothing."""
        if not str(pos.exit_blocked).startswith("broker shows") or getattr(pos, "naked_sent", False) \
                or not hasattr(self.broker, "net_position") or not hasattr(self.broker, "open_orders"):
            return
        short = pos.opt == "EQ" and pos.side < 0
        held = self.broker.net_position(pos.symbol)
        working = self.broker.open_orders(pos.symbol)
        past_flat = held is not None and ((held > 0) if short else (held < 0))
        if not past_flat or working is None or working or pos.pending_exit_oid:
            pos.naked_seen = None
            return
        seen, now = getattr(pos, "naked_seen", None), time.time()
        if seen is None or seen[1] != held:
            pos.naked_seen = (now, held)
            return
        if now - seen[0] < self.NAKED_CONFIRM_SEC:
            return
        pos.naked_sent = True                   # one order, whatever happens next
        slot = self.feed.store.slot_of_token.get(str(pos.token))
        buy = held < 0
        px = 0.0
        if slot is not None:
            px = float(self.feed.store.ask[slot] if buy else self.feed.store.bid[slot])
        leg = dict(self._exit_leg(pos, px or pos.mark), side="BUY" if buy else "SELL",
                   qty=abs(held), depth=abs(held), market=True, kind="X")
        try:
            legs, _ = self.broker.place_structure(
                _Ticket([leg], abs(held), "INTRADAY_EXIT", f"flatten naked {pos.symbol}", pos.expiry), 1)
            what = f"{leg['side']} {abs(held)} sent, filled {legs[0].get('filled_qty', legs[0].get('filled', '?'))}"
        except Exception as e:
            what = f"{leg['side']} {abs(held)} FAILED: {e!r}"
        self._halt_alert(f"NAKED {pos.symbol} flatten: {what}",
                         f"<b>NAKED POSITION</b> <code>{pos.symbol}</code> ({held}): one "
                         f"risk-reducing order - {what}. Entries stay halted; check the Angel app.")
        self.log(f"[intraday] NAKED {pos.symbol}: broker {held} confirmed twice, {what}")

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

    def _exit_failed(self, pos, leg, errors, missed=False):
        """A refused or unfilled exit (exit_safety_fixes): after a refusal the
        next try waits 0.5 s, doubling to 5 s; after an order Angel took that
        did not fill (`missed`: an IOC in a fast market) it waits EXIT_MISS_GAP.
        EXIT_ALERT_AFTER in a row of either are reported once. A refusal used
        to be re-sent on every tick, about 8 a second - Angel's 1000-an-hour
        order budget gone in two minutes, and then every exit refused, the stop
        and the square-off too (review O5, 2 Oct 2026). A miss is not a
        refusal: doubled to 5 s it left a stop that kept missing unmanaged for
        5 s between tries (review O-ADV-3, 5 Oct 2026); 1 s keeps a single
        stop's tries inside the order budget for a quarter of an hour, and
        after exit_market_after misses it goes at market anyway."""
        pos.exit_fails += 1
        first, longest = self.EXIT_BACKOFF
        if missed:
            pos.exit_retry_at = time.time() + self.EXIT_MISS_GAP
        else:
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

    def _unadopted(self, symbol, rows):
        """Signed fills of pending entry orders in symbol that the engine has
        not taken on yet, read from the same order-book rows the two books
        were agreed on; None while one of those orders cannot be found in them
        (no id yet). Those fills are in the account's position but in no open
        position: an exit reconciliation that counted them booked too little
        as gone and sent a second SELL over a filled one, and the entry's poll
        then took the same shares on again - the engine holding what the
        account did not, and its stop selling the account short (review
        O-ADV-1, 5 Oct 2026)."""
        total = 0
        for rec in self._pending_entries:
            if rec["symbol"] != symbol:
                continue
            row = next((r for r in rows or []
                        if (rec["oid"] and r["oid"] == rec["oid"])
                        or (rec["uid"] and r["uid"] == rec["uid"])), None)
            if row is None:
                return None
            total += rec["sign"] * max(0, row["filled"] - rec["booked"])
        return total

    def _agreed_net(self, symbol, rows=None, held=None):
        """(held, why, rows): _held_agreed less the fills of pending entry
        orders not taken on yet (_unadopted) - what the engine's own open
        positions in symbol stand for at the broker."""
        b = self.broker
        if rows is None and hasattr(b, "order_rows"):
            rows = b.order_rows(symbol)
        held, why = self._held_agreed(symbol, rows=rows, held=held)
        if held is not None and any(r["symbol"] == symbol for r in self._pending_entries):
            extra = self._unadopted(symbol, rows)
            if extra is None:
                return None, "an entry order in it is not settled yet", rows
            held -= extra
        return held, why, rows

    def _gone_price(self, pos, rows, gone, short, px):
        """(price, order ids): what `gone` units of pos that went without a
        known fill price went at - the average of the finished exit-side
        order-book rows that no order id known to the engine accounts for
        (the lost exit's own), when they add up to exactly `gone`. Otherwise
        the mark px, as before: a lost exit was booked at the mark even when
        the order book showed its real fill (review O-obs1, 5 Oct 2026)."""
        known = getattr(self.broker, "known_oids", None)
        if gone <= 0 or not rows or not isinstance(known, set):
            return float(px), []
        side = "BUY" if short else "SELL"
        mine = [r for r in rows if r["final"] and r["side"] == side and r["filled"] > 0
                and r["avg"] > 0 and r["oid"] not in known and r["oid"] != pos.pending_exit_oid]
        if not mine or sum(r["filled"] for r in mine) != gone:
            return float(px), []
        return sum(r["avg"] * r["filled"] for r in mine) / gone, [r["oid"] for r in mine]

    def _know(self, oids):
        """Order ids now accounted for: never priced a second time."""
        known = getattr(self.broker, "known_oids", None)
        if isinstance(known, set):
            known.update(str(o) for o in oids if o)

    def _agreed_or_wait(self, pos, rows=None):
        """(held, rows): _agreed_net for a pending exit, held None after saying
        why. An unreadable book is reported at once (review O6); a
        disagreement that outlives PENDING_ALERT_SEC halts new entries (O2)."""
        held, why, rows = self._agreed_net(pos.symbol, rows=rows)
        if held is not None:
            return held, rows
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
        return None, rows

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

    def _settle_rest(self, pos, held, short, reason, px, what, rows=None):
        """Both books agree the broker holds `held`: book what else of pos has
        gone - at its order-book price when that can be told (_gone_price) -
        then let the next exit go (True), unless pos is blocked."""
        gone = self._held_gone(pos, held, short)
        if gone is None and pos.exit_blocked:
            return False
        fill, oids = self._gone_price(pos, rows, min(gone or 0, pos.qty), short, px)
        self._clear_pending(pos)
        self._save_open(force=True)
        if gone:
            self._know(oids)
            self.log(f"[intraday] {pos.symbol}: {what}; broker shows {held} held, "
                     f"so {gone} closed" + (f" @ {fill:.2f} (order book)" if oids else ""))
            self._book_fill(pos, min(gone, pos.qty), fill, reason,
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
                self._know([hit["oid"]])
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
            held, rows = self._agreed_or_wait(pos, rows)
            if held is None:
                return False
            return self._settle_rest(pos, held, short, reason, px, "lost exit order", rows)
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
        held, rows = self._agreed_or_wait(pos)
        if held is None:
            return False
        return self._settle_rest(pos, held, short, reason, px,
                                 f"exit order {oid} {st['status']}", rows)

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
            opening = self._opening_capital()
            pct = 100.0 * self.realised_pnl / opening if opening else 0
            self.notifier.send(
                f"{emoji} <b>EXIT</b> {pos.symbol}\n"
                f"{pos.entry:.2f} → {fill:.2f} ({points:+.2f} pt) · {reason}\n"
                f"Net <code>Rs {net:+,.0f}</code>\n"
                f"Day <code>Rs {self.realised_pnl:+,.0f}</code> "
                f"(<code>{pct:+.2f}%</code> of Rs {opening:,.0f}, "
                f"target {self.L.daily_target_pct:g}%)")

        self._check_daily_halt()
        return rec

    def _opening_capital(self):
        """The day's opening traded figure: the daily loss halt is measured on
        it, not on live capital re-read from the account mid-day."""
        return float(getattr(self.capital, "opening_capital", self.capital.capital))

    def _refresh_funds(self):
        """Live only: re-read the account's money every FUNDS_REFRESH_SEC while
        flat and trade min(configured, it) - the account can hold less than
        the configured amount, and what it holds changes through the day
        (owner, 11 Oct 2026). Only while flat: an open position's premium is
        already out of the account and is also counted as deployed. A failed
        read keeps the last figure."""
        reader = self.funds_reader
        if reader is None or not hasattr(self.capital, "set_account_funds"):
            return
        if self.book.count() or self._pending_entries:
            return
        now = time.time()
        if now - self._funds_read_at < self.FUNDS_REFRESH_SEC:
            return
        self._funds_read_at = now
        try:
            funds, detail = reader()
        except Exception as e:
            funds, detail = 0.0, f"{e!r}"
        if funds <= 0:
            if not self._funds_fail_said:
                self._funds_fail_said = True
                self.log(f"[capital] account funds unreadable ({str(detail)[:120]}); "
                         f"trading on the last figure Rs {self.capital.capital:,.0f}")
            return
        self._funds_fail_said = False
        before = self.capital.capital
        after = self.capital.set_account_funds(funds)
        if abs(after - before) >= 1.0:
            ceiling = float(getattr(self.capital, "ceiling", 0.0) or 0.0)
            self.log(f"[capital] account funds Rs {funds:,.2f} ({detail}): trading capital "
                     f"Rs {before:,.0f} -> Rs {after:,.0f}"
                     + (f" (min of configured Rs {ceiling:,.0f} and the account)" if ceiling else ""))

    def _check_daily_halt(self):
        if self.realised_pnl <= -abs(self._opening_capital() * self.L.max_daily_loss_pct / 100.0):
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
    def _gates(self, exempt=()):
        """Everything that must be true before an entry is even considered.
        `exempt`: gate names a caller has its own clock for (last_half_hour
        enters at 15:00, inside no_new_trades_minutes, by design)."""
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
        if mins <= L.no_new_trades_minutes and "too_close_to_squareoff" not in exempt:
            return "too_close_to_squareoff"
        open_at = now_ist().replace(hour=9, minute=15, second=0, microsecond=0)
        if (now_ist() - open_at).total_seconds() / 60.0 < L.warmup_minutes \
                and "warmup" not in exempt:
            return "warmup"
        return ""

    def _closed_rows(self, name, rows, t0=None):
        """Decide on COMPLETED bars only, and only just after one closes.

        Owner's 1 Oct 2026 review: live passed the still-forming bar to the
        playbooks, so a rejection wick 23 seconds into a minute was traded as a
        rejection candle, and tools/backtest.py - which only ever sees closed
        bars - had tested a different rule. With decide_on_closed_bars the
        forming bar is dropped, and the decision is taken only within
        entry_window_sec of the bar's close: the confirmation is the close, the
        entry is the next tick, and a setup not taken then is not chased later.
        Returns (rows, reason); reason is "" when this is the moment to act.

        Fails closed (review D-M1, 2 Oct 2026). `t0` is the time taken BEFORE
        candles.get() copied the rows: every trailing row whose interval had
        not fully elapsed by then is still forming in that copy - with no live
        bar for the name (a spot slot that never ticks, a stalled sampler) or
        when the sampler rolled the minute between get() and this read - and
        is dropped. A bar time that cannot be read is a refusal, not a go.
        """
        L = self.L
        if not getattr(L, "decide_on_closed_bars", False) or not rows:
            return rows, ""
        if t0 is None:
            t0 = time.time()
        # One read of the live bar, the same one the drop below is checked against.
        live = (getattr(self.candles, "live", None) or {}).get(name) if self.candles else None
        live_iso = live[0] if live else None
        if live_iso is not None and rows[-1][0] == live_iso:
            rows = rows[:-1]
        from arb.candles import SECONDS
        secs = SECONDS.get(getattr(self.candles, "interval", "1m"), 60)
        closed_at = None
        while rows:
            try:
                closed_at = datetime.fromisoformat(str(rows[-1][0])).timestamp() + secs
            except (ValueError, TypeError):
                return rows, "bar_time_unparseable"
            if closed_at <= t0:
                break
            rows = rows[:-1]                    # still forming when the rows were copied
        if not rows:
            return rows, "no_closed_bar"
        if time.time() - closed_at > float(getattr(L, "entry_window_sec", 15.0)):
            return rows, "waiting_for_bar_close"
        return rows, ""

    def _chased(self, rows, px):
        """F1's chase guard: with decide_on_closed_bars, price has already moved
        more than max_chase_atr ATRs from the decision bar's close. Indices and
        stocks alike (review D-L2)."""
        L = self.L
        chase = float(getattr(L, "max_chase_atr", 0.0) or 0.0)
        if not (getattr(L, "decide_on_closed_bars", False) and chase > 0 and rows):
            return False
        a = atr(rows[:-1], period=L.atr_period) if len(rows) > L.atr_period + 1 else 0.0
        return a > 0 and abs(float(px) - float(rows[-1][4])) > chase * a

    def _late_or_trapped(self, rows, side, spot):
        """F1's chase guard and the F2/F3 filters, on completed bars. "" = go."""
        L = self.L
        if self._chased(rows, spot):
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

    # ── last-half-hour momentum ─────────────────────────────────────────────
    def _lhm_signal(self, und, rows, ranges=None):
        """(side, close, stop, move, adr) when the 14:59 bar has moved
        lhm_adr_frac x ADR from the previous close, else None. `rows` are
        completed bars ending at the decision bar. `ranges` ({day: [high,
        low]}, full sessions from the history fetch) completes the ADR window
        the candle store is too short for (it holds ~7 sessions, ADR wants 10).

        As research/portfolio_test.py lhm_gen (screen.prep): ADR is the mean
        high-low of the last lhm_adr_days FULL sessions, the stop
        lhm_stop_atr x the ATR of the 20 bars BEFORE the decision bar."""
        L = self.L
        if not rows:
            return None
        day = rows[-1][0][:10]
        prev = [r for r in rows if r[0][:10] < day]
        if not prev:
            return None
        days, first = {}, {}
        for r in prev:
            d = days.setdefault(r[0][:10], [r[2], r[3]])
            first.setdefault(r[0][:10], str(r[0])[11:16])
            d[0], d[1] = max(d[0], r[2]), min(d[1], r[3])
        # The store keeps its last max_rows bars, so its oldest session is only
        # a tail (on a full store: 15:00-15:29). A tail is not a day's range -
        # counted, it pulled the ADR down and let smaller moves through.
        days = {k: v for k, v in days.items() if first[k] <= "09:20"}
        for k, hl in (ranges or {}).items():
            if str(k) < day:
                days[str(k)] = [float(hl[0]), float(hl[1])]
        keys = sorted(days)[-int(L.lhm_adr_days):]
        self._lhm_adr_n = len(keys)
        if len(keys) < 3:
            return None
        adr = sum(days[k][0] - days[k][1] for k in keys) / len(keys)
        close, pc = rows[-1][4], prev[-1][4]
        move = close - pc
        if adr <= 0 or abs(move) < float(L.lhm_adr_frac) * adr:
            return None
        side = 1 if move > 0 else -1
        a = atr(rows[:-1], period=int(L.atr_period))
        if a <= 0:
            return None
        return side, close, close - side * float(L.lhm_stop_atr) * a, move, adr

    def _lhm_ranges(self, und, day):
        """Full-session highs and lows from the noise-breakout history fetch,
        when it has run today (never started from here: no new API calls)."""
        h = self.__dict__.get("_nb_hist")
        prof = h.get(day, und) if h is not None else None
        return (prof or {}).get("ranges")

    def _lhm_mode(self):
        """"off" | "shadow" | "on"; anything else is off, said once."""
        mode = str(getattr(self.L, "last_half_hour", "off") or "off")
        if mode in ("off", "shadow", "on"):
            return mode
        said = self.__dict__.setdefault("_lhm_said", set())
        if ("mode", mode) not in said:
            said.add(("mode", mode))
            self.log(f"[intraday] last_half_hour = {mode!r} is not a mode "
                     f"('off', 'shadow' or 'on') - treated as off")
        return "off"

    def _shadow_lhm(self):
        """last_half_hour = "shadow": at lhm_entry_at, note the trade the rule
        would take - contract, premium, index stop - and follow it to the
        square-off on live quotes. No order is ever sent. Forward evidence
        before any money (owner's momentum request, 5 Oct 2026)."""
        L = self.L
        if self._lhm_mode() != "shadow":
            return
        now = now_ist()
        if not hasattr(self, "_lhm"):
            self._lhm = {}
        hh, mm = (int(x) for x in str(L.lhm_entry_at).split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        due = 0.0 <= (now - at).total_seconds() <= float(getattr(L, "entry_window_sec", 15.0))
        for und in getattr(L, "lhm_indices", ()) or ():
            key = (now.strftime("%Y-%m-%d"), und)
            sh = self._lhm.get(key)
            if sh is None and due:
                self._lhm[key] = self._lhm_open(und) or {"none": True}
            elif sh and not sh.get("none") and not sh.get("done"):
                self._lhm_follow(sh)

    def _live_lhm(self):
        """last_half_hour = "on" (owner, 6 Oct 2026, on research/
        portfolio_result.txt): at lhm_entry_at, on the CLOSED bar ending then,
        buy the index's move when it is lhm_adr_frac x ADR from the previous
        close and the 4-hour trend agrees - through enter(), the same order
        path, guards and adoption as every other entry. One attempt per index
        per day, inside entry_window_sec; a pass refused by a gate that may
        clear (an exit settling, a stale quote) is retried in the window.
        Returns True when it entered."""
        L = self.L
        if self._lhm_mode() != "on":
            return False
        now = now_ist()
        day = now.strftime("%Y-%m-%d")
        hh, mm = (int(x) for x in str(L.lhm_entry_at).split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        since = (now - at).total_seconds()
        if since < 0:
            return False
        st = self.__dict__.get("_lhm_live")
        if st is None or st.get("day") != day:
            st = self._lhm_live = {"day": day, "idx": {}}
        win = float(getattr(L, "entry_window_sec", 15.0))
        for und in [str(u) for u in (getattr(L, "lhm_indices", ()) or ())]:
            rec = st["idx"].setdefault(und, {"state": "waiting", "why": "", "said": set()})
            if rec["state"] != "waiting":
                continue
            if since > win:
                rec["state"] = "missed"
                self.log(f"[intraday] last_half_hour {und}: not taken today - "
                         f"{rec['why'] or 'no pass inside the entry window'}")
                continue
            final, entered, why = self._lhm_try(und, rec, at, day)
            rec["why"] = why
            if why not in rec["said"]:
                rec["said"].add(why)
                self.log(f"[intraday] last_half_hour {und}: {why}")
            if final:
                rec["state"] = "done"
            if entered:
                return True
        return False

    def _lhm_try(self, und, rec, at, day):
        """One decision pass for one index. (final, entered, why)."""
        L = self.L
        gate = self._gates(exempt=("too_close_to_squareoff", "warmup"))
        if gate:
            return False, False, f"blocked: {gate}"
        limit = abs(self._opening_capital() * L.max_daily_loss_pct / 100.0)
        if self.realised_pnl <= -limit:
            return True, False, "blocked: daily loss limit"
        if self.book.time_to_close(self.cfg.session_end) <= float(L.squareoff_minutes) + 1.0:
            return True, False, "blocked: at the square-off"
        if self.careful and not grade_at_least("", getattr(L, "grade_after_streak", "B")):
            return True, False, "blocked: graded setups only until a win (losing streak)"
        b = self._binding_for(und)
        if b is None or not self.candles:
            return True, False, "no expiry bound or no candles"
        from arb.candles import SECONDS
        secs = SECONDS.get(getattr(self.candles, "interval", "1m"), 60)
        if secs != 60:
            return True, False, "needs 1-minute candles"
        t0 = time.time()
        rows, wait = self._closed_rows(und, self.candles.get(und, limit=5000), t0)
        if wait:
            return False, False, f"waiting: {wait}"
        at_ts = at.timestamp()

        def end(r):
            return datetime.fromisoformat(str(r[0])).timestamp() + secs
        while rows and end(rows[-1]) > min(at_ts, t0):
            rows = rows[:-1]                    # the forming 15:00 bar, with closed bars off
        if not rows or abs(end(rows[-1]) - at_ts) > 1.0:
            return False, False, f"waiting: the bar ending {L.lhm_entry_at} is not in the candles"
        sig = self._lhm_signal(und, rows, self._lhm_ranges(und, day))
        n = getattr(self, "_lhm_adr_n", 0)
        if sig is None:
            return True, False, f"no signal (move under {L.lhm_adr_frac:g} x ADR({n}))"
        side, close, stop, move, adr = sig
        what = f"{move:+.1f} from prev close ({move / adr:+.2f} x ADR({n}) {adr:.1f})"
        tr = trend_4h(rows, int(getattr(L, "trend_4h_bars", 5) or 5))
        if tr != side:
            return True, False, f"{what} against the 4-hour trend - no trade"
        if self._same_idea_blocked(side):
            return True, False, f"{what} - same side as a loss inside same_side_cooldown_min"
        spot = self._spot(und)
        if spot <= 0:
            return False, False, "no spot"
        if self._chased(rows, spot):
            return False, False, f"{what} - price moved over max_chase_atr since the close"
        cv = b.view(self.feed.store, time.perf_counter_ns(), time.time())
        # The contract exactly as hunt() picks it: the most delta whose loss at
        # the rule's own index stop fits max_risk_pct_per_trade, costs inside.
        cap_inr = self.capital.capital * float(L.max_risk_pct_per_trade) / 100.0
        idx_risk = abs(close - stop)
        lot = max(int(cv.lot_size), 1)
        inside = self._costs_inside()
        cand = self.selector.select(
            cv, spot, side, self.capital.capital - self.book.deployed(),
            max_age_ms=400.0, min_depth_mult=1.0, max_delta=cap_inr / (idx_risk * lot),
            max_delta_fn=(lambda px, spr: (cap_inr / lot - self._extra_risk_points(px, spr, lot))
                          / idx_risk) if inside else None)
        if cand is None:
            return False, False, f"{what} - no contract ({self.selector.last_reject})"
        if time.time() - self._symbol_cooldown.get(cand.symbol, -1e9) < L.symbol_cooldown_sec:
            return False, False, f"{what} - symbol_cooldown on {cand.symbol}"
        frame = _LhmFrame(close, stop, f"last_half_hour: {und} {what}")
        cost_pts = self._round_trip_points(cand.ask, cand.lot_size)
        # plan_trade's premium stop is reprice's: max(index risk x delta, 2
        # spreads), at most max_risk_frac_of_premium of the premium, refused
        # over the cap (costs inside), under min_net_points or over
        # max_cost_frac_of_risk.
        plan = plan_trade(cand, frame, side, L, cost_pts, invalidation=stop,
                          max_risk_inr=cap_inr,
                          extra_risk_points=(self._extra_risk_points(
                              cand.ask, cand.detail.get("spread", 0.0), cand.lot_size)
                              if inside else 0.0),
                          atr=atr(rows[:-1], period=L.atr_period))
        if plan is None:
            return False, False, f"{what} - {cand.symbol}: no viable premium stop inside the cap"
        if plan.get("planned_risk_inr", plan["risk_inr"]) > cap_inr + 1e-6:
            return False, False, f"{what} - risk_too_large"
        lots = self._lots_for("", cand, plan)
        if lots <= 0:
            return False, False, f"{what} - risk_too_large"
        plan.update(index_entry=spot, entry_bar=rows[-1][0], index_target=0.0,
                    reward_delta=cand.delta, reward_points=0.0, reward_inr=0.0,
                    exit_rules="lhm", playbook="last_half_hour")
        v = Verdict(side=side, day_trend=tr, bias=tr, bias_source="4h", grade="",
                    reason=frame.reason,
                    setups=[Setup(name="last_half_hour", side=side, invalidation=stop,
                                  steps=[(f"move {move / adr:+.2f} ADR", round(move, 1)),
                                         ("4h trend agrees", tr),
                                         (f"stop {L.lhm_stop_atr:g} ATR", round(stop, 1))])])
        rec["state"] = "sent"                   # one order a day, whatever comes back
        ok = self.enter(cand, plan, frame, None, v, lots=lots)
        if ok:
            k = (und, "last_half_hour", side)
            self.playbook_count[k] = self.playbook_count.get(k, 0) + 1
        return True, ok, (f"{what} - ENTERED {cand.symbol}, index stop {stop:.1f}" if ok else
                          f"{what} - entry order for {cand.symbol} not filled (see the order log)")

    def _lhm_open(self, und):
        L = self.L
        b = self._binding_for(und)
        if b is None or not self.candles:
            return None
        t0 = time.time()
        rows, wait = self._closed_rows(und, self.candles.get(und, limit=3000), t0=t0)
        sig = self._lhm_signal(und, rows, self._lhm_ranges(und, now_ist().strftime("%Y-%m-%d"))) \
            if not wait else None
        if sig is None:
            self.log(f"[shadow] last_half_hour {und}: no signal")
            return None
        side, close, stop, move, adr = sig
        tr = trend_4h(self.candles.get(und, limit=3000), int(getattr(L, "trend_4h_bars", 5) or 5))
        if tr != side:
            self.log(f"[shadow] last_half_hour {und}: move {move:+.1f} ({move / adr:+.2f} ADR) "
                     f"against the 4-hour trend - no trade")
            return None
        spot = self._spot(und) or close
        cv = b.view(self.feed.store, time.perf_counter_ns(), time.time())
        cap_inr = self.capital.capital * float(L.lhm_risk_pct) / 100.0
        idx_risk = abs(close - stop)
        lot = max(cv.lot_size, 1)
        cand = self.selector.select(
            cv, spot, side, self.capital.capital - self.book.deployed(),
            max_age_ms=400.0, min_depth_mult=1.0, max_delta=cap_inr / (idx_risk * lot),
            max_delta_fn=(lambda px, spr: (cap_inr / lot - self._extra_risk_points(px, spr, lot)) / idx_risk)
            if self._costs_inside() else None)
        if cand is None:
            self.log(f"[shadow] last_half_hour {und}: signal {('CE' if side > 0 else 'PE')} but "
                     f"no contract ({self.selector.last_reject})")
            return None
        sh = {"und": und, "side": side, "symbol": cand.symbol, "token": str(cand.token),
              "lot": cand.lot_size, "entry": cand.ask, "index_entry": spot, "index_stop": stop,
              "opened": now_ist().isoformat(), "move_adr": round(move / adr, 2)}
        self.log(f"[shadow] last_half_hour WOULD BUY {cand.symbol} @ {cand.ask:.2f} x{cand.lot_size} "
                 f"| {und} {move:+.1f} from prev close ({move / adr:+.2f} ADR) | index stop "
                 f"{stop:.1f} | NO ORDER SENT")
        return sh

    def _lhm_follow(self, sh):
        L = self.L
        slot = self.feed.store.slot_of_token.get(sh["token"])
        bid = float(self.feed.store.bid[slot]) if slot is not None else 0.0
        idx = self._spot(sh["und"])
        hit = idx > 0 and (idx - sh["index_stop"]) * sh["side"] <= 0
        mins = self.book.time_to_close(self.cfg.session_end)
        if not (hit or mins <= float(L.squareoff_minutes)) or bid <= 0:
            return
        q = sh["lot"]
        cost = leg_cost(sh["entry"], q, "BUY", "OPT", self.cm) + leg_cost(bid, q, "SELL", "OPT", self.cm)
        net = (bid - sh["entry"]) * q - cost
        sh.update(done=True, exit=bid, why="index_stop" if hit else "squareoff", net=round(net, 2))
        self.log(f"[shadow] last_half_hour {sh['symbol']} would EXIT @ {bid:.2f} ({sh['why']}) "
                 f"net Rs {net:+,.0f} | NO ORDER SENT")
        jdir = getattr(self.journal, "dir", None)
        if jdir:
            try:
                with open(os.path.join(jdir, f"shadow_{sh['opened'][:10]}.jsonl"), "a") as fh:
                    fh.write(json.dumps(dict(sh, playbook="last_half_hour")) + "\n")
            except OSError as e:
                self.log(f"[shadow] journal write failed: {e}")

    # ── noise-area breakout, shadow mode ────────────────────────────────────
    # Zarattini, Aziz & Barbon (2024); the rule is arb/noise_breakout.py, which
    # research/noise_parity.py matches bar for bar against the backtest the
    # evidence comes from (research_out/momentum/screen.py). Owner's approval
    # of 5 Oct 2026 is for SHADOW only: nothing here can reach the broker -
    # it reads candles, the chain and quotes, and writes the log and
    # journal/<mode>/shadow_YYYY-MM-DD.jsonl.
    def _nb_log_once(self, key, text):
        seen = self.__dict__.setdefault("_nb_logged", {})
        if seen.get(key) != text:
            seen[key] = text
            self.log(text)

    @staticmethod
    def _nb_hhmm(s):
        try:
            hh, mm = (int(x) for x in str(s).split(":")[:2])
            return hh * 60 + mm
        except Exception:
            return 15 * 60 + 15

    def _nb_enabled(self):
        mode = str(getattr(self.L, "noise_breakout", "off") or "off")
        if mode == "shadow":
            return True
        if mode != "off":
            self._nb_log_once("mode", f"[shadow] noise_breakout = {mode!r} is not supported - "
                                      f"treated as off (only 'shadow'; real orders need the "
                                      f"owner's separate approval)")
        return False

    def _nb_history(self):
        h = self.__dict__.get("_nb_hist")
        if h is None:
            from arb.noise_sigma import SigmaHistory
            L = self.L
            h = self._nb_hist = SigmaHistory(
                lambda: getattr(self.candles, "api", None),
                dict(getattr(self.candles, "underlyings", None) or {}),
                ndays=int(L.nb_sigma_days), min_days=int(L.nb_sigma_min_days),
                history_days=int(getattr(L, "nb_history_days", 28)), log=self.log)
        return h

    def _shadow_noise(self):
        """noise_breakout = "shadow": on each 30-minute grid bar, note the
        trade the noise-area breakout would take - contract, premium, index
        stop, which live gates pass - and follow it on the trailed band and
        live quotes until its stop or the square-off. No order is ever sent.
        Every raw signal is followed, gates failed or not, so the index is
        busy exactly as in the backtest; the gates decide what counts."""
        if not self._nb_enabled():
            return
        from arb.instruments import market_phase
        now = now_ist()
        day = now.strftime("%Y-%m-%d")
        end = str(getattr(self.cfg, "session_end", "15:15"))
        phase = market_phase(now, start=getattr(self.cfg, "session_start", "09:15"), end=end)
        if phase not in ("preopen", "open"):
            # After the session end no new decision, but a shadow trade still
            # open (its square-off waiting for a quote, or a restart after the
            # close) is closed now, so the day's record is complete.
            if phase == "closed" and now.hour * 60 + now.minute >= self._nb_hhmm(end):
                st = self.__dict__.get("_nb")
                if st is None or st["day"] != day:
                    st = self._nb = {"day": day, "idx": {}}
                    self._nb_resume(st)
                for und, S in list(st["idx"].items()):
                    if S.get("open") is not None:
                        try:
                            self._nb_follow(S, und, None, None, now, closing=True)
                        except Exception as e:
                            self._nb_log_once(f"close:{und}", f"[shadow] noise_breakout {und} "
                                                              f"close error: {e!r}")
            return
        if not self.candles or str(getattr(self.candles, "interval", "1m")) != "1m":
            self._nb_log_once("interval", "[shadow] noise_breakout needs 1-minute candles - off")
            return
        st = self.__dict__.get("_nb")
        if st is None or st["day"] != day:
            st = self._nb = {"day": day, "idx": {}}
            self._nb_resume(st)
        names = [str(u) for u in (getattr(self.L, "nb_indices", ()) or ())]
        hist = self._nb_history()
        hist.ensure(day, names)                 # a background thread, never a wait
        for und in names:
            try:
                self._nb_index(st, und, hist.get(day, und), hist, now)
            except Exception as e:
                self._nb_log_once(f"err:{und}", f"[shadow] noise_breakout {und} error: {e!r}")

    def _nb_closed(self, und, t0):
        """Completed in-session 1-minute bars (the live bar and anything still
        forming when they were copied dropped, as _closed_rows), and whether
        the last closed within entry_window_sec."""
        from arb import noise_breakout as nb
        rows = self.candles.get(und, limit=3000)
        live = (getattr(self.candles, "live", None) or {}).get(und)
        if live and rows and rows[-1][0] == live[0]:
            rows = rows[:-1]
        rows = nb.in_session(rows)
        closed_at = None
        while rows:
            closed_at = datetime.fromisoformat(str(rows[-1][0])).timestamp() + 60.0
            if closed_at <= t0:
                break
            rows = rows[:-1]
        fresh = bool(rows) and time.time() - closed_at <= float(getattr(self.L, "entry_window_sec", 15.0))
        return rows, fresh

    def _nb_index(self, st, und, prof, hist, now):
        from arb import noise_breakout as nb
        L = self.L
        S = st["idx"].setdefault(und, {"open": None, "free_after": "", "last_grid": "",
                                       "have_bar": "", "trades": 0})
        sh = S["open"]
        # The bar that should be the last closed one now; once it is in, the
        # rows are not copied again until the next minute.
        want = (now.replace(second=0, microsecond=0)).timestamp() - 60.0
        want_iso = datetime.fromtimestamp(want, now.tzinfo).strftime("%Y-%m-%dT%H:%M:%S+05:30")
        rows, fresh = None, False
        if S["have_bar"] != want_iso:
            rows, fresh = self._nb_closed(und, time.time())
            if rows and rows[-1][0] == want_iso and prof is not None:
                S["have_bar"] = want_iso        # (no sigma yet: copied again next pass)
        if sh is not None:
            self._nb_follow(S, und, rows, prof, now)
            if S["open"] is not None:
                return
            # Closed on this pass. The bar in hand may be a grid bar after the
            # exit bar, which the backtest decides (screen.gen: i > busy); on a
            # pass without new rows (the exit waited for a quote) they are
            # copied again rather than the decision being lost.
            if rows is None:
                rows, fresh = self._nb_closed(und, time.time())
        if not rows or rows[-1][0][:10] != st["day"]:
            return
        last = rows[-1]
        t = nb.end_minute(last[0])
        if not (nb.hhmm(L.nb_lo) <= t <= nb.hhmm(L.nb_hi)) or not nb.on_grid(t, int(L.nb_grid_min)):
            return
        if last[0] <= S["last_grid"] or last[0] <= S["free_after"]:
            return                              # decided, or the last trade exited on/after it
        hm = f"{t // 60:02d}:{t % 60:02d}"     # the decision time: the bar's END minute
        if not fresh:
            S["last_grid"] = last[0]
            self.log(f"[shadow] noise_breakout {und} {hm}: grid bar seen too late - not decided")
            return
        if prof is None:
            S["have_bar"] = ""                  # look again next pass: it may land in the window
            self._nb_log_once(f"nosig:{und}:{last[0]}", f"[shadow] noise_breakout {und} {hm}: "
                              f"sigma not ready ({hist.status(st['day'], und)}) - nothing done")
            return
        S["last_grid"] = last[0]
        self._nb_open(S, und, rows, prof, now)

    def _nb_open(self, S, und, rows, prof, now):
        from arb import noise_breakout as nb
        L = self.L
        k = nb.today_of(rows)
        prev = rows[:k]
        pc_candles = float(prev[-1][4]) if prev else None
        pc_day = prev[-1][0][:10] if prev else ""
        # The previous close from the sigma history fetch - the same Angel
        # history the backtest's rows are, and normally the same last bar the
        # live store holds for yesterday once its morning refresh has run. The
        # store's value only when it holds a later session than the history
        # returned; both are journalled.
        if pc_candles is not None and pc_day > str(prof.get("prev_day") or ""):
            pc, src = pc_candles, "candles"
        else:
            pc, src = float(prof["prev_close"]), "history"
        vm, minr, grid = float(L.nb_vm), float(L.nb_minr), int(L.nb_grid_min)
        sig = nb.signal(rows[-450:], pc, prof["sigma"], vm, minr, L.nb_lo, L.nb_hi, grid,
                        require_open="09:15")
        te = nb.end_minute(rows[-1][0])
        hm = f"{te // 60:02d}:{te % 60:02d}"
        if not sig["side"]:
            if sig["why"] == "inside_noise_area":
                self.log(f"[shadow] noise_breakout {und} {hm}: inside the noise area "
                         f"(close {sig['close']:.1f}, band {sig['lb']:.1f} - {sig['ub']:.1f})")
            else:
                self.log(f"[shadow] noise_breakout {und} {hm}: no decision ({sig['why']})")
            return
        side, close, stop = sig["side"], sig["close"], sig["stop"]
        # The live gates the evidence applied: the 4-hour trend and the half-day pace.
        h4 = trend_4h(rows, int(getattr(L, "trend_4h_bars", 0) or 5))
        pace = nb.pace_ratio(rows, period=20, base=200)
        halves = (getattr(L, "atr_by_index", None) or {}).get(und) or (L.min_atr_ratio, L.min_atr_ratio)
        t = sig["t"]
        split = nb.hhmm(str(getattr(L, "atr_half_split", "12:15")))
        need = float(halves[0] if t < split else halves[1])
        pace_pass = nb.pace_ok(pace, t, halves, half=split)
        gates_ok = h4 == side and pace_pass
        others = [u for u, s in self._nb["idx"].items()
                  if u != und and s["open"] and s["open"]["gates"]["all"]]
        sh = {"playbook": "noise_breakout", "shadow": True, "event": "open",
              "id": f"NB-{rows[-1][0][:10]}-{und}-{hm.replace(':', '')}",
              "und": und, "side": side, "decision_bar": rows[-1][0], "t": f"{t // 60:02d}:{t % 60:02d}",
              "close": close, "o": sig["o"], "pc": pc, "pc_source": src,
              "pc_history": prof.get("prev_close"), "prev_day": prof.get("prev_day"),
              "pc_candles": pc_candles, "pc_candles_bar": prev[-1][0] if prev else "",
              "sigma_days": len(prof.get("days") or []), "ub": sig["ub"], "lb": sig["lb"],
              "sigma": sig["sigma"], "band": sig["band"], "atr": sig["atr"],
              "index_stop0": stop, "index_stop": stop, "stop_path": [[rows[-1][0], stop]],
              "gates": {"trend_4h": h4, "trend_ok": h4 == side, "pace": round(pace, 4),
                        "pace_need": need, "pace_ok": pace_pass, "all": gates_ok},
              "live_position_open": bool(self.book.count() or self._pending_entries),
              "f4_blocked": self._same_idea_blocked(side), "other_shadow_open": others,
              "opened": now.isoformat(), "last_bar": rows[-1][0],
              "symbol": None, "token": None, "lot": 0, "entry": 0.0}
        spot = self._spot(und) or close
        sh["index_entry"] = spot
        self._nb_contract(sh, und, spot, abs(close - stop))
        S["open"] = sh
        S["trades"] += 1
        sides = S.setdefault("sides", {})
        sides[side] = sides.get(side, 0) + 1
        sh["nth_today_this_side"] = sides[side]     # the backtest takes at most 2 a side a day
        g = (f"4h {h4:+d} {'ok' if h4 == side else 'AGAINST'}, pace {pace:.2f}/{need:.2f} "
             f"{'ok' if pace_pass else 'LOW'} -> {'GATES PASS' if gates_ok else 'gates fail'}")
        what = (f"WOULD BUY {sh['symbol']} @ {sh['entry']:.2f} x{sh['lot']}" if sh["symbol"]
                else f"signal {'CE' if side > 0 else 'PE'} but no contract ({sh.get('no_contract', '')})")
        self.log(f"[shadow] noise_breakout {what} | {und} {hm} close {close:.1f} "
                 f"{'>' if side > 0 else '<'} {sig['ub'] if side > 0 else sig['lb']:.1f} | index stop "
                 f"{stop:.1f} | {g} | live position {'yes' if sh['live_position_open'] else 'no'}"
                 f"{', F4 would block' if sh['f4_blocked'] else ''} | NO ORDER SENT")
        self._nb_journal(sh)

    def _nb_contract(self, sh, und, spot, idx_risk):
        """The contract the live playbook would buy: selector.select at the
        per-trade cap (max_risk_pct_per_trade of capital), costs inside when
        risk_includes_costs; plus the premium stop plan_trade would set, for
        the record."""
        L = self.L
        b = self._binding_for(und)
        if b is None or idx_risk <= 0:
            sh["no_contract"] = "no binding" if b is None else "zero index risk"
            return
        cv = b.view(self.feed.store, time.perf_counter_ns(), time.time())
        capital = float(self.capital.capital)
        cap_inr = capital * float(L.max_risk_pct_per_trade) / 100.0
        lot = max(int(cv.lot_size), 1)
        inside = self._costs_inside()
        cand = self.selector.select(
            cv, spot, sh["side"], capital, max_age_ms=400.0, min_depth_mult=1.0,
            max_delta=cap_inr / (idx_risk * lot),
            max_delta_fn=(lambda px, spr: (cap_inr / lot - self._extra_risk_points(px, spr, lot)) / idx_risk)
            if inside else None)
        sh["cap_inr"] = round(cap_inr, 2)
        if cand is None:
            sh["no_contract"] = self.selector.last_reject or "no contract"
            return
        sp = float(cand.detail.get("spread", 0.0) or 0.0)
        ask, q = float(cand.ask), int(cand.lot_size)
        prem_risk = min(max(idx_risk * float(cand.delta), 2.0 * sp), ask * float(L.max_risk_frac_of_premium))
        extra = self._extra_risk_points(ask, sp, q) if inside else 0.0
        cost_pts = self._round_trip_points(ask, q)
        why = ""
        if (prem_risk + extra) * q > (cap_inr + 1e-6 if inside else cap_inr * 1.05):
            why = "premium risk over the cap"
        elif 2.0 * prem_risk - cost_pts < float(L.min_net_points):
            why = "move too small for costs"
        frac = float(getattr(L, "max_cost_frac_of_risk", 0.0) or 0.0)
        if not why and frac > 0 and cost_pts > frac * (prem_risk + extra):
            why = "costs too large a part of the risk"
        sh.update(symbol=cand.symbol, token=str(cand.token), lot=q, entry=ask, entry_bid=float(cand.bid),
                  delta=round(float(cand.delta), 4), spread=sp, strike=cand.strike,
                  prem_risk=round(prem_risk, 2), prem_stop=round(ask - prem_risk, 2),
                  cost_pts=round(cost_pts, 2), plan_ok=not why, plan_why=why,
                  best_bid=0.0, worst_bid=0.0)

    def _nb_walk(self, sh, und, rows, prof, now):
        """Today's completed bars after sh["last_bar"] through the trail; the
        exit it finds (a dict for exit_due) or None.

        A missing minute is not stepped over at once: the trail holds there
        for up to nb_gap_hold_sec (the history refresh normally fills it, e.g.
        after a restart), then goes on and lists it in sh["gaps"]. A listed
        minute that turns up later is checked against the stop that was in
        force then: a stop it hit is the exit, as the backtest would have it
        ("seen": "late_bar"). sh["gaps"] marks the trade as followed with a
        hole in its bars, for the evaluation."""
        from arb import noise_breakout as nb
        L = self.L
        k = nb.today_of(rows)
        if rows[k][0][:10] != sh["decision_bar"][:10]:
            return None
        today = rows[k:]
        side = sh["side"]
        gaps = sh.get("gaps") or []
        if gaps:
            checked = sh.setdefault("gaps_checked", [])
            for r in today:
                if r[0] in gaps and r[0] not in checked:
                    checked.append(r[0])
                    stop_then = [s for b, s in sh["stop_path"] if b < r[0]][-1]
                    if (side > 0 and float(r[3]) <= stop_then) or (side < 0 and float(r[2]) >= stop_then):
                        px = min(float(r[1]), stop_then) if side > 0 else max(float(r[1]), stop_then)
                        self.log(f"[shadow] noise_breakout {und}: the late {r[0][11:16]} bar went through "
                                 f"the stop {stop_then:.1f} then | NO ORDER SENT")
                        return {"why": "index_stop", "bar": r[0], "index_ref": px, "seen": "late_bar",
                                "at": now.isoformat()}
        tz = now.tzinfo
        prev_ts = datetime.fromisoformat(sh["last_bar"]).timestamp()
        hold = float(getattr(L, "nb_gap_hold_sec", 180.0))
        state = {"side": side, "stop": sh["index_stop"]}
        for n, r in enumerate(today):
            if r[0] <= sh["last_bar"]:
                continue
            ts = datetime.fromisoformat(r[0]).timestamp()
            if ts - prev_ts > 60.0 + 1e-6:
                missing = [datetime.fromtimestamp(prev_ts + 60.0 * m, tz).isoformat()
                           for m in range(1, int(round((ts - prev_ts) / 60.0)))]
                waits = sh.setdefault("gap_wait", {})
                first = waits.setdefault(missing[0], now.isoformat())
                if (now - datetime.fromisoformat(first)).total_seconds() < hold:
                    break                       # hold the trail: the minute may still arrive
                sh["gaps"] = (sh.get("gaps") or []) + missing
                self.log(f"[shadow] noise_breakout {und}: {len(missing)} minute(s) from "
                         f"{missing[0][11:16]} missing - trail goes on, trade marked 'gaps'")
            state = nb.trail_step(state, today[:n + 1], sh["o"], sh["pc"], prof["sigma"],
                                  float(L.nb_vm), int(L.nb_grid_min), None)
            sh["last_bar"] = r[0]
            prev_ts = ts
            if "exit" in state:
                return {"why": "index_stop", "bar": r[0], "index_ref": state["exit"]["px"],
                        "seen": "bar", "at": now.isoformat()}
            if "moved" in state:
                sh["index_stop"] = state["stop"]
                sh["stop_path"].append([r[0], state["stop"]])
                self.log(f"[shadow] noise_breakout {und} stop -> {state['stop']:.1f} "
                         f"at {r[0][11:16]} | NO ORDER SENT")
        return None

    def _nb_follow(self, S, und, rows, prof, now, closing=False):
        """The open shadow trade: every new completed bar through the trail
        (stop on the bar's low / high, ratchet on grid bars), the live index
        against the stop on every pass, the square-off; the exit at the bid.
        `closing`: the session has ended - whatever is open is recorded now."""
        L = self.L
        sh = S["open"]
        due = sh.get("exit_due")
        if rows and prof is not None and not due:
            due = self._nb_walk(sh, und, rows, prof, now)
        spot = self._spot(und)
        if not due and not closing and spot > 0 and (spot - sh["index_stop"]) * sh["side"] <= 0:
            bar = now.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+05:30")
            due = {"why": "index_stop", "bar": bar, "index_ref": sh["index_stop"], "seen": "tick",
                   "at": now.isoformat()}
        ttc = self.book.time_to_close(self.cfg.session_end)
        if not due and (closing or ttc <= float(L.squareoff_minutes)):
            bar = now.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+05:30")
            due = {"why": "squareoff", "bar": bar, "index_ref": spot, "seen": "clock",
                   "at": now.isoformat()}
        if due and not sh.get("exit_due"):
            due["index_spot"] = spot
            sh["exit_due"] = due
        bid = 0.0
        if sh["token"]:
            slot = self.feed.store.slot_of_token.get(sh["token"])
            bid = float(self.feed.store.bid[slot]) if slot is not None else 0.0
            if bid > 0:
                sh["best_bid"] = max(sh.get("best_bid") or bid, bid)
                sh["worst_bid"] = min(sh.get("worst_bid") or bid, bid)
                self._nb_premium_rules(sh, bid, now)
        due = sh.get("exit_due")
        if not due:
            return
        if sh["token"] and bid <= 0:
            # No quote to exit at. A minute of that is recorded as it is rather
            # than holding the index busy for the rest of the day - and never
            # past the session end (the 15:14 square-off cannot wait a minute):
            # from 6 s before it, or once it has passed, it is recorded now.
            waited = (now - datetime.fromisoformat(due["at"])).total_seconds()
            if waited < 60.0 and not closing and ttc > 0.1:
                return
            due["why"] += "_no_quote"
        q = sh["lot"]
        net = None
        if sh["token"] and bid > 0:
            cost = leg_cost(sh["entry"], q, "BUY", "OPT", self.cm) + leg_cost(bid, q, "SELL", "OPT", self.cm)
            net = round((bid - sh["entry"]) * q - cost, 2)
        pe = sh.get("prem_exit")
        sh.update(event="exit", done=True, exit=bid, why=due["why"], exit_bar=due["bar"],
                  exit_seen=due["seen"], index_exit_ref=due["index_ref"], index_exit=spot,
                  closed=now.isoformat(), net=net,
                  net_with_premium_rules=(pe["net"] if pe else net))
        S["open"] = None
        S["free_after"] = max(S["free_after"], due["bar"])
        self.log(f"[shadow] noise_breakout {sh['symbol'] or und} would EXIT"
                 + (f" @ {bid:.2f}" if sh["token"] and bid > 0 else "")
                 + f" ({due['why']}, index {spot:.1f}, stop {sh['index_stop']:.1f})"
                 + (f" net Rs {net:+,.0f}" if net is not None else "")
                 + (f"; premium rules would have left at {pe['bid']:.2f} ({pe['why']}) net Rs {pe['net']:+,.0f}"
                    if pe else "")
                 + f" | gates {'pass' if sh['gates']['all'] else 'fail'} | NO ORDER SENT")
        self._nb_journal(sh)

    def _nb_premium_rules(self, sh, bid, now):
        """For the record only: where the live premium stop and the +1R
        breakeven (arb/positions.py; research/lab/realistic.py reprice) would
        have taken the trade out. The shadow keeps following the index rule."""
        if sh.get("prem_exit") or not sh.get("prem_risk"):
            return
        stop = float(sh.get("prem_live_stop") or sh["prem_stop"])
        if not sh.get("prem_trailed") and bid - sh["entry"] >= sh["prem_risk"]:
            stop = max(stop, sh["entry"] + sh["cost_pts"])
            sh["prem_trailed"] = True
        sh["prem_live_stop"] = stop
        if bid <= stop:
            q = sh["lot"]
            cost = leg_cost(sh["entry"], q, "BUY", "OPT", self.cm) + leg_cost(bid, q, "SELL", "OPT", self.cm)
            sh["prem_exit"] = {"at": now.isoformat(), "bid": bid,
                               "why": "prem_trail" if sh.get("prem_trailed") else "prem_stop",
                               "net": round((bid - sh["entry"]) * q - cost, 2)}

    def _nb_journal(self, sh):
        jdir = getattr(self.journal, "dir", None)
        if not jdir:
            return
        try:
            with open(os.path.join(jdir, f"shadow_{sh['opened'][:10]}.jsonl"), "a") as fh:
                fh.write(json.dumps(sh, default=str) + "\n")
        except OSError as e:
            self.log(f"[shadow] journal write failed: {e}")

    def _nb_resume(self, st):
        """A restart mid-day: today's shadow journal says which noise trades
        are still open (followed again from their entry bar, so the trail is
        rebuilt from the candles) and where the last ones exited."""
        jdir = getattr(self.journal, "dir", None)
        if not jdir:
            return
        try:
            with open(os.path.join(jdir, f"shadow_{st['day']}.jsonl")) as fh:
                lines = [json.loads(x) for x in fh if x.strip()]
        except (OSError, ValueError):
            return
        opens, closed = {}, set()
        for r in lines:
            if r.get("playbook") != "noise_breakout":
                continue
            S = st["idx"].setdefault(r["und"], {"open": None, "free_after": "", "last_grid": "",
                                                "have_bar": "", "trades": 0})
            S["last_grid"] = max(S["last_grid"], r.get("decision_bar", ""))
            if r.get("event") == "exit":
                closed.add(r["id"])
                S["free_after"] = max(S["free_after"], r.get("exit_bar", ""))
            else:
                opens[r["id"]] = r
        for i, r in opens.items():
            if i in closed:
                continue
            S = st["idx"][r["und"]]
            r = dict(r, index_stop=r["index_stop0"], last_bar=r["decision_bar"],
                     stop_path=[[r["decision_bar"], r["index_stop0"]]], resumed=True)
            for k in ("exit_due", "prem_exit", "prem_trailed", "prem_live_stop", "gaps", "gap_wait",
                      "gaps_checked"):
                r.pop(k, None)
            S["open"] = r
            S["trades"] += 1
            self.log(f"[shadow] noise_breakout {r['und']}: resumed the open shadow trade from "
                     f"{r.get('t', r['decision_bar'][11:16])} | NO ORDER SENT")

    def hunt(self):
        try:
            self._refresh_funds()               # live: size on the account's money now
        except Exception as e:
            self.log(f"[capital] funds refresh error: {e!r}")
        try:
            self._shadow_lhm()                  # never sends an order
        except Exception as e:
            self.log(f"[shadow] last_half_hour error: {e!r}")
        try:
            if self._live_lhm():                # "on": enters through enter() at 15:00
                return
        except Exception as e:
            self.log(f"[intraday] last_half_hour error: {e!r}")
        try:
            self._shadow_noise()                # never sends an order
        except Exception as e:
            try:
                self._nb_log_once("hunt", f"[shadow] noise_breakout error: {e!r}")
            except Exception:
                pass
        # The stock scan runs on its own clock, whatever the gates say: the
        # dashboard should show what is in play even while a position is open.
        desk = self.stocks
        if desk is not None and desk.due():
            try:
                desk.pick(index_moves=self._index_moves(),
                          cost_ratio=self._stock_cost_ratio)
            except Exception as e:
                self.log(f"[stocks] scan failed: {e!r}")
        if desk is not None:
            # Every subscribed stock, once a minute: a sudden ATR spike joins the
            # picks at once instead of waiting for the next 30-minute rescan.
            try:
                desk.spike_tick(cost_ratio=self._stock_cost_ratio)
            except Exception as e:
                if not getattr(self, "_spike_err_logged", False):
                    self._spike_err_logged = True
                    self.log(f"[stocks] spike watch failed: {e!r}")
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
            # t0 BEFORE the copy: _closed_rows drops what was still forming then.
            t0 = time.time()
            rows = self.candles.get(und, limit=400) if self.candles else []
            # A structure engine with no candles is not a cautious engine, it is
            # a blind one, and every downstream gate will refuse for reasons
            # that sound like ordinary market conditions. Say it out loud, once
            # per index per outage, or a dead data feed reads as a quiet day.
            self._check_starved(und, rows)
            rows, wait = self._closed_rows(und, rows, t0)
            if wait:
                # Waiting for the next close is the normal state between bars,
                # not a refusal: counting it buried the real reasons on the
                # dashboard (about 3,000 a day against a few hundred real ones).
                if wait != "waiting_for_bar_close":
                    self._note(wait)
                continue
            st = analyse(rows, strength=self.L.swing_strength,
                         min_bars=self.L.min_structure_bars, limits=self.L)
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
                if self.selector.last_reject_kind == "cost":
                    self._note("no_affordable_strike")
                    self._unaffordable(und, available, self.selector.last_cheapest)
                else:
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

    def _take_on(self, symbol, held, adopt, note="", account=None):
        """The broker shows `held` from an entry the engine did not see fill:
        with adopt_unknown_entry an option is adopted under the software stop;
        either way new entries halt until a human has looked. `account`, when
        known, is the account's whole position in symbol - what the alert
        reports (review O-ADV-1)."""
        adopted = False
        if adopt is not None and held \
                and getattr(self.L, "adopt_unknown_entry", False):
            try:
                adopted = adopt(held)
            except Exception as e:
                self.log(f"[intraday] could not adopt {symbol}: {e!r}")
        if account is not None and account != held:
            shows = f"broker shows {account}, {held} of it from this entry"
        else:
            shows = f"broker shows {held}"
        shows += f", {note}" if note else ""
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
            more = st["filled"] - rec["booked"]
            if more <= 0:
                self._drop_entry(rec)
                return "done"
            account = None
            if hasattr(b, "order_rows"):
                # Taken on only as far as the account holds more than the
                # engine: fills an exit reconciliation has already sold must
                # not be taken on again (review O-ADV-1, 5 Oct 2026). So that
                # what the engine holds is known, an exit in the symbol is
                # settled first.
                if any(p.symbol == sym and not p.exit_blocked
                       and (p.pending_exit_oid or p.pending_exit_tag)
                       for p in self.book.snapshot()):
                    self._entry_stall(rec, now, f"{st['status']} with {more} more filled, "
                                                f"waiting for an exit in it to settle")
                    return "waiting"
                rows = b.order_rows(sym)
                if rows is None:
                    self._entry_stall(rec, now, "the order book cannot be read")
                    return "unreadable"
                account = b.signed_net(rows)
            self._drop_entry(rec)
            self.log(f"[intraday] entry order {rec['oid']} for {sym} {st['status']} with "
                     f"{more} filled that the engine did not see")
            take = more
            if account is not None:
                take = min(more, max(0, (account - self._engine_held(sym)) * rec["sign"]))
            if take <= 0:
                self._halt_alert(f"entry order {rec['oid']} for {sym} filled {more} the engine did "
                                 f"not see, but the broker shows {account} - nothing taken on; "
                                 f"check the Angel app",
                                 f"Entry order <code>{rec['oid']}</code> for <code>{sym}</code> "
                                 f"filled {more} the engine did not see, but the account holds "
                                 f"no more than the engine ({account}). Nothing taken on. "
                                 f"New entries halted.")
                return "done"
            if take < more:
                self.log(f"[intraday] {sym}: the account holds only {take} of them; "
                         f"{take} taken on")
            self._take_on(sym, rec["sign"] * take, rec["adopt"],
                          note=f"order {rec['oid']} {st['status']}", account=account)
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

    def _stock_round_trip(self, fill_px, q, bid, ask):
        """A cash stock's round trip for q shares, in rupees: exact fees both
        ways, a slippage tick each way and the spread."""
        return (leg_cost(fill_px, q, "BUY", "EQ", self.cm)
                + leg_cost(fill_px, q, "SELL", "EQ", self.cm)
                + 2 * self.cm.slippage_points() * q + (ask - bid) * q)

    def _stock_qty(self, risk, fill_px, bid, ask, available, cap_inr):
        """Shares for a stock entry: the fewest of (risk cap / stop distance)
        and (free capital x leverage / price); with risk_includes_costs, the
        largest size whose stop-out, costs included, fits the cap. Cost rises
        with size, so a bisection finds it exactly. Shared with the
        constraints page (arb/constraints.py) so the two cannot drift."""
        if risk <= 0 or fill_px <= 0:
            return 0
        lev = float(getattr(self.L, "stocks_leverage", 5.0))
        qty = min(int(cap_inr // risk), int(max(0.0, available) * lev // fill_px))
        if self._costs_inside() and qty > 0:
            lo_q, hi_q = 0, qty
            while lo_q < hi_q:
                mid = (lo_q + hi_q + 1) // 2
                if risk * mid + self._stock_round_trip(fill_px, mid, bid, ask) <= cap_inr:
                    lo_q = mid
                else:
                    hi_q = mid - 1
            qty = lo_q
        return max(0, qty)

    def _hunt_stocks(self, available):
        """The index decision layer, unchanged, on the stocks in play.

        Cash shares, intraday: a long is bought, a short is sold first. The
        size is the fewest of (risk cap / stop distance) and (free capital x
        leverage / price), and the trade must pay its round trip - fees both
        ways, a tick of slippage each way - by stocks_min_net_inr at the target.
        """
        L = self.L
        store = self.feed.store
        cap_inr = self.capital.capital * stock_risk_pct(L) / 100.0
        for pick in list(self.stocks.active):
            sym = pick.symbol
            info = self.stocks.universe.get(sym) or {}
            slot = self.stocks.slots.get(sym)
            if slot is None:
                continue
            t0 = time.time()
            rows = self.candles.get(sym, limit=400) if self.candles else []
            self.evaluated_at[sym] = time.time()
            rows, wait = self._closed_rows(sym, rows, t0)
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
            # The index desk's chase guard: a stock that has already moved
            # max_chase_atr from the closed bar's close is not chased (D-L2).
            if self._chased(rows, px):
                self._note("stock:price_moved_since_close")
                continue
            if self.careful and not grade_at_least(v.grade, getattr(L, "grade_after_streak", "B")):
                self._note("grade_too_low_after_streak")
                continue
            if time.time() - self._symbol_cooldown.get(info.get("eq_symbol", sym), -1e9) \
                    < L.symbol_cooldown_sec:
                self._note("symbol_cooldown")
                continue
            st = analyse(rows, strength=L.swing_strength, min_bars=L.min_structure_bars, limits=L)
            self.stock_structures[sym] = st
            lead = v.setups[0] if v.setups else None
            inv = structural_invalidation(st, v.side, lead.invalidation if lead else None)
            ref = st.closes[-1] if st.closes else px
            risk = abs(ref - inv) if inv else 0.0
            a = atr(rows[:-1], period=L.atr_period)
            if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
                self._note("stock:no_viable_stop")
                continue
            try:
                self._shadow_stock_option(sym, info, v.side, px, risk, getattr(lead, "name", ""))
            except Exception as e:
                if not getattr(self, "_sso_err_logged", False):
                    self._sso_err_logged = True
                    self.log(f"[shadow] stock option error: {e!r}")
            bid, ask = float(store.bid[slot]), float(store.ask[slot])
            if bid <= 0 or ask <= 0:
                self._note("stock:no_quote")
                continue
            fill_px = ask if v.side > 0 else bid
            qty = self._stock_qty(risk, fill_px, bid, ask, available, cap_inr)
            if qty <= 0:
                self._note("stock:risk_too_large")
                continue
            rt = self._stock_round_trip(fill_px, qty, bid, ask)
            if L.max_cost_frac_of_risk > 0 and rt > L.max_cost_frac_of_risk * risk * qty:
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

    # ── the stock's own option, shadow only ─────────────────────────────────
    def _shadow_stock_option(self, sym, info, side, px, risk_pts, playbook=""):
        """stocks_options = "shadow": for a stock setup that has a stop, price
        the stock's CE (long) / PE (short) off NSE's chain and log what the
        engine would buy - or why it cannot (one lot above capital, loss at the
        stop above the risk cap). The chain is fetched on a background thread
        (one at a time, reused for 3 minutes); a setup is logged at most once
        per stock per 10 minutes. Never sends an order."""
        if str(getattr(self.L, "stocks_options", "off")) != "shadow":
            return
        nse = getattr(self.stocks, "nse", None)
        if nse is None:
            return
        now = time.time()
        if not hasattr(self, "_sso_chain"):
            self._sso_chain, self._sso_last, self._sso_busy = {}, {}, False
        if now - self._sso_last.get(sym, -1e9) < 600:
            return
        got = (getattr(self.stocks, "chains", None) or {}).get(sym) or self._sso_chain.get(sym)
        if got is None or now - got[0] > 180:
            if not self._sso_busy:
                self._sso_busy = True

                def work():
                    try:
                        self._sso_chain[sym] = (time.time(), nse.option_chain(sym) or {})
                    except Exception:
                        self._sso_chain[sym] = (time.time(), {})
                    finally:
                        self._sso_busy = False
                threading.Thread(target=work, name="nse-chain", daemon=True).start()
            return                               # decided on a later pass, with the chain
        self._sso_last[sym] = now
        chain = got[1]
        from arb.stockspike import pick_stock_option
        cap_inr = self.capital.capital * stock_risk_pct(self.L) / 100.0
        lot = int(info.get("lot") or 0)
        choice, why = pick_stock_option(chain, side, risk_pts, self.capital.capital, cap_inr, lot, self.cm,
                                        otm=int(getattr(self.L, "stocks_option_strikes", 4))) \
            if chain else (None, [f"no NSE chain ({getattr(nse, 'last_error', '')[:60]})"])
        rec = {"playbook": "stock_option", "symbol": sym, "side": side, "setup": playbook,
               "stock_px": px, "stop_points": risk_pts, "lot": lot, "cap_inr": round(cap_inr, 2),
               "opened": now_ist().isoformat(), "choice": choice, "skipped": why}
        if choice:
            self.log(f"[shadow] stock option WOULD BUY {sym} {choice['strike']:g}{choice['opt']} "
                     f"{choice['expiry']} @ {choice['ask']:.2f} x{lot} (Rs {choice['lot_cost']:,.0f}) | "
                     f"delta {choice['delta']:.2f}, loss at stop Rs {choice['loss_at_stop']:,.0f} "
                     f"<= cap Rs {cap_inr:,.0f} | NO ORDER SENT")
        else:
            self.log(f"[shadow] stock option {sym} {'CE' if side > 0 else 'PE'}: nothing tradable - "
                     + "; ".join(why[:3]))
        jdir = getattr(self.journal, "dir", None)
        if jdir:
            try:
                with open(os.path.join(jdir, f"shadow_{rec['opened'][:10]}.jsonl"), "a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            except OSError:
                pass

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
        if self._ip_refused(legs):
            return False

        def adopt(held):
            # An unseen fill on the entry's side, taken on at the quote so the
            # software stop manages it (options already did this; stocks only halted).
            if held * v.side <= 0:
                return False
            n = abs(int(held))
            p = self._stock_position(sym, info, v, st, px, inv, risk, tgt, n, quote, rt, rows, pick)
            p.detail.update(adopted=True, entry_estimated=True)
            self.log(f"[intraday] ADOPTED {sym} x{n} @ ~{quote:.2f} (stop {inv:.2f})")
            return True
        if not legs[0].get("filled"):
            self._entry_working(legs[0], leg["symbol"], adopt=adopt, side=v.side)
            self._unknown_entry(legs[0], leg["symbol"], adopt=adopt, side=v.side)
            self._note("entry_not_filled")
            return False
        fill = float(legs[0]["fill_price"])
        qty = int(legs[0].get("qty") or qty)     # what the broker actually filled
        pos = self._stock_position(sym, info, v, st, px, inv, risk, tgt, qty, fill, rt, rows, pick)
        # Part filled and still working after its cancel: more of it may fill (O4).
        self._entry_working(legs[0], leg["symbol"], side=v.side,
                            adopt=lambda more: more * v.side > 0 and self._adopt_more(pos, abs(more)))
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

    def _stock_position(self, sym, info, v, st, px, inv, risk, tgt, qty, fill, rt, rows, pick):
        """Book a stock position - a seen fill or an adopted one - and return it."""
        L = self.L
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
        return pos

    def _unaffordable(self, und, free, cheapest):
        """A setup was ready and no eligible strike's lot fits the free capital.
        Live sizes from the account's funds, so a small balance refuses every
        setup all day; it read as a risk-cap refusal on the dashboard and said
        nothing else (11 Oct 2026). Say it once per index per day."""
        key = (now_ist().strftime("%Y-%m-%d"), und)
        if key in self._unaffordable_said:
            return
        self._unaffordable_said.add(key)
        mode = getattr(self.broker, "mode", "paper")
        lot = f"Rs {cheapest[0]:,.0f} ({cheapest[1]:,.0f} strike)" if cheapest else "unknown"
        self.log(f"[intraday] NO AFFORDABLE STRIKE: {und} setup ready but the cheapest eligible "
                 f"lot is {lot} against free capital Rs {free:,.0f} "
                 f"({mode}: capital Rs {self.capital.capital:,.0f}"
                 + (", from the account's available funds" if mode == "live" else "") + "). "
                 f"No {und} trade can be taken at this capital.")
        if self.notifier:
            self.notifier.send(
                f"\u26a0\ufe0f <b>NO AFFORDABLE STRIKE</b> {und}\n"
                f"A setup was ready, but the cheapest eligible lot is <code>{lot}</code> "
                f"and free capital is <code>Rs {free:,.0f}</code>."
                + (" Live sizes from the Angel account's available funds - add funds to trade."
                   if mode == "live" else ""))

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
        if plan.get("exit_rules"):
            # last_half_hour (and any later rule with the backtest's own exits):
            # no target of any kind - out at the index stop, the premium stop,
            # the +1R breakeven or the square-off (arb/positions.py review).
            pos.exit_rules = str(plan["exit_rules"])
            pos.index_target = 0.0
            pos.target = fill * 100.0
            pos.detail.update(playbook=plan.get("playbook", ""), index_target=0.0,
                              target_premium_est=None, exit_rules=pos.exit_rules)
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
            return held > 0 and self._adopt_entry(cand, plan, structure, zone, verdict, held)
        if self._ip_refused(legs):
            return False
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
        if pos.exit_rules == "lhm":
            return "none (index stop, +1R breakeven, square-off)"
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
            # An entry order still being followed may yet be taken on (its
            # fills wait while an exit in the symbol settles), so an empty book
            # is not the end while one is pending: stopping there left its
            # fills open at the broker (review reverify O, 5 Oct 2026).
            if (not left and not self._pending_entries) or \
                    not (0.0 <= end - time.time() <= self.SQUAREOFF_RETRY_SEC):
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
        opening = self._opening_capital()
        return {
            "capital": round(cap, 2),
            # Live: the account's funds as last read (None in paper).
            "account_funds": getattr(self.capital, "account_funds", None),
            "day_pnl": round(self.realised_pnl, 2),
            "day_pct": round(100.0 * self.realised_pnl / opening, 3) if opening else 0.0,
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
        return atr_ratio(rows, period=self.L.atr_period, baseline_bars=self.L.atr_baseline_bars,
                         no_gap=bool(getattr(self.L, "pace_no_gap", False)))
