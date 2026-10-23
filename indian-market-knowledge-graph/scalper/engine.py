"""Live / paper scalping engine.

    SmartStream ticks ─► BarBuilder ─► features ─► regime ─► 5 setups
                                                      │
                     strategy gate (walk-forward stats on the last N days)
                                                      │
       sizing: stop-out loss incl. charges <= min(₹35, budget left)
                                                      │
          net R:R + expectancy check ─► marketable LIMIT entry ─► broker SL-L
                                                      │
       tick-level target / stop / breakeven / ATR trail / time stop / EOD flatten
                                                      │
                  DAILY KILL SWITCH at -₹75 (realised + open, after charges)
"""
from __future__ import annotations

import csv
import json
import os
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from . import backtest, context
from .broker import AngelClient, load_instruments
from .config import Config
from .costs import trade_costs
from .execution import LiveExecutor, PaperExecutor
from .feed import IST, BarBuilder, SmartStream, Tick
from .features import add_features, index_bias
from .risk import DayRisk, Gate, Position, kill_file, size_signal
from .options import INDICES, OptionChain, plan_option
from .strategies import Signal, regime, scan

log = logging.getLogger("scalper.engine")


class _EventTap(logging.Handler):
    """Keeps the last log lines in memory for the dashboard."""

    def __init__(self, n: int = 80):
        super().__init__(logging.INFO)
        self.events: deque = deque(maxlen=n)

    def emit(self, rec: logging.LogRecord):
        self.events.append({"t": datetime.fromtimestamp(rec.created).strftime("%H:%M:%S"),
                            "lvl": rec.levelname, "msg": rec.getMessage()})


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


class Telegram:
    def __init__(self, env: dict):
        self.token, self.chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")

    def send(self, text: str):
        if not (self.token and self.chat):
            return

        def _post():
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat, "text": text}, timeout=5)
            except requests.RequestException:
                pass
        threading.Thread(target=_post, daemon=True).start()


