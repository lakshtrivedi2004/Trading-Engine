"""Live web dashboard.

A threaded HTTP server that reads engine state from outside the scan loop. It
never writes, never locks, and never touches the hot path - the scanner does not
know it exists. State is assembled per request; the probe that computes
distance-to-profitable is throttled so a refreshing browser cannot drive it.
"""
import glob
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from arb import history as hist
from arb.capital_book import CapitalBook
from arb.instruments import IST, market_phase, now_ist, years_to_expiry
from arb.mathview import chain_math, term_structure
from arb.settlement import LegMarker, SettlementBook
from arb.fileio import write_json

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "dashboard.html")
REPO_ROOT = os.path.dirname(HERE)
LOG_PATH = os.path.join(REPO_ROOT, "engine.log")
# What "Set paper capital" accepts: under Rs 1,000 not even one lot's premium
# fits; over a crore is a typo.
PAPER_CAPITAL_RANGE = (1_000.0, 1_00_00_000.0)


def _log_source():
    """The engine's log: production's systemd-redirected engine.log
    (deployment/trading-engine.service), or the newest local dev session log
    (./run.sh writes logs/session_*.log) when that file does not exist."""
    if os.path.isfile(LOG_PATH):
        return LOG_PATH
    sessions = sorted(glob.glob(os.path.join(REPO_ROOT, "logs", "session_*.log")))
    return sessions[-1] if sessions else None