class Engine:
    def __init__(self, cfg: Config, env: dict, live: bool = False):
        self.cfg, self.env, self.live = cfg, env, live
        self.client = AngelClient(env)
        self.exec = LiveExecutor(cfg, self.client) if live else PaperExecutor(cfg)
        self.tg = Telegram(env)
        self.ticks: "queue.Queue[Tick]" = queue.Queue()
        self.builder = BarBuilder()
        self.bars: dict[str, pd.DataFrame] = {}
        self.feats: dict[str, pd.DataFrame] = {}
        self.index_bars = pd.DataFrame()
        self.ltp: dict[str, float] = {}
        self.pos: Position | None = None
        self.stop_crossed_at: float | None = None
        self.fired: dict[str, set] = {}
        self.cooldown: dict[str, int] = {}
        self.last_scan_minute: datetime | None = None
        self.last_reconcile = self.last_sl_poll = 0.0
        self._scan_report_at = time.monotonic()
        self._scan_counts = {
            "bars": 0, "signals": 0, "gate_rejects": 0, "budget_rejects": 0,
            "size_rejects": 0, "sized": 0, "option_signals": 0, "option_rejects": 0,
        }
        self._kill_done = False
        self.static_ip = env.get("STATIC_IP", "").strip()
        self.ip_block: str | None = None
        self.order_block: str | None = None   # set after repeated broker rejections
        self.last_ip_check = 0.0
        self.today = now_ist().date()
        self.risk = DayRisk(cfg, self.today)
        self.ctx = context.DayContext(self.today)
        self.tag = "LIVE" if live else "PAPER"
        for d in (cfg.state_dir, cfg.data_dir, cfg.log_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.state_path = cfg.state_dir / f"day_{self.today:%Y%m%d}_{self.tag.lower()}.json"
        self.journal = cfg.log_dir / f"trades_{self.tag.lower()}.csv"
        self.status_path = cfg.state_dir / f"status_{self.tag.lower()}.json"
        self.started = now_ist()
        self.curve: list[tuple[str, float]] = []
        self.last_status = 0.0
        self.bias = 0
        self.phase = "starting"
        self.tap = _EventTap()
        logging.getLogger("scalper").addHandler(self.tap)

    # ------------------------------------------------------------ persistence
    def _load_day(self):
        if self.state_path.exists():
            j = json.loads(self.state_path.read_text())
            self.risk.realized, self.risk.trades = j["realized"], j["trades"]
            log.info("resuming day: realised %.2f over %d trades", self.risk.realized, self.risk.trades)
            if self.risk.check_kill():
                raise SystemExit(f"daily loss limit already used: {self.risk.kill_reason}")

    def _save_day(self):
        self.state_path.write_text(json.dumps({"realized": round(self.risk.realized, 2), "trades": self.risk.trades}))

    def _journal(self, row: dict):
        new = not self.journal.exists()
        with self.journal.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    # ------------------------------------------------------------ setup
    def setup(self):
        if kill_file(self.cfg, self.today, self.tag).exists():
            raise SystemExit(f"Kill switch already fired today ({kill_file(self.cfg, self.today, self.tag).name}). Not trading again today.")
        self.client.login()
        self.inst = load_instruments(self.cfg.data_dir, self.cfg.universe if self.cfg.trade_stocks else [], self.cfg.exchange)
        self.spot = {u: {"token": INDICES[u][1], "exchange": INDICES[u][0]} for u in self.cfg.indices if u in INDICES}
        if not self.inst and not self.spot:
            raise SystemExit("no instruments resolved")
        self.tok2sym = {v["token"]: k for k, v in self.inst.items()}
        self.tok2sym.update({v["token"]: u for u, v in self.spot.items()})
        self.chain = OptionChain(self.cfg.data_dir / f"scrip_master_{now_ist():%Y%m%d}.json") if self.spot else None
        if self.spot and not self.cfg.aggressive:
            log.warning("index options (%s) trade only with --aggressive: option P&L is not part of the backtest gate",
                        ", ".join(self.spot))
        self._load_day()
        self.ctx = context.load(self.today)
        log.info("day context: %s | VIX prev close %s | budget x%.2f", ", ".join(self.ctx.tags()) or "normal day",
                 self.ctx.vix_prev_close, self.ctx.budget_mult)
        if self.ctx.holiday or (self.ctx.no_trade_reason and "event" in self.ctx.no_trade_reason):
            raise SystemExit(f"not trading today - {self.ctx.no_trade_reason}")
        if self.live:
            rms = self.client.rms()
            avail = float(rms.get("availablecash") or rms.get("net") or 0)
            log.info("broker available cash: %.2f", avail)
            if avail < self.cfg.capital * 0.5:
                log.warning("available cash %.0f is below half the configured capital %.0f", avail, self.cfg.capital)
            stray = {str(p.get("symboltoken")): (p.get("tradingsymbol"), int(float(p.get("netqty") or 0)))
                     for p in self.client.positions() if int(float(p.get("netqty") or 0)) != 0}
            if stray and not self.cfg.allow_existing:
                raise SystemExit(f"Open positions already exist {dict(stray.values())}. Close them in the Angel app, "
                                 "or start with --allow-existing to leave them alone.")
            if stray:
                self.exec.foreign = {tok: q for tok, (_, q) in stray.items()}
                log.warning("leaving existing positions alone (not counted in P&L, never traded or closed): %s",
                            ", ".join(f"{s} x{q}" for s, q in stray.values()))
        self.phase = "loading history + gate"
        self._write_status()
        self._history_and_gate()
        subs: dict[str, list[str]] = {self.cfg.exchange: [v["token"] for v in self.inst.values()] + [self.cfg.index_token, context.VIX_TOKEN]}
        for v in self.spot.values():
            if v["token"] not in subs.setdefault(v["exchange"], []):
                subs[v["exchange"]].append(v["token"])
        self.stream = SmartStream(self.client.jwt, self.client.api_key, self.client.client_code,
                                  self.client.feed_token, subs, self.ticks)
        self.stream.start()
        if not self.stream.connected.wait(15):
            raise SystemExit("market data feed did not connect")
        msg = (f"[{self.tag}] scalper started | capital ₹{self.cfg.capital:,.0f} | daily stop ₹{self.cfg.max_daily_loss:.0f} | "
               f"strategies: {', '.join(sorted(self.gate.enabled)) if self.gate.use else 'ALL (aggressive)'} | "
               f"stocks: {len(self.inst)} | indices: {', '.join(self.spot) or '-'}")
        log.info(msg)
        self.tg.send(msg)

    def _history_and_gate(self):
        end = now_ist()
        start = (end - timedelta(days=int(self.cfg.gate_lookback_days * 1.6) + 4)).replace(hour=9, minute=15)
        hist: dict[str, pd.DataFrame] = {}
        todo = list(self.inst)
        for attempt in range(3):                                   # second/third pass for rate-limited symbols
            failed = []
            for sym in todo:
                try:
                    hist[sym] = self.client.candles(self.inst[sym]["token"], start, end, self.cfg.exchange)
                except Exception as e:  # noqa: BLE001
                    failed.append(sym)
                    log.warning("history %s failed (pass %d): %s", sym, attempt + 1, e)
            todo = failed
            if not todo:
                break
            time.sleep(3)
        for und, v in self.spot.items():
            for attempt in range(3):
                try:
                    hist[und] = self.client.candles(v["token"], start, end, v["exchange"])
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("history %s failed (pass %d): %s", und, attempt + 1, e)
                    time.sleep(3)
            else:
                todo.append(und)
        if todo:
            log.warning("no history for %s -> not traded today (VWAP/opening range would be wrong)", ", ".join(todo))
        hist = {s: df for s, df in hist.items() if len(df) >= 60}
        idx = hist["NIFTY"] if "NIFTY" in hist else self.client.candles(self.cfg.index_token, start, end, self.cfg.exchange)
        cur_min = end.replace(second=0, microsecond=0)
        for sym, df in hist.items():
            self.bars[sym] = df[df.index < cur_min]                 # drop the still-forming candle
        self.index_bars = idx[idx.index < cur_min]

        # walk-forward gate on completed days only (today excluded -> no look-ahead)
        today0 = pd.Timestamp(self.today)
        past = {s: df[df.index < today0] for s, df in self.bars.items() if s in self.inst}   # gate = stocks
        days = sorted({ts.date() for df in past.values() for ts in df.index})[-self.cfg.gate_lookback_days:]
        if days:
            lo = pd.Timestamp(days[0]) - pd.Timedelta(days=3)       # small warm-up before the window
            past = {s: df[df.index >= lo] for s, df in past.items()}
            ticks = {s: self.inst[s]["tick"] for s in past}
            trades = backtest.run(past, self.index_bars[self.index_bars.index < today0], self.cfg, ticks, portfolio=False)
            trades = [t for t in trades if pd.Timestamp(t["entry_ts"]).date() >= days[0]]
            self.gate = Gate.from_trades(trades, self.cfg, asof=str(self.today))
        else:
            self.gate = Gate(use=self.cfg.use_gate)
        self.gate.save(self.cfg.state_dir / "gate.json")
        log.info("gate (%d days): %s", len(days), json.dumps(self.gate.strategies))
        if self.gate.use and not self.gate.enabled:
            log.warning("NO strategy showed a positive after-cost edge recently -> engine will stay flat today")
        for sym in self.bars:
            self.feats[sym] = add_features(self.bars[sym].tail(1500))
            self.fired[sym] = set()

    # ------------------------------------------------------------ main loop
    def _acquire_lock(self):
        """Paper and live share ONE Angel session: a second engine's logout would kill the first one's
        orders and feed. flock is released by the OS even if the process crashes, so no stale locks."""
        import fcntl
        path = self.cfg.state_dir / "engine.lock"
        self._lock_fh = path.open("a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_fh.seek(0)
            other = self._lock_fh.read().strip() or "another engine"
            self._lock_fh.close()
            raise SystemExit(f"{other} is already running. Stop it first (Ctrl+C in its terminal) - paper and live "
                             "share one Angel login, and stopping one would log the other out.")
        self._lock_fh.seek(0)
        self._lock_fh.truncate()
        self._lock_fh.write(f"{self.tag} engine (pid {os.getpid()}, started {now_ist():%H:%M:%S})")
        self._lock_fh.flush()

    def run(self):
        flat_t = _at(now_ist(), self.cfg.flatten_at)
        stop_t = _at(now_ist(), "15:20")
        self._acquire_lock()          # outside try: a refused start must not log the running engine out
        try:
            self.setup()
            while True:
                now = now_ist()
                if now >= stop_t:
                    break
                try:
                    t = self.ticks.get(timeout=0.2)
                    self._on_tick(t)
                    while not self.ticks.empty():             # drain burst, keep latency low
                        self._on_tick(self.ticks.get_nowait())
                except queue.Empty:
                    pass
                now = now_ist()
                if self.pos and self.live:
                    self._poll_broker_stop()
                if now >= flat_t:
                    if self.pos:
                        self._close("eod")
                    if not self.risk.killed:
                        log.info("flatten time reached - done for the day")
                    break
                if self.last_scan_minute is None or (now.replace(second=0, microsecond=0) > self.last_scan_minute and now.second >= 1):
                    self._guard(self._on_minute, now)
                if self.live and self.static_ip and time.monotonic() - self.last_ip_check > 60:
                    self.last_ip_check = time.monotonic()
                    threading.Thread(target=self._check_ip, daemon=True).start()
                if self.live and time.monotonic() - self.last_reconcile > self.cfg.reconcile_every_s:
                    self._reconcile()
                if time.monotonic() - self.last_status >= 1.0:
                    self.phase = "trading"
                    self._guard(self._write_status)
                if self.risk.killed:
                    break
        except KeyboardInterrupt:
            log.warning("interrupted by user")
        finally:
            self.shutdown()

    def _check_rejections(self):
        """3 rejected entries in a row = a broker/account problem (IP not whitelisted, no margin, segment
        not enabled...), not a market one: stop sending orders and say why instead of retrying every minute."""
        rej = getattr(self.exec, "rejections", [])
        if len(rej) >= 3 and not self.order_block:
            self.order_block = f"broker rejected {len(rej)} orders in a row - last: {rej[-1][:200]}"
            msg = f"[{self.tag}] new entries STOPPED: {self.order_block}. Fix the cause, then restart the engine."
            log.critical(msg)
            self.tg.send(msg)

    def _check_ip(self):
        """Angel rejects orders from an unregistered IP: if the public IP drifts mid-session,
        stop opening trades (the broker-side SL of an open position is already on the exchange)."""
        ip = None
        for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
            try:
                ip = requests.get(url, timeout=5).text.strip()
                break
            except requests.RequestException:
                continue
        if not ip:
            return
        if ip != self.static_ip and not self.ip_block:
            self.ip_block = f"public IP changed to {ip} (registered {self.static_ip})"
            msg = f"[{self.tag}] {self.ip_block} - new entries blocked; check open position in the Angel app"
            log.critical(msg)
            self.tg.send(msg)
        elif ip == self.static_ip and self.ip_block:
            log.warning("public IP back to %s - entries allowed again", ip)
            self.ip_block = None

    def _guard(self, fn, *a):
        """A bug in signal/report code must not take down position management.
        Too many errors in a short window -> stop (shutdown still flattens)."""
        try:
            fn(*a)
        except Exception:  # noqa: BLE001
            log.exception("error in %s (engine keeps running)", fn.__name__)
            now = time.monotonic()
            self._errors = [t for t in getattr(self, "_errors", []) if now - t < 120] + [now]
            if len(self._errors) >= 10:
                log.critical("10 errors in 2 minutes - stopping engine")
                raise

    def _on_tick(self, t: Tick):
        if t.token == context.VIX_TOKEN:
            self.ctx.vix = t.ltp
            return
        sym = self.tok2sym.get(t.token)
        key = sym or ("INDEX" if t.token == self.cfg.index_token else None)
        if key is None:
            return
        self.ltp[key] = t.ltp
        if t.token == self.cfg.index_token:
            self.ltp["INDEX"] = t.ltp
        # on_tick returns the just-completed bar when this symbol prints its first
        # tick in a new minute. Persist it here; the wall-clock flush below only
        # catches symbols that had no tick to roll their bar over.
        finished = self.builder.on_tick(t)
        if finished:
            self._append_bar(finished)
        if self.pos and sym == self.pos.symbol:
            self._manage_tick(t.ltp)
        elif self.pos and self.pos.under and sym == self.pos.under:
            p = self.pos                                   # option trade: the index levels decide too
            if p.under_side * (t.ltp - p.under_stop) <= 0:
                self._close("index_stop")
            elif p.under_side * (t.ltp - p.under_target) >= 0:
                self._close("index_target")

    def _manage_tick(self, px: float):
        p = self.pos
        if self.risk.check_kill(p.open_pnl(px)):
            self._close("kill_switch")
            return
        if p.target_hit(px, px):
            self._close("target")
        elif p.stop_hit(px, px):
            if not self.live:
                self._close("stop")
            elif self.stop_crossed_at is None:
                self.stop_crossed_at = time.monotonic()
            elif time.monotonic() - self.stop_crossed_at > 3.0:
                log.warning("price beyond stop for 3s and broker SL not filled -> forcing exit")
                self._close("stop_forced")
        else:
            self.stop_crossed_at = None

    def _poll_broker_stop(self):
        urgent = self.stop_crossed_at is not None
        if not urgent and time.monotonic() - self.last_sl_poll < 3.0:
            return
        self.last_sl_poll = time.monotonic()
        px = self.exec.broker_stop_filled(self.pos)
        if px is not None:
            self._book_exit(px, "stop")

    def _on_minute(self, now: datetime):
        minute = now.replace(second=0, microsecond=0)
        self.last_scan_minute = minute
        for b in self.builder.flush_older_than(now):
            self._append_bar(b)
        changed = [s for s in self.bars if len(self.bars[s]) and self.bars[s].index[-1] == minute - timedelta(minutes=1)]
        for sym in changed:
            self.feats[sym] = add_features(self.bars[sym].tail(1500))
            if self.cooldown.get(sym):
                self.cooldown[sym] -= 1
        # bar-close management of the open position
        if self.pos and self.pos.symbol in changed:
            r = self.feats[self.pos.symbol].iloc[-1]
            new_stop, why = self.pos.on_bar_close(r["close"], r["high"], r["low"], r["atr"], self.cfg)
            if why:
                self._close(why)
            elif new_stop is not None and self.exec.move_stop(self.pos, new_stop):
                log.info("stop -> %.2f (%.2fR best)", new_stop, self.pos.r_multiple(self.pos.best))
        elif self.pos and self.pos.under in changed:
            p = self.pos
            p.bars += 1
            if p.bars >= self.cfg.time_stop_bars and p.r_multiple(self.ltp.get(p.symbol, p.entry)) < self.cfg.time_stop_min_r:
                self._close("time_stop")
        self._scan(now, changed)

    def _append_bar(self, b: dict):
        row = pd.DataFrame([{k: b[k] for k in ("open", "high", "low", "close", "volume")}], index=[b["ts"]])
        if b["token"] == self.cfg.index_token:
            if self.index_bars.empty or b["ts"] > self.index_bars.index[-1]:
                self.index_bars = pd.concat([self.index_bars, row]).tail(3000)
        sym = self.tok2sym.get(b["token"])
        if sym is None or sym not in self.bars:      # no history today -> never traded
            return
        df = self.bars.get(sym)
        if df is None or df.empty or b["ts"] > df.index[-1]:
            self.bars[sym] = pd.concat([df, row]).tail(3000) if df is not None and not df.empty else row

    def _scan(self, now: datetime, changed: list[str]):
        if self.pos or not changed:
            return
        if not (_at(now, self.cfg.entry_start) <= now <= _at(now, self.cfg.entry_end)):
            return
        ok, why = self.risk.can_open(now)
        if not ok or self.ctx.no_trade_reason or self.ip_block or self.order_block:
            return
        stock_budget = self.risk.trade_budget() * self.ctx.budget_mult
        remaining = self.cfg.max_daily_loss + min(self.risk.realized, 0.0)
        opt_budget = min(self.cfg.option_max_loss or remaining, remaining) * (0.5 if (self.ctx.vix or 0) >= 20 else 1.0)
        bias = int(index_bias(self.index_bars.tail(400)).iloc[-1]) if len(self.index_bars) > 30 else 0
        self.bias = bias
        c = self.cfg
        cands = []
        for sym in changed:
            if self.cooldown.get(sym) or sym not in self.feats:
                continue
            is_idx = sym in self.spot
            if is_idx and not c.aggressive:
                continue
            params = ((c.index_min_atr_pct, c.index_max_atr_pct, c.index_min_stop_atr, c.index_min_stop_pct) if is_idx
                      else (c.min_atr_pct, c.max_atr_pct, c.min_stop_atr, c.min_stop_pct))
            f = self.feats[sym]
            self._scan_counts["bars"] += 1
            signals = scan(f, len(f) - 1, sym, bias, self.fired.setdefault(sym, set()), None, *params)
            self._scan_counts["signals"] += len(signals)
            for sig in signals:
                if is_idx:
                    self._scan_counts["option_signals"] += 1
                    cands.append((sig.reward / sig.risk, sig, None))      # option sizing happens on entry
                    continue
                if not self.gate.allows(sig.strategy, sym):
                    self._scan_counts["gate_rejects"] += 1
                    continue
                if stock_budget < c.min_trade_budget:
                    self._scan_counts["budget_rejects"] += 1
                    continue
                sz = size_signal(sig, stock_budget, c, self.inst[sym]["tick"],
                                 self.gate.edge_r(sig.strategy) if self.gate.use else None)
                if sz:
                    self._scan_counts["sized"] += 1
                    cands.append((sz.edge_r * min(sz.net_rr, 3.0) if self.gate.use else sz.net_rr, sig, sz))
                else:
                    self._scan_counts["size_rejects"] += 1
        for _, sig, sz in sorted(cands, key=lambda x: -x[0])[:3]:
            if self._enter(sig, sz, now, opt_budget):
                return
            if sig.symbol in self.spot:
                self._scan_counts["option_rejects"] += 1
        self._report_scan_counts()

    def _report_scan_counts(self):
        """Explain why an otherwise healthy session is staying flat, without logging every bar."""
        now = time.monotonic()
        if now - self._scan_report_at < 300:
            return
        c = self._scan_counts
        log.info(
            "entry scan (last 5m): symbols=%d setups=%d gate_rejects=%d "
            "budget_rejects=%d sizing_rejects=%d sized=%d option_setups=%d option_rejects=%d",
            c["bars"], c["signals"], c["gate_rejects"], c["budget_rejects"],
            c["size_rejects"], c["sized"], c["option_signals"], c["option_rejects"],
        )
        self._scan_counts = {k: 0 for k in c}
        self._scan_report_at = now

    def _enter(self, sig: Signal, sz, now: datetime, opt_budget: float) -> bool:
        ltp = self.ltp.get(sig.symbol, sig.entry)
        if abs(ltp - sig.entry) > 0.25 * sig.risk:          # price ran away since the bar closed
            log.info("skip %s %s: price moved %.2f -> %.2f", sig.symbol, sig.strategy, sig.entry, ltp)
            return False
        side_txt = "LONG" if sig.side > 0 else "SHORT"
        if sig.symbol in self.spot:
            if opt_budget < self.cfg.min_trade_budget:
                return False
            plan = plan_option(self.chain, self.client, sig.symbol, sig.side, ltp, abs(ltp - sig.stop), abs(sig.target - ltp),
                               opt_budget, self.cfg.capital, self.cfg.min_net_rr, self.cfg.slippage_ticks, now,
                               exclude=set(getattr(self.exec, "foreign", {})))
            if plan is None:
                log.info("skip %s %s: no option plan fits budget ₹%.0f, capital ₹%.0f and net R:R %.2f",
                         sig.symbol, sig.strategy, opt_budget, self.cfg.capital, self.cfg.min_net_rr)
                return False
            key = plan.tradingsymbol
            inst = {"token": plan.token, "tradingsymbol": key, "tick": plan.tick, "exchange": plan.exchange, "kind": plan.exchange}
            self.inst[key], self.tok2sym[plan.token] = inst, key
            self.stream.subscribe(plan.exchange, [plan.token])
            self.ltp[key] = plan.premium
            log.info("SIGNAL %s %s %s @%.2f (idx stop %.2f tgt %.2f) -> BUY %s x%d @%.2f SL %.2f TGT %.2f | delta %.2f IV %.0f%% | "
                     "max loss ₹%.0f, net win ₹%.0f | %s", sig.strategy, side_txt, sig.symbol, ltp, sig.stop, sig.target, key,
                     plan.qty, plan.premium, plan.prem_stop, plan.prem_target, plan.delta, plan.iv * 100,
                     plan.net_loss, plan.net_win, sig.reason)
            osig = Signal(key, sig.strategy, 1, plan.premium, plan.prem_stop, plan.prem_target, sig.atr * plan.delta, sig.reason, sig.ts)
            self.fired.setdefault(sig.symbol, set()).add(f"{sig.strategy}{sig.side:+d}")
            pos = self.exec.open(osig, plan.qty, inst, plan.premium, now)
            if pos is None:
                self._check_rejections()
                return False
            pos.under, pos.under_stop, pos.under_target, pos.under_side = sig.symbol, sig.stop, sig.target, sig.side
            pos.plan_loss = plan.net_loss
        else:
            log.info("SIGNAL %s %s %s qty=%d entry~%.2f stop=%.2f tgt=%.2f | max loss ₹%.0f, net win ₹%.0f, edge %.2fR | %s",
                     sig.strategy, side_txt, sig.symbol, sz.qty, ltp, sig.stop, sig.target, sz.net_loss, sz.net_win, sz.edge_r, sig.reason)
            self.fired.setdefault(sig.symbol, set()).add(f"{sig.strategy}{sig.side:+d}")
            pos = self.exec.open(sig, sz.qty, self.inst[sig.symbol], ltp, now)
            if pos is None:
                self._check_rejections()
                return False
            pos.plan_loss = sz.net_loss
        self.pos = pos
        self.stop_crossed_at = None
        msg = (f"[{self.tag}] ENTRY {pos.strategy} {'LONG' if pos.side > 0 else 'SHORT'} {pos.symbol} x{pos.qty} @ {pos.entry:.2f} "
               f"SL {pos.stop:.2f} TGT {pos.target:.2f} (max loss ₹{pos.plan_loss:.0f})"
               + (f" | {pos.under} {'↑' if pos.under_side > 0 else '↓'} stop {pos.under_stop:.2f}" if pos.under else ""))
        log.info(msg)
        self.tg.send(msg)
        return True

    # ------------------------------------------------------------ exits
    def _close(self, reason: str):
        if not self.pos:
            return
        px = self.exec.close(self.pos, reason, self.ltp.get(self.pos.symbol, self.pos.entry))
        self._book_exit(px, reason)

    def _book_exit(self, px: float, reason: str):
        p = self.pos
        self.pos = None
        self.stop_crossed_at = None
        gross = p.side * (px - p.entry) * p.qty
        costs = trade_costs(p.side, p.entry, px, p.qty, p.kind)
        net = gross - costs
        now = now_ist()
        self.risk.on_close(net, now)
        self.cooldown[p.symbol] = self.cfg.cooldown_bars_after_exit
        if p.under:
            self.cooldown[p.under] = self.cfg.cooldown_bars_after_exit
        self._save_day()
        self._journal({"date": str(self.today), "mode": self.tag, "symbol": p.symbol, "strategy": p.strategy,
                       "side": p.side, "qty": p.qty, "entry_ts": str(p.opened), "exit_ts": str(now), "entry": p.entry,
                       "exit": px, "reason": reason, "gross": round(gross, 2), "costs": round(costs, 2),
                       "net": round(net, 2), "day_pnl": round(self.risk.realized, 2)})
        msg = (f"[{self.tag}] EXIT {p.symbol} {reason} @ {px:.2f} | net ₹{net:+.2f} (charges ₹{costs:.2f}) | "
               f"day ₹{self.risk.realized:+.2f} | trades {self.risk.trades}")
        log.info(msg)
        self.tg.send(msg)
        if reason == "kill_switch" or self.risk.check_kill():
            self._kill()

    def _kill(self):
        if self._kill_done:
            return
        self._kill_done = True
        self.risk.killed = True
        kill_file(self.cfg, self.today, self.tag).write_text(f"{now_ist()} {self.risk.kill_reason}\n")
        if self.pos:
            self._close("kill_switch")
        self.exec.flatten_all(self.inst)
        msg = f"[{self.tag}] KILL SWITCH: {self.risk.kill_reason}. All positions closed. Engine stopped for today."
        log.critical(msg)
        self.tg.send(msg)

    def _reconcile(self):
        self.last_reconcile = time.monotonic()
        try:
            broker = self.exec.open_positions(self.inst)
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile failed: %s", e)
            return
        mine = {self.pos.symbol: self.pos.side * self.pos.qty} if self.pos else {}
        if broker == mine:
            return
        if self.pos and self.pos.symbol not in broker:
            px = self.exec.broker_stop_filled(self.pos)
            log.warning("broker shows %s flat - booking exit", self.pos.symbol)
            self._book_exit(px if px is not None else self.ltp.get(self.pos.symbol, self.pos.stop), "broker_flat")
            return
        log.critical("POSITION MISMATCH broker=%s engine=%s -> flattening everything", broker, mine)
        self.tg.send(f"[{self.tag}] position mismatch broker={broker} engine={mine}; flattening")
        self.pos = None
        self.exec.flatten_all(self.inst)

    # ------------------------------------------------------------ dashboard
    def _write_status(self, final: bool = False):
        """Atomic JSON snapshot read by `python -m scalper dashboard`."""
        self.last_status = time.monotonic()
        now = now_ist()
        p = self.pos
        ltp = self.ltp.get(p.symbol, p.entry) if p else None
        open_pnl = p.open_pnl(ltp) if p else 0.0
        day = self.risk.realized + open_pnl
        stamp = now.strftime("%H:%M")
        if not self.curve or self.curve[-1][0] != stamp:
            self.curve.append((stamp, round(day, 2)))
        else:
            self.curve[-1] = (stamp, round(day, 2))
        universe = []
        for sym, f in self.feats.items():
            if f.empty:
                continue
            r = f.iloc[-1]
            px = self.ltp.get(sym, r["close"])
            num = lambda v, k=2: None if v != v else round(float(v), k)  # noqa: E731
            universe.append({"symbol": sym, "ltp": round(px, 2), "regime": regime(r),
                             "vwap_dist": num((px - r["vwap"]) / r["vwap"] * 100, 3), "z": num(r["z"]),
                             "relvol": num(r["relvol"]), "atr_pct": num(r["atr"] / r["close"] * 100, 3),
                             "cooldown": int(self.cooldown.get(sym, 0))})
        snap = {
            "mode": self.tag, "aggressive": self.cfg.aggressive, "phase": "stopped" if final else self.phase, "updated": now.isoformat(timespec="seconds"),
            "started": self.started.isoformat(timespec="seconds"),
            "capital": self.cfg.capital, "max_daily_loss": self.cfg.max_daily_loss,
            "realized": round(self.risk.realized, 2), "open_pnl": round(open_pnl, 2), "day_pnl": round(day, 2),
            "loss_room": round(self.cfg.max_daily_loss + min(day, 0.0), 2), "trade_budget": round(self.risk.trade_budget(), 2),
            "trades": self.risk.trades, "max_trades": self.cfg.max_trades_per_day,
            "killed": self.risk.killed, "kill_reason": self.risk.kill_reason,
            "paused_until": self.risk.paused_until.strftime("%H:%M") if self.risk.paused_until and now < self.risk.paused_until else None,
            "nifty": self.ltp.get("INDEX"), "bias": self.bias,
            "context": None if not hasattr(self, "ctx") else {
                "tags": self.ctx.tags(), "vix": self.ctx.vix, "vix_prev": self.ctx.vix_prev_close,
                "budget_mult": self.ctx.budget_mult, "block": self.order_block or self.ip_block or self.ctx.no_trade_reason},
            "position": None if not p else {
                "symbol": p.symbol, "strategy": p.strategy, "side": "LONG" if p.side > 0 else "SHORT", "qty": p.qty,
                "entry": p.entry, "stop": p.stop, "target": p.target, "ltp": ltp, "pnl": round(open_pnl, 2),
                "r": round(p.r_multiple(ltp), 2), "max_loss": round(p.plan_loss, 2), "bars": p.bars,
                "opened": str(p.opened)[11:19]},
            "gate": {"enabled": sorted(self.gate.enabled) if hasattr(self, "gate") else [], "use": getattr(self, "gate", Gate()).use,
                     "stats": getattr(self, "gate", Gate()).strategies},
            "universe": sorted(universe, key=lambda u: u["symbol"]),
            "curve": self.curve[-400:],
            "events": list(self.tap.events)[::-1],
        }
        tmp = self.status_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(snap, default=str))
            tmp.replace(self.status_path)
        except OSError as e:
            log.debug("status write failed: %s", e)

    def shutdown(self):
        try:
            if self.pos:
                self._close("shutdown")
            if self.live and getattr(self, "inst", None):
                self.exec.flatten_all(self.inst)
        finally:
            if getattr(self, "stream", None):
                self.stream.stop()
            self.client.logout()
            msg = f"[{self.tag}] session end | day P&L ₹{self.risk.realized:+.2f} over {self.risk.trades} trades"
            log.info(msg)
            self._write_status(final=True)
            logging.getLogger("scalper").removeHandler(self.tap)
            self.tg.send(msg)
            time.sleep(1)
            fh = getattr(self, "_lock_fh", None)
            if fh and not fh.closed:
                fh.seek(0)
                fh.truncate()
                fh.close()                # releases the flock