def _tail_lines(path, n=400, chunk=65536, max_bytes=8_000_000):
    """Last ``n`` lines of ``path`` without reading the whole file - a
    long-running engine.log can grow to hundreds of MB over weeks."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        data = b""
        read = 0
        while pos > 0 and data.count(b"\n") <= n and read < max_bytes:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            data = fh.read(step) + data
            read += step
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


class DashboardState:
    """Assembles a JSON snapshot of everything the page shows."""

    def __init__(self, engine, feed, risk, execution, cfg, mode, journal,
                 candles=None, margin=None, settlements=None):
        self.engine, self.feed, self.risk = engine, feed, risk
        self.execution, self.cfg, self.mode = execution, cfg, mode
        self.journal = journal
        self.candles = candles
        self.margin = margin
        self.settlements = settlements or SettlementBook()
        # Exit price and P&L per leg: live mid before expiry, intrinsic at the
        # recorded settlement level after it. None only for the placeholder
        # state built before any trading day has run yet (see intraday_mode).
        self.marker = LegMarker(feed.store, self.settlements, candles) \
            if feed is not None else None
        # Set by the intraday runner; when present the snapshot, the
        # history feed and the page all switch to that strategy. This can be
        # True while `intraday` (the engine) is still None - the process is
        # up and idling between trading days, with no engine/feed attached
        # yet - so the page must not fall back to the arb layout meant for
        # an always-attached engine.
        self.intraday_mode = False
        self.intraday = None
        # Why today's live start was refused ("" when it was not): shown as the
        # halt reason, with a Retry LIVE button, while the process holds.
        self.start_refused = ""
        # The paper ledger "Set paper capital" writes while no session runs
        # (a running engine's own CapitalBook takes it instead).
        self.paper_ledger = os.path.join(REPO_ROOT, "journal", "capital.json")
        self._hist = None
        self._hist_at = 0.0
        self.started = time.time()
        self.spots = {}
        self.trades = []
        self._probe = {}
        self._probe_at = 0.0
        self._math = None
        self._math_at = 0.0
        self._cons = None
        self._cons_at = 0.0
        self._lock = threading.Lock()

    def live_spot(self, name):
        """Index level from the live feed, falling back to the session's opening
        fetch when the spot slot has not ticked yet."""
        slots = getattr(self.feed, "spot_slots", {}) or {}
        s = slots.get(name)
        if s is not None:
            try:
                ltp = float(self.feed.store.ltp[s])
                if ltp > 0:
                    return ltp
            except Exception:
                pass
        return float(self.spots.get(name, 0.0))

    def constraints(self):
        """Every entry check against its current value (arb/constraints.py).
        Computed only when the page asks, and at most every 2 s however often
        it does, so an open tab cannot load the trading loop."""
        now = time.time()
        if self._cons is not None and now - self._cons_at < 2.0:
            return self._cons
        from arb import constraints
        e = getattr(self, "intraday", None)
        saved_path = os.path.join(REPO_ROOT, ".cache", "constraints_last.json")
        if e is None:
            # No session: thresholds from config, pace from cached candles, and
            # the per-instrument table as the last session left it on disk.
            saved = None
            try:
                with open(saved_path) as fh:
                    saved = json.load(fh)
            except (OSError, ValueError):
                pass
            try:
                from arb.config import IntradayLimits
                out = constraints.offline(self.cfg, IntradayLimits.load(), saved)
            except Exception as exc:
                out = {"waiting": True, "now": now_ist().strftime("%H:%M:%S"),
                       "session_start": self.cfg.session_start,
                       "error": f"{type(exc).__name__}: {exc}"}
        else:
            try:
                out = constraints.snapshot(e, self.cfg)
            except Exception as exc:
                # The engine mutates these dicts on its own thread; a read that
                # races it is retried on the next poll rather than trusted.
                out = {"error": f"{type(exc).__name__}: {exc}"}
            # Kept on disk (at most every 30 s, and only while the market is
            # open) so a restart after the close still has the day's last table.
            if "error" not in out and out.get("phase") == "open" \
                    and now - getattr(self, "_cons_saved_at", 0.0) >= 30.0:
                self._cons_saved_at = now
                try:
                    write_json(saved_path, dict(out, saved_at=now_ist().strftime("%Y-%m-%d %H:%M:%S")))
                except OSError:
                    pass
        self._cons, self._cons_at = out, now
        return out

    def mathematics(self):
        if getattr(self, 'intraday', None) is not None:
            return {}
        """Every chain evaluated against the pricing integrals, plus each index's
        futures curve. Throttled: a page polling once a second must not make the
        server redo eight chains of finite differences each time."""
        now = time.time()
        if self._math is not None and now - self._math_at < 2.0:
            return self._math
        self._math_at = now
        store = self.feed.store
        now_ns = time.perf_counter_ns()
        chains, curves = [], []
        for b in self.engine.bindings:
            spot = self.live_spot(b.underlying)
            rate = self.engine.implied_rates.get(f"{b.underlying} {b.expiry}",
                                                 self.cfg.risk_free_rate)
            try:
                cm = chain_math(b, store, spot, rate, self.risk.limits, now_ns)
            except Exception as e:
                cm = {"underlying": b.underlying, "expiry": b.expiry, "error": str(e)}
            if cm:
                chains.append(cm)
        futs = getattr(self.feed, "future_slots", {}) or {}
        for name, rows in futs.items():
            spot = self.live_spot(name)
            box_rates = {k.split(" ", 1)[1]: v for k, v in self.engine.implied_rates.items()
                         if k.startswith(name + " ")}
            try:
                curves.append(term_structure(name, rows, store, spot,
                                             self.cfg.risk_free_rate, box_rates,
                                             years_to_expiry))
            except Exception as e:
                curves.append({"underlying": name, "error": str(e), "futures": [], "pairs": []})
        self._math = {"now": now_ist().strftime("%H:%M:%S"), "chains": chains,
                      "curves": curves,
                      "band": [self.risk.limits.min_excess_return_pa,
                               self.risk.limits.max_excess_return_pa],
                      "min_tenor_days": self.risk.limits.min_tenor_days,
                      "stake": self.risk.limits.stake_per_trade,
                      "costs": self.risk.cm.__dict__}
        return self._math

    def record_trade(self, fill):
        o = fill["opportunity"]
        with self._lock:
            self.trades.insert(0, {
                "id": fill["trade_id"],
                "time": fill.get("executed_at", fill["ts_ist"][11:19]),
                "leg_spread_ms": fill.get("leg_spread_ms", 0.0),
                "strategy": o["strategy"],
                "underlying": o.get("underlying", ""),
                "description": o["description"],
                "expiry": o["expiry"],
                "lots": fill["lots"],
                "gross": round(fill["gross_inr"], 2),
                "cost": round(fill["cost_inr"], 2),
                "slip": round(fill.get("realised_slippage_inr", 0.0), 2),
                "net": round(fill["net_inr"], 2),
                "capital": round(fill.get("capital_inr", 0.0), 2),
                "capital_source": fill.get("capital_source", ""),
                "margin_inr": round(fill.get("margin_inr", 0.0) or 0.0, 2),
                "settlement_inr": round(fill.get("settlement_inr", 0.0) or 0.0, 2),
                "return_pct": fill.get("return_pct", 0.0),
                "filled": fill["all_filled"],
                "detect_ms": round(fill["latency_ms"]["detect_to_order"], 2),
                "legs": [{"side": l["side"], "symbol": l["symbol"],
                          "token": str(l.get("token", "")), "strike": l.get("strike", 0.0),
                          "qty": l["qty"], "price": round(l["fill_price"], 2),
                          "filled": bool(l.get("filled")),
                          "opt": l.get("opt", ""),
                          "quoted_at": l.get("quoted_at", ""),
                          "ms_before": round(l.get("ms_before_fire", 0.0))}
                         for l in fill["legs"]],
            })
            del self.trades[40:]

    def history(self, days=30):
        """Rolled-up paper-run history. Re-read from disk on a slow cadence -
        the files only change when a trade lands, and a browser refreshing once a
        second must not re-parse a month of journals each time."""
        now = time.time()
        if self._hist is None or now - self._hist_at > 3.0:
            self._hist_at = now
            try:
                trades = hist.load_trades(days=days)
                summary = hist.summarise(trades, self.risk.limits.total_capital,
                                         self.risk.limits.stake_per_trade)
                today = now_ist().strftime("%Y-%m-%d")
                dates = [d["date"] for d in summary["days"]]
                summary["intraday"] = hist.intraday_curve(trades, today)
                summary["dates"] = dates
                summary["by_day"] = {d: [self.marker.enrich(t) for t in hist.day_trades(trades, d)]
                                     for d in dates[-7:]}
                self._hist = summary
            except Exception as e:
                self._hist = {"error": str(e), "days": [], "equity_curve": [],
                              "totals": {}, "intraday": [], "dates": [], "by_day": {}}
        return self._hist

    def probe(self):
        now = time.time()
        if now - self._probe_at > 1.5:
            self._probe_at = now
            try:
                self._probe = self.engine.probe()
            except Exception:
                pass
        return self._probe

    def chain(self, limit=7):
        """Strikes nearest the money for each expiry, with quote ages."""
        store = self.feed.store
        now_ns = time.perf_counter_ns()
        out = []
        for b in self.engine.bindings:
            n = len(b.strikes)
            mid = n // 2
            spot = self.spots.get(b.underlying, 0.0)
            if spot:
                mid = min(range(n), key=lambda i: abs(b.strikes[i] - spot))
            lo = max(0, mid - limit // 2)
            rows = []
            for i in range(lo, min(n, lo + limit)):
                ce, pe = int(b.ce_slots[i]), int(b.pe_slots[i])
                rows.append({
                    "strike": float(b.strikes[i]),
                    "ce_bid": round(float(store.bid[ce]), 2),
                    "ce_ask": round(float(store.ask[ce]), 2),
                    "ce_age": round((now_ns - int(store.recv_ns[ce])) / 1e6),
                    "pe_bid": round(float(store.bid[pe]), 2),
                    "pe_ask": round(float(store.ask[pe]), 2),
                    "pe_age": round((now_ns - int(store.recv_ns[pe])) / 1e6),
                    "atm": i == mid,
                })
            fut = None
            if b.fut_slot is not None:
                fs = b.fut_slot
                fut = {"symbol": b.fut_symbol,
                       "bid": round(float(store.bid[fs]), 2),
                       "ask": round(float(store.ask[fs]), 2)}
            out.append({"underlying": b.underlying, "segment": b.segment,
                        "expiry": b.expiry, "lot": b.lot_size,
                        "rows": rows, "future": fut,
                        "implied_rate": self.engine.implied_rates.get(
                            f"{b.underlying} {b.expiry}")})
        return out

    @staticmethod
    def _levels(p):
        """The levels that actually manage a position, live.

        With an index target the premium target is parked out of reach
        (entry x 100) so the index level decides the exit; that parked number
        is not a price and must not be shown as one. What is shown instead is
        the index target and stop as they stand now (both move: extensions,
        trailing), and the premium the target should be worth, at the points
        per index point measured at entry rather than the chain delta.
        """
        if p.index_target <= 0 or p.index_entry <= 0:
            return {"index_target": 0.0, "index_stop": round(p.index_invalidation, 2),
                    "target_est": None, "extensions": p.extensions}
        d = p.detail or {}
        ppp = p.delta
        est0, tgt0 = d.get("target_premium_est"), d.get("index_target")
        if est0 and tgt0 and abs(float(tgt0) - p.index_entry) > 0:
            ppp = (float(est0) - p.entry) / abs(float(tgt0) - p.index_entry)
        # A stock is its own underlying: its target is a share price already.
        est = None if p.opt == "EQ" else p.entry + abs(p.index_target - p.index_entry) * ppp
        return {"index_target": round(p.index_target, 2),
                "index_stop": round(p.index_invalidation, 2),
                "target_est": round(est, 2) if est else None,
                "extensions": p.extensions}

    def _last_day(self):
        """The most recent trading day's closed trades, from the journal the
        engine writes - so an idle process (after the close, or restarted
        before the next session) shows that day's figures instead of zeros.
        They stay up until the next day's session_start, then clear.

        Returns (day, records) or (None, []). Re-read every few seconds, not
        on every page poll."""
        now = time.time()
        if getattr(self, "_last_day_at", 0.0) and now - self._last_day_at < 3.0:
            return self._last_day_cache
        self._last_day_at = now
        self._last_day_cache = (None, [])
        jdir = os.path.join(REPO_ROOT, "journal", "live" if self.mode == "live" else "")
        for path in sorted(glob.glob(os.path.join(jdir, "trades_*.jsonl")), reverse=True):
            day = os.path.basename(path)[len("trades_"):-len(".jsonl")]
            try:
                with open(path) as fh:
                    recs = [json.loads(l) for l in fh if l.strip()]
            except (OSError, ValueError):
                continue
            recs = [r for r in recs if "exit_reason" in r]
            if not recs:
                continue
            try:
                hh, mm = (int(x) for x in self.cfg.session_start.split(":"))
                reset = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)) \
                    .replace(hour=hh, minute=mm, tzinfo=IST)
            except ValueError:
                break
            if now_ist() < reset:
                self._last_day_cache = (day, recs)
            break
        return self._last_day_cache

    def _idle_day_stats(self):
        """Dashboard figures for the last day, rebuilt from its journal."""
        cap = round(getattr(self, "boot_capital", 0.0), 2)
        day, recs = self._last_day()
        try:
            from arb.config import IntradayLimits
            pct = IntradayLimits.load().daily_target_pct
        except Exception:
            pct = 0.0
        if not recs:
            tgt = round(cap * pct / 100.0, 2)
            return {"capital": cap, "day_pnl": 0.0, "day_pct": 0.0,
                    "target_inr": tgt, "target_pct": pct, "target_hit": False,
                    "progress_pct": 0.0, "trades": 0, "wins": 0, "losses": 0,
                    "recent_trades": []}
        # The day's opening capital, as the engine showed it all day - not the
        # post-close figure that already has the day's P&L folded in.
        cap = round(float(recs[0].get("capital") or cap), 2)
        pnl = round(sum(float(r.get("net_inr") or 0.0) for r in recs), 2)
        wins = sum(1 for r in recs if float(r.get("net_inr") or 0.0) >= 0)
        tgt = round(cap * pct / 100.0, 2)
        return {"capital": cap, "day_pnl": pnl,
                "day_pct": round(100.0 * pnl / cap, 3) if cap else 0.0,
                "target_inr": tgt, "target_pct": pct, "target_hit": pnl >= tgt > 0,
                "progress_pct": round(100.0 * pnl / tgt, 1) if tgt else 0.0,
                "trades": len(recs), "wins": wins, "losses": len(recs) - wins,
                "recent_trades": recs[-12:][::-1], "stats_day": day}

    def intraday_snapshot(self):
        """State for the intraday strategy. The arbitrage figures - implied
        rates, the return band, stake, capital utilisation - have no meaning
        here and are deliberately absent rather than shown as zero."""
        if self.intraday is None:
            # Idling between trading days: no engine/feed exists yet (either
            # this process has not run a single day yet, or it is between
            # yesterday's close and the next session_start). The page shows
            # the same shape as a normal day - last known spots, capital,
            # controls - just with everything at rest, instead of crashing on
            # a None engine or hiding the whole page.
            return {"strategy": "intraday", "waiting": True,
                    "now": now_ist().strftime("%H:%M:%S"),
                    "phase": market_phase(start=self.cfg.session_start,
                                          end=self.cfg.session_end),
                    "mode": self.mode, "session_start": self.cfg.session_start,
                    "connected": False, "uptime_s": 0,
                    "spots": {k: round(v, 2) for k, v in (self.spots or {}).items()},
                    "indices": sorted((self.spots or {}).keys()),
                    "ticks": 0, "tick_rate": 0.0, "passes": 0,
                    "minutes_to_close": 0.0,
                    **self._idle_day_stats(),
                    # What the next session trades (the tile keeps the last
                    # day's opening figure until then).
                    "paper_capital": round(getattr(self, "boot_capital", 0.0), 2),
                    "open_positions": [],
                    "halted": False, "halt_reason": "",
                    "rejects": {}, "structures": {}, "confluence": {},
                    "limits": {}}
        e, f = self.intraday, self.feed
        up = time.time() - self.started
        s = e.summary()
        book = [{
            "trade_id": p.trade_id, "symbol": p.symbol, "opt": p.opt,
            "strike": p.strike, "underlying": p.underlying, "qty": p.qty,
            "entry": round(p.entry, 2), "mark": round(p.mark, 2),
            "stop": round(p.stop, 2), "initial_stop": round(p.initial_stop, 2),
            "target": round(p.target, 2), "trailed": p.trailed,
            "r": round(p.r_multiple, 2),
            "pnl_points": round(p.pnl_points(), 2),
            "pnl_inr": round(p.pnl_inr(), 2),
            "mae": round(p.mae, 2), "mfe": round(p.mfe, 2),
            "held_s": int(time.time() - p.opened_at),
            "detail": p.detail,
            **self._levels(p),
        } for p in e.book.snapshot()]
        return {
            "strategy": "intraday",
            "now": now_ist().strftime("%H:%M:%S"),
            "phase": market_phase(start=self.cfg.session_start, end=self.cfg.session_end),
            "mode": self.mode,
            "connected": bool(f.connected.is_set()) if hasattr(f, "connected") else True,
            "uptime_s": int(up),
            "spots": {k: round(self.live_spot(k), 2) for k in self.spots},
            "indices": sorted({b.underlying for b in e.bindings}),
            "ticks": f.ticks,
            "tick_rate": round(f.ticks / up, 1) if up > 1 else 0.0,
            "passes": e.passes,
            "minutes_to_close": round(e.book.time_to_close(self.cfg.session_end), 1),
            "capital": s["capital"],
            "paper_capital": s["capital"],
            "day_pnl": s["day_pnl"],
            "day_pct": s["day_pct"],
            "target_inr": s["target_inr"],
            "target_pct": s["target_pct"],
            "target_hit": s["target_hit"],
            "progress_pct": round(100.0 * s["day_pnl"] / s["target_inr"], 1)
                            if s["target_inr"] else 0.0,
            "trades": s["trades"], "wins": s["wins"], "losses": s["losses"],
            "open_positions": book,
            "halted": s["halted"], "halt_reason": s["halt_reason"],
            "rejects": s["rejects"],
            "structures": s["structures"],
            # What the decision layer is actually seeing right now: which named
            # setups have completed per index, and why a trade was refused.
            # Without this the dashboard shows a system doing nothing and gives
            # no way to tell a quiet market from a broken one.
            "confluence": s["confluence"],
            "limits": {
                "max_strike_offset": e.L.max_strike_offset,
                "reward_risk": e.L.reward_risk,
                "trail_trigger_r": e.L.trail_trigger_r,
                "allow_selling": e.L.allow_selling,
                "min_net_points": e.L.min_net_points,
                "max_daily_loss_pct": e.L.max_daily_loss_pct,
                "squareoff_minutes": e.L.squareoff_minutes,
            },
            "recent_trades": e.records[-12:][::-1],
            "lhm": self._lhm(e),
        }

    @staticmethod
    def _lhm(e):
        """The day's last_half_hour decision - the rule, the AI, the outcome."""
        try:
            return e.lhm_status() if hasattr(e, "lhm_status") else None
        except Exception as ex:
            return {"error": repr(ex)[:200]}

    def snapshot(self):
        if getattr(self, "intraday_mode", False):
            d = self.intraday_snapshot()
            if self.start_refused:
                d.update(halted=True, halt_reason=self.start_refused,
                         start_refused=self.start_refused)
            return d
        e, f, r = self.engine, self.feed, self.risk
        st = e.stats.summary()
        h = e.quote_health
        legs = max(1, h["legs_seen"])
        up = time.time() - self.started
        return {
            "now": now_ist().strftime("%H:%M:%S"),
            "phase": market_phase(start=self.cfg.session_start, end=self.cfg.session_end),
            "mode": self.mode,
            "connected": bool(f.connected.is_set()) if hasattr(f, "connected") else True,
            "uptime_s": int(up),
            "spots": {k: round(self.live_spot(k), 2) for k in self.spots},
            "indices": sorted({b.underlying for b in self.engine.bindings}),
            "chains": len(self.engine.bindings),
            "ticks": f.ticks,
            "dropped": f.dropped,
            "reconnects": getattr(f, "reconnects", 0),
            "stalls": getattr(f, "stalls", 0),
            "quiet_s": round((time.perf_counter_ns() - f.last_tick_ns) / 1e9, 1)
                       if f.last_tick_ns else None,
            "tick_rate": round(f.ticks / up, 1) if up > 1 else 0.0,
            "passes": st["passes"],
            "chains_scanned": e.chains_scanned,
            "chains_skipped": e.chains_skipped,
            "scan_p50_us": st["scan_us_p50"],
            "scan_p99_us": st["scan_us_p99"],
            "quote_health": {k: round(100.0 * h[k] / legs, 1)
                             for k in ("tradable", "stale", "thin", "one_sided")},
            "legs_seen": h["legs_seen"],
            "implied_rates": {k: round(v, 4) for k, v in e.implied_rates.items()},
            "signals": e.signals_by_strategy,
            "rejects": r.state.rejects,
            "trades_today": r.state.trades_today,
            "session_pnl": round(r.state.session_pnl, 2),
            "total_capital": r.limits.total_capital,
            "equity": round(r.equity, 2),
            "capital_deployed": round(r.state.capital_deployed, 2),
            "capital_free": round(r.capital_free, 2),
            "day_pct": round(100.0 * r.state.session_pnl / r.limits.total_capital, 3)
                       if r.limits.total_capital else 0.0,
            "stake": r.limits.stake_per_trade,
            "stake_pct": round(100.0 * r.state.session_pnl / r.limits.stake_per_trade, 3)
                         if r.limits.stake_per_trade else 0.0,
            "return_band": [r.limits.min_excess_return_pa, r.limits.max_excess_return_pa],
            "halted": r.state.halted,
            "halt_reason": r.state.halt_reason,
            "probe": self.probe(),
            "chain": self.chain(),
            "recent_trades": [self.marker.enrich(dict(t, legs=[dict(l) for l in t["legs"]]))
                              for t in self.trades[:12]],
            "strategies": list(self.cfg.strategies),
            "margin": self.margin.snapshot() if self.margin is not None else None,
        }


def switch_target(path, mode):
    """The mode a switch request asks for: ?to=paper|live when given (Retry
    LIVE after a refused start is live -> live), else the other mode."""
    to = (parse_qs(urlparse(path).query).get("to") or [""])[0]
    if to in ("paper", "live"):
        return to
    return "paper" if mode == "live" else "live"


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                                   # never spam the trading console

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                if self.path.startswith("/api/state"):
                    self._send(json.dumps(state.snapshot()).encode(),
                               "application/json")
                elif self.path.startswith("/api/history"):
                    self._send(json.dumps(state.history()).encode(),
                               "application/json")
                elif self.path.startswith("/api/math"):
                    self._send(json.dumps(state.mathematics()).encode(),
                               "application/json")
                elif self.path.startswith("/api/candles"):
                    rows = state.candles.all(180) if state.candles else {}
                    self._send(json.dumps(rows).encode(), "application/json")
                elif self.path.startswith("/api/logs"):
                    q = parse_qs(urlparse(self.path).query)
                    n = max(50, min(5000, int(q.get("lines", ["400"])[0])))
                    src = _log_source()
                    body = {"path": src or "", "available": bool(src),
                            "lines": _tail_lines(src, n) if src else []}
                    self._send(json.dumps(body).encode(), "application/json")
                elif self.path in ("/logs", "/logs.html"):
                    with open(os.path.join(HERE, "logs.html"), "rb") as fh:
                        self._send(fh.read(), "text/html; charset=utf-8")
                elif self.path.startswith("/api/constraints"):
                    self._send(json.dumps(state.constraints()).encode(), "application/json")
                elif self.path in ("/constraints", "/constraints.html"):
                    with open(os.path.join(HERE, "constraints.html"), "rb") as fh:
                        self._send(fh.read(), "text/html; charset=utf-8")
                elif self.path in ("/", "/index.html"):
                    page = PAGE
                    if getattr(state, "intraday_mode", False):
                        alt = os.path.join(os.path.dirname(PAGE), "intraday.html")
                        if os.path.isfile(alt):
                            page = alt
                    with open(page, "rb") as fh:
                        self._send(fh.read(), "text/html; charset=utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                try:
                    self._send(json.dumps({"error": str(exc)}).encode(),
                               "application/json")
                except Exception:
                    pass

        def _control(self, body):
            self._send(json.dumps(body).encode(), "application/json")

        def _control_intraday(self, fn, action):
            e = getattr(state, "intraday", None)
            if e is None:
                msg = ("no trading session running right now (market closed / "
                       "idle between sessions)" if getattr(state, "intraday_mode", False)
                       else "no intraday engine running (arb strategy is active)")
                return self._control({"ok": False, "action": action, "error": msg})
            fn(e)
            self._control({"ok": True, "action": action})

        def _switch_mode(self):
            """Flip .trading_mode and restart the single trading-engine unit.

            Runs as a detached transient unit (systemd-run), not a child of this
            process: the switch script restarts the very unit this process may
            be running under, so it must sit outside this unit's cgroup to
            survive that restart.
            """
            target = switch_target(self.path, state.mode)
            # The script must restart the unit THIS process runs under: prod
            # (trading-engine) and dev (trading-engine-dev) share this code, and
            # switch_mode.sh on dev would rewrite dev's mode but restart prod.
            name = "switch_mode.sh"
            try:
                with open("/proc/self/cgroup") as fh:
                    if "trading-engine-dev.service" in fh.read():
                        name = "switch_mode_dev.sh"
            except OSError:
                pass
            script = os.path.join(os.path.dirname(HERE), "deployment", name)
            unit = f"trading-switch-{int(time.time() * 1000)}"
            try:
                # systemd-run returns once the transient unit is started; a
                # failure there used to be discarded and the page said
                # "Switching..." while nothing happened.
                r = subprocess.run(
                    ["systemd-run", "--collect", f"--unit={unit}",
                     "/bin/bash", script, target],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, timeout=15)
            except FileNotFoundError:
                return self._control({"ok": False,
                    "error": "systemd-run not found - mode switching only works "
                             "on the deployed host (systemd), not this environment"})
            except subprocess.TimeoutExpired:
                return self._control({"ok": False,
                    "error": f"systemd-run did not answer in 15 s; check: journalctl -u {unit}"})
            if r.returncode != 0:
                return self._control({"ok": False,
                    "error": f"systemd-run failed ({r.returncode}): {(r.stderr or '').strip()[:300]}"})
            self._control({"ok": True, "switching_to": target})

        def _paper_capital(self):
            """Paper only, scalper page only: trade the capital the owner names.

            A running session's ledger takes it at once - the next sizing, the
            risk cap, the daily target and the loss halt use it. Idle, the
            paper ledger on disk does, and the next session opens on it.
            Refused while a position is open: its stop and size were set on
            the old figure."""
            if not getattr(state, "intraday_mode", False):
                return self._control({"ok": False,
                    "error": "paper capital is set for the intraday scalper only"})
            if state.mode != "paper":
                return self._control({"ok": False,
                    "error": "live trades the account's funds - switch to PAPER to set its capital"})
            raw = (parse_qs(urlparse(self.path).query).get("capital") or [""])[0]
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                amount = float("nan")
            lo, hi = PAPER_CAPITAL_RANGE
            if not lo <= amount <= hi:
                return self._control({"ok": False,
                    "error": f"capital must be Rs {lo:,.0f} to Rs {hi:,.0f}, got {raw!r}"})
            e = getattr(state, "intraday", None)
            if e is not None and e.book.count():
                return self._control({"ok": False,
                    "error": "a position is open - set the capital once it has closed"})
            if e is not None:
                traded = e.capital.set_capital(amount)
                e.log(f"[capital] paper capital set to Rs {traded:,.2f} on the dashboard")
            else:
                traded = CapitalBook(amount, path=state.paper_ledger).set_capital(amount)
                # CapitalBook logs a failed save and carries on; the page must not.
                if CapitalBook(0.0, path=state.paper_ledger).chosen != round(amount, 2):
                    return self._control({"ok": False,
                        "error": f"could not save {state.paper_ledger}"})
            state.boot_capital = traded
            self._control({"ok": True, "capital": round(traded, 2)})

        def do_POST(self):
            # A web page on another site can make the browser POST here (the
            # controls include switching to LIVE); the dashboard's own buttons
            # send an Origin naming this host. Tools without an Origin pass.
            origin = urlparse(self.headers.get("Origin") or "").netloc
            if origin and origin != (self.headers.get("Host") or ""):
                self.send_response(403)
                self.end_headers()
                return
            try:
                if self.path == "/api/control/exit_all":
                    self._control_intraday(lambda e: e.manual_exit_all(), "exit_all")
                elif self.path == "/api/control/stop_trading":
                    self._control_intraday(
                        lambda e: e.manual_stop("manual stop (dashboard)"), "stop_trading")
                elif urlparse(self.path).path == "/api/control/switch_mode":
                    self._switch_mode()
                elif urlparse(self.path).path == "/api/control/paper_capital":
                    self._paper_capital()
                else:
                    self.send_response(404)
                    self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                try:
                    self._control({"ok": False, "error": str(exc)})
                except Exception:
                    pass
    return Handler


class Dashboard:
    def __init__(self, state, port=8089, host="0.0.0.0"):
        self.state = state
        self.port, self.host = port, host
        self.httpd = None
        self._thread = None

    def start(self):
        self.httpd = ThreadingHTTPServer((self.host, self.port),
                                         make_handler(self.state))
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="dashboard", daemon=True)
        self._thread.start()
        return self

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
