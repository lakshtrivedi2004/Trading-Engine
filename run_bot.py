#!/usr/bin/env python3
"""NIFTY options arbitrage bot - live runner.

    python run_bot.py                      # paper mode against the live feed
    python run_bot.py --mode live --yes    # send real orders
    python run_bot.py --status             # one-shot connectivity + universe check

Paper mode is the default and is the only mode that runs without an explicit
--yes. Nothing about the detection path differs between the two: live mode swaps
the broker and nothing else, so what paper trades is exactly what live trades.
"""
import argparse
import signal
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from arb.capital_book import CapitalBook
from arb.config import (Credentials, CostModel, EngineConfig, IntradayLimits,
                        RiskLimits)
from arb.candles import CandleStore
from arb.dashboard import Dashboard, DashboardState
from arb.engine import ArbEngine, ChainBinding
from arb.execution import ExecutionEngine, LiveBroker, PaperBroker
from arb.feed import NSE_CM, NSE_FO, AngelSession, MarketFeed
from arb.instruments import (build_universe, live_futures, load_scrip_master, market_phase,
                             now_ist, years_to_expiry)
from arb.intraday_engine import IntradayEngine
from arb.journal import Journal
from arb.recorder import TickRecorder
from arb.margin import MarginService
from arb.risk import RiskManager
from arb.settlement import LegMarker, SettlementBook
from arb.telegram import TelegramNotifier, _inr


def log(msg):
    print(f"[{now_ist().strftime('%H:%M:%S')}] {msg}", flush=True)


def cached_spot(name, max_age_days=5.0):
    """Last close from the persisted candle cache, or 0.0.

    The fallback for when Angel refuses the REST spot lookup (AG8004) while the
    websocket still works. Without it the strike ladder is centred on nothing,
    and on 24 Sep 2026 that meant NIFTY 23170 being traded off a 23800-24800
    chain: every contract the selector could see was far out of the money.
    """
    import json
    import os
    from datetime import datetime
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".cache", "candles", f"{name}_1m.json")
    try:
        with open(path) as fh:
            rows = json.load(fh)
        last = rows[-1]
        age = (datetime.now().astimezone() - datetime.fromisoformat(last[0])).total_seconds()
        if age <= max_age_days * 86400 and float(last[4]) > 0:
            return float(last[4])
    except Exception:
        pass
    return 0.0


def live_funds(api):
    """(available rupees, where the figure came from) from Angel's RMS limits."""
    try:
        r = api.rmsLimit() or {}
        d = r.get("data") or {}
    except Exception as e:
        return 0.0, f"rmsLimit failed: {e}"
    for key in ("availablecash", "net"):
        try:
            v = float(d.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v, key
    return 0.0, f"rmsLimit returned {str(r)[:120]}"


def open_broker_positions(api):
    """Open positions in the account right now, as readable lines. [] if none.
    A failed lookup is itself a reason not to start live, so it is reported."""
    try:
        r = api.position() or {}
    except Exception as e:
        return [f"could not read positions: {e}"]
    if isinstance(r, dict) and r.get("status") is False:
        return [f"could not read positions: {r.get('message')}"]
    out = []
    for p in (r.get("data") or []) if isinstance(r, dict) else []:
        try:
            q = int(float(p.get("netqty") or 0))
        except (TypeError, ValueError):
            q = 0
        if q:
            out.append(f"{p.get('tradingsymbol')} qty {q} ({p.get('producttype')})")
    return out


def stock_candidates(sm, L):
    """F&O stocks worth subscribing: those whose cached NSE daily history says
    they are liquid enough to ever pass the scan. Without a cache (first run)
    every F&O stock is subscribed and the scan filters later."""
    import json
    import os
    from arb.scanner import daily_stat, fo_universe
    uni = fo_universe(sm)
    hist = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "history", "stocks")
    keep = {}
    for sym, info in uni.items():
        p = os.path.join(hist, f"{sym}_1d.json")
        if os.path.exists(p):
            try:
                st = daily_stat(sym, json.load(open(p)))
            except (OSError, ValueError):
                st = None
            # Half the live floors: the subscription is a superset, the scan decides.
            if st is not None and (st.turnover_cr < 0.5 * L.stocks_min_turnover_cr
                                   or st.prev_close < 0.5 * L.stocks_min_price):
                continue
        keep[sym] = info
    return keep


def build(cfg, creds, args):
    """Log in, pick the universe across every enabled index, wire the feed."""
    log(f"Angel One login as {creds.client_id}")
    # Wait out a network outage at start (DNS down, no route) for up to ten
    # minutes rather than dying: on 15 Sep 2026 the machine's DNS was out at
    # 13:55 and the bot stayed dead until it was restarted by hand at 14:48.
    session = AngelSession(creds).login(retry_for_sec=600.0, log=log)
    log("login ok")

    sm, src = load_scrip_master()
    log(f"scrip master: {len(sm):,} rows from {src}")

    underlyings = cfg.active_underlyings()
    specs, spots = [], {}
    for u in underlyings:
        spot = 0.0
        try:
            spot = session.spot_ltp(u.spot_token, u.name, u.spot_exchange)
        except Exception as e:
            log(f"  {u.name}: spot fetch failed ({e})")
        if spot <= 0:
            spot = cached_spot(u.name)
            if spot > 0:
                log(f"  {u.name}: Angel refused the spot lookup; centring strikes "
                    f"on the last saved candle close {spot:,.2f}")
            else:
                log(f"  {u.name}: NO SPOT PRICE - Angel refused the lookup and no "
                    f"saved candles exist. The strike ladder will be centred on "
                    f"the chain midpoint and may be far from the market. Fix the "
                    f"API key (see [candles] message) or restart after ~5 min of "
                    f"live data so the cache can centre it.")
        spots[u.name] = spot
        expiries, chains, futures = build_universe(
            sm, u.name, spot or None, u.num_expiries, u.strikes_each_side,
            u.segment, cfg.min_hours_to_expiry)
        if not expiries:
            log(f"  {u.name}: no live expiries, skipped")
            continue
        specs.append((u, expiries, chains, futures))

    if not specs:
        raise SystemExit("no tradable index found in the scrip master")

    # Stocks in play (arb/stockdesk.py): the cash tokens of every liquid F&O
    # stock, subscribed now because the socket only subscribes on connect.
    stock_universe = {}
    if getattr(args, "strategy", "intraday") == "intraday" and IntradayLimits.load().stocks_enabled:
        stock_universe = stock_candidates(sm, IntradayLimits.load())
        log(f"stocks             {len(stock_universe)} liquid F&O cash stocks subscribed")
    n_tokens = sum(len(c) * 2 for _, _, ch, _ in specs for c in ch.values()) \
        + sum(len(f) for _, _, _, f in specs) + len(specs) + len(stock_universe)
    feed = MarketFeed(
        session, capacity=n_tokens + 256, log=log,
        market_open=lambda: market_phase(start=cfg.session_start,
                                         end=cfg.session_end) == "open")

    bindings = []
    spot_slots = {}
    future_slots = {}
    for u, expiries, chains, futures in specs:
        spot_slots[u.name] = feed.register(u.spot_token, u.spot_exchange_type)
        log(f"{u.name}  spot {spots[u.name]:,.2f}  ({u.segment})")
        # The futures term structure, for calendar-carry analytics on the
        # dashboard. Futures already tied to a chain share the same slot.
        future_slots[u.name] = []
        for f in live_futures(sm, u.name, u.segment, cfg.min_hours_to_expiry):
            known = feed.store.slot_of_token.get(str(f["token"]))
            slot = known if known is not None else \
                feed.register(f["token"], u.option_exchange_type)
            future_slots[u.name].append(dict(f, slot=slot))
        for exp in expiries:
            chain = chains.get(exp) or {}
            if len(chain) < 2:
                log(f"    {exp}: too few paired strikes, skipped")
                continue
            strikes = sorted(chain)
            gi = len(bindings)
            ce_slots, pe_slots = [], []
            ce_sym, pe_sym, ce_tok, pe_tok = [], [], [], []
            for k in strikes:
                ce, pe = chain[k]["CE"], chain[k]["PE"]
                ce_slots.append(feed.register(ce["token"], u.option_exchange_type, gi))
                pe_slots.append(feed.register(pe["token"], u.option_exchange_type, gi))
                ce_sym.append(ce["symbol"]); pe_sym.append(pe["symbol"])
                ce_tok.append(ce["token"]); pe_tok.append(pe["token"])
            fut = futures.get(exp)
            fut_slot = feed.register(fut["token"], u.option_exchange_type, gi) \
                if fut else None
            bindings.append(ChainBinding(
                exp, strikes, ce_slots, pe_slots, ce_sym, pe_sym, ce_tok, pe_tok,
                lot_size=chain[strikes[0]]["CE"]["lotsize"],
                fut_slot=fut_slot,
                fut_sym=fut["symbol"] if fut else "",
                fut_tok=fut["token"] if fut else "",
                underlying=u.name, segment=u.segment))
            step = (strikes[1] - strikes[0]) if len(strikes) > 1 else 0
            log(f"    {exp}: {len(strikes)} strikes {strikes[0]:.0f}-{strikes[-1]:.0f} "
                f"(step {step:.0f}), lot {bindings[-1].lot_size}, "
                f"T={years_to_expiry(exp) * 365:.2f}d, "
                f"future {fut['symbol'] if fut else 'NONE (parity off)'}")
    if not bindings:
        raise SystemExit("no tradable chains")
    log(f"{len(bindings)} chains across {len(specs)} indices; "
        f"subscribing to {feed.store._next} tokens in SNAP_QUOTE mode")
    feed.spot_slots = spot_slots
    feed.future_slots = future_slots
    feed.stock_universe = stock_universe
    feed.stock_slots = {sym: feed.register(info["eq_token"], NSE_CM)
                        for sym, info in stock_universe.items()}
    return session, feed, bindings, spots


def _wait_until_session(cfg, stopping, log):
    """Block until the market is open, making no broker/NSE calls while
    waiting. Returns False if ``stopping`` fired before that happened."""
    phase = market_phase(start=cfg.session_start, end=cfg.session_end)
    if phase == "open":
        return not stopping.is_set()
    log(f"market {phase}; idle until {cfg.session_start} IST "
        f"(no API calls while waiting)")
    while not stopping.is_set():
        if market_phase(start=cfg.session_start, end=cfg.session_end) == "open":
            log("market open; resuming")
            return True
        stopping.wait(30)
    return False


def run_intraday_forever(cfg, cm, creds, args, mode):
    """Run the intraday strategy day after day in one long-lived process.

    Each trading day is a fresh Angel One login and a fresh universe
    (build()), run until close; then the process goes idle - no broker or
    NSE calls at all - until the next session_start, and builds again. The
    dashboard HTTP server is created once and stays up the whole time,
    rendering whichever day's state is current - including the frozen final
    snapshot of the last completed day while idle.

    ``--duration`` (a bounded test run) opts out of the loop entirely and
    keeps the old one-day-then-exit behaviour, since that flag exists for
    manual smoke tests, not production.
    """
    stopping = threading.Event()
    dash_holder = {"dash": None, "dstate": None}
    current = {"engine": None}

    notifier = None
    if not args.no_telegram:
        notifier = TelegramNotifier(creds.telegram_bot_token,
                                    creds.telegram_chat_id).start()

    # Built once, before the first session even logs in, so the dashboard is
    # reachable the instant the process starts - including a restart that
    # lands outside market hours, when it would otherwise sit idle with
    # nothing listening on the port at all.
    if not args.no_dashboard:
        dstate = DashboardState(None, None, None, None, cfg, mode, None)
        dstate.intraday_mode = True
        # Last known values, read from disk only (no broker/NSE calls): the
        # page should still show yesterday's spots and capital while idle,
        # not a blank screen, even before this process has run a single day.
        dstate.spots = {u.name: cached_spot(u.name) for u in cfg.active_underlyings()}
        cap_path = "journal/live/capital_live.json" if mode == "live" else "journal/capital.json"
        try:
            dstate.boot_capital = CapitalBook(0.0, path=cap_path).capital
        except Exception:
            dstate.boot_capital = 0.0
        dash = Dashboard(dstate, port=args.port).start()
        dash_holder["dash"], dash_holder["dstate"] = dash, dstate
        log(f"dashboard   {dash.url}")

    def on_signal(*_):
        stopping.set()
        e = current["engine"]
        if e is not None:
            e.stop()

    def on_restart(*_):
        # ./run.sh restart / systemd KillSignal=SIGUSR1: open positions go to
        # the next process (open_positions.json) instead of being squared off.
        e = current["engine"]
        if e is not None:
            e.handover = True
        on_signal()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, on_restart)

    try:
        if args.duration:
            _run_intraday_day(cfg, cm, creds, args, mode, notifier,
                              dash_holder, current, stopping)
            return
        while not stopping.is_set():
            if not _wait_until_session(cfg, stopping, log):
                break
            _run_intraday_day(cfg, cm, creds, args, mode, notifier,
                              dash_holder, current, stopping)
    finally:
        if dash_holder["dash"]:
            dash_holder["dash"].stop()
        if notifier:
            notifier.stop()


def _run_intraday_day(cfg, cm, creds, args, mode, notifier, dash_holder,
                       current, stopping):
    """Directional intraday option buying, managed with market structure, for
    one trading day: login, build the universe, run until close, clean up.

    Not arbitrage. Every trade here is a bet on where the index goes next, held
    for minutes, protected by a stop and closed before the bell. The arbitrage
    engine's return band does not apply and is not consulted.
    """
    session, feed, bindings, spots = build(cfg, creds, args)
    phase = market_phase(start=cfg.session_start, end=cfg.session_end)
    L = IntradayLimits.load()
    if mode == "live":
        # Real money: its own ledger and journal, sized from the funds the
        # broker says the account holds - never from the paper ledger's P&L.
        funds, detail = live_funds(session.api)
        open_pos = open_broker_positions(session.api)
        if open_pos:
            raise SystemExit(
                "LIVE START REFUSED: the account already has open positions the engine "
                "did not open and cannot manage:\n  " + "\n  ".join(open_pos) +
                "\nClose them in the Angel app (or wait for them to square off), then start again.")
        if funds <= 0:
            raise SystemExit(f"LIVE START REFUSED: could not read available funds from Angel ({detail})")
        if args.capital:
            funds = min(funds, float(args.capital))
        capital = CapitalBook(funds, path="journal/live/capital_live.json", log=log)
        capital.open_day()
        cap_now = capital.sync_to_funds(funds)
        log(f"LIVE funds          Rs {funds:,.2f} available ({detail}); trading capital Rs {cap_now:,.2f}")
    else:
        capital = CapitalBook(args.capital or L.starting_capital, log=log)
    interval = args.candle_interval or L.candle_interval

    journal = Journal(directory="journal/live") if mode == "live" else Journal()
    broker = (LiveBroker(session.api, cm, product="INTRADAY", confirm_fills=True, log=log)
              if mode == "live" else PaperBroker(cm))

    # Structure is read off index candles, so this is on the critical path now -
    # not a dashboard ornament. It starts whether or not the dashboard runs.
    candles = CandleStore(session.api, cfg.active_underlyings(),
                          interval=interval, log=log)
    candles.attach_feed(feed.store, getattr(feed, "spot_slots", {}),
                        getattr(feed, "future_slots", {}),
                        extra_slots=getattr(feed, "stock_slots", {})).start()

    engine = IntradayEngine(feed, bindings, cfg, cm, L, broker, journal,
                            capital, candles, notifier=notifier, log=log)
    current["engine"] = engine
    if L.stocks_enabled and getattr(feed, "stock_slots", None):
        from arb.nse import NseClient
        from arb.stockdesk import StockDesk
        engine.stocks = StockDesk(feed.stock_universe, feed.stock_slots, candles, L,
                                  api=session.api, nse=NseClient(log=log), log=log).start()
    # One book snapshot a minute (~4 MB a day): Angel serves no history for
    # expired options, so this is the only premium data any rule can be tested on.
    recorder = TickRecorder(feed.store, bindings, hz=1 / 60.0).start() if cfg.record_ticks else None

    dash = dash_holder["dash"]
    if dash is not None:
        # Rebind the running dashboard to today's fresh objects in place: the
        # HTTP handler closed over this DashboardState instance when the
        # server started, so reassigning dash.state would not be seen - only
        # mutating the same object's attributes reaches the live page. This
        # is also how the very first day fills in the placeholder dashboard
        # that run_intraday_forever created before ever logging in.
        dstate = dash_holder["dstate"]
        dstate.engine = engine
        dstate.intraday = engine
        dstate.feed = feed
        dstate.journal = journal
        dstate.candles = candles
        dstate.mode = mode
        dstate.spots = dict(spots)
        dstate.started = time.time()
        dstate.marker = LegMarker(feed.store, dstate.settlements, candles)
        log(f"dashboard   {dash.url} (bound to today's session)")

    feed.start()
    log("websocket connecting...")
    if not feed.connected.wait(timeout=30):
        log("websocket did not connect in 30s; continuing to wait in background")
    slots = list(range(feed.store._next))
    ready = feed.wait_for_quotes(slots, timeout=25, need=max(4, len(slots) // 4))
    log(f"{ready}/{len(slots)} instruments quoting")

    tgt = capital.daily_target(L.daily_target_pct)
    log(f"capital            {_inr(capital.capital)}")
    log(f"daily target       {_inr(tgt)} ({L.daily_target_pct:g}% of capital, a goal not a cap)")
    log(f"strikes            ATM +/- {L.max_strike_offset}, one lot, "
        f"buying only (selling {'ON' if L.allow_selling else 'OFF'})")
    log(f"risk               stop at structure, 1:{L.reward_risk:g} target, "
        f"breakeven at +{L.trail_trigger_r:g}R")
    log(f"structure          {interval} candles, swing strength {L.swing_strength}")

    if notifier:
        notifier.send(
            f"🟢 <b>INTRADAY ENGINE UP</b> · <code>{mode.upper()}</code>\n\n"
            f"Account <code>{creds.client_id}</code>\n"
            f"Market <code>{phase}</code> · {now_ist().strftime('%d %b %H:%M IST')}\n"
            f"Capital <code>{_inr(capital.capital)}</code>\n"
            f"Target <code>{_inr(tgt)}</code> "
            f"(<code>{L.daily_target_pct:g}%</code>, a goal — more is better)\n"
            f"Buying <code>1 lot, ATM ±{L.max_strike_offset}</code> · "
            f"selling <code>{'on' if L.allow_selling else 'off'}</code>\n"
            f"Stop <code>at structure</code> · target "
            f"<code>1:{L.reward_risk:g}</code> · breakeven at "
            f"<code>+{L.trail_trigger_r:g}R</code>\n"
            f"Square-off <code>{L.squareoff_minutes:g} min before "
            f"{cfg.session_end}</code>\n\n"
            f"<i>This is directional trading, not arbitrage. Every trade can lose.</i>")

    stop_at = time.time() + args.duration if args.duration else None

    def should_stop():
        if stop_at and time.time() > stop_at:
            return True
        return market_phase(start=cfg.session_start,
                            end=cfg.session_end) == "closed" and phase == "open"

    engine.start(stop_predicate=should_stop)
    log(f"scanning ({mode}); Ctrl+C to stop")

    last = 0.0
    try:
        while not stopping.is_set() and engine._running.is_set():
            time.sleep(1.0)
            if time.time() - last >= 60:
                last = time.time()
                s = engine.summary()
                # The pace gate refuses most of the day by design; printing the
                # reading against its bar says so, instead of looking like a fault.
                pace = " ".join(f"{k} {v['atr_ratio']:.2f}x"
                                for k, v in sorted(s["structures"].items()))
                log(f"ticks={feed.ticks:,} passes={engine.passes:,} "
                    f"trades={s['trades']} (W{s['wins']}/L{s['losses']}) "
                    f"open={s['open']} "
                    f"pnl={_inr(s['day_pnl'])} ({s['day_pct']:+.2f}%) "
                    f"target={_inr(s['target_inr'])} "
                    + (f"| pace {pace} (need {engine.L.min_atr_ratio:g}x) " if pace else "")
                    + f"| {s['rejects'] or 'scanning'}")
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        try:
            candles.stop()
            if recorder:
                recorder.stop()
        except Exception:
            pass
        # The day's realised profit becomes tomorrow's capital. Only closed
        # trades count: an open mark is not money.
        if engine.trades_today:
            # Every trade is already in the day ledger (arb/capital_book.py).
            # After the close it is folded into capital now; stopped earlier,
            # it stays in the ledger and is folded when the next day opens, so
            # a mid-day restart resumes the day instead of splitting it.
            before = capital.capital
            after = before + capital.day_pnl
            folded_now = market_phase() in ("closed", "weekend")
            if folded_now:
                before, after = capital.close_day(note=mode)
            s = engine.summary()
            log("")
            log(f"trades             {s['trades']}  "
                f"(won {s['wins']}, lost {s['losses']})")
            log(f"realised           {_inr(engine.realised_pnl)} "
                f"({100.0 * engine.realised_pnl / before:+.2f}% of the "
                f"{_inr(before)} we started with)")
            log(f"target             {_inr(capital.daily_target(L.daily_target_pct))} "
                f"- {'MET' if engine.realised_pnl >= tgt else 'not met'}")
            log(f"capital            {_inr(before)} -> {_inr(after)}"
                + ("" if folded_now else "  (added at the next day's open)"))
            if notifier:
                hit = engine.realised_pnl >= tgt
                notifier.send(
                    f"{'🏆' if hit else '📕'} <b>DAY CLOSED</b>\n\n"
                    f"Trades <code>{s['trades']}</code> "
                    f"(W<code>{s['wins']}</code> / L<code>{s['losses']}</code>)\n"
                    f"Realised <code>{_inr(engine.realised_pnl)}</code> "
                    f"(<code>{100.0 * engine.realised_pnl / before:+.2f}%</code>)\n"
                    f"Target <code>{_inr(tgt)}</code> — "
                    f"<b>{'beaten' if engine.realised_pnl > tgt else ('met' if hit else 'missed')}</b>\n"
                    f"Capital <code>{_inr(before)}</code> → "
                    f"<code>{_inr(after)}</code>")
        else:
            log("no trades today; capital unchanged")
        feed.stop()
        current["engine"] = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("paper", "live"), default=None)
    ap.add_argument("--yes", action="store_true", help="required to arm live orders")
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--min-edge", type=float, default=0.05,
                    help="minimum net edge in points per unit to consider")
    ap.add_argument("--status", action="store_true", help="check and exit")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds; 0 = until close")
    ap.add_argument("--port", type=int, default=8089, help="dashboard port")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--candle-interval", default=None,
                    choices=("1m", "5m", "15m", "1h", "1d"),
                    help="structure timeframe; default 1m intraday, 5m for arb")
    ap.add_argument("--strategy", choices=("intraday", "arb"), default="intraday",
                    help="intraday: buy one option and scalp it with market "
                         "structure (default). arb: the hold-to-expiry "
                         "risk-free structure scanner.")
    ap.add_argument("--capital", type=float, default=None,
                    help="override the purse for this session only")
    args = ap.parse_args()

    cfg = EngineConfig.load()
    cm = CostModel.load()
    limits = RiskLimits()
    creds = Credentials.load()
    if creds.missing():
        raise SystemExit(f"missing credentials in .angel.env: {creds.missing()}")

    mode = args.mode or cfg.trading_mode
    if mode == "live" and not args.yes:
        raise SystemExit(
            "live mode places real orders with real money.\n"
            "Re-run with --mode live --yes once you have watched paper mode "
            "for a full session and agree with what it did.")

    phase = market_phase(start=cfg.session_start, end=cfg.session_end)
    log(f"market phase: {phase} ({now_ist().strftime('%a %d %b %H:%M IST')})")

    if args.strategy == "intraday" and not args.status:
        return run_intraday_forever(cfg, cm, creds, args, mode)

    session, feed, bindings, spots = build(cfg, creds, args)
    if args.status:
        log("status check complete")
        return

    notifier = None
    if not args.no_telegram:
        notifier = TelegramNotifier(creds.telegram_bot_token,
                                    creds.telegram_chat_id).start()

    journal = Journal()
    # "Invested" is what Angel One blocks, from its own margin calculator, not
    # the structure's theoretical settlement obligation (Rs 83,656 versus
    # Rs 13,000 for a one-lot NIFTY box). Calibrated per chain in the background
    # so the scan loop never waits on HTTP; exact figure fetched after each fill.
    margin = MarginService(session.api, bindings, limits, log=log, spots=spots).start()
    risk = RiskManager(limits, cm, margin=margin)
    # Pick the day back up where it left off. Counters, limits and the trade-id
    # sequence all come from the journal, so a restart is invisible to the
    # risk gate and to the dashboard.
    last_id = risk.restore_today()
    if risk.state.trades_today:
        log(f"restored today's state from journal: {risk.state.trades_today} trade(s), "
            f"P&L {_inr(risk.state.session_pnl)}, "
            f"deployed {_inr(risk.state.capital_deployed)}"
            + (f" — HALTED: {risk.state.halt_reason}" if risk.state.halted else ""))
    broker = (LiveBroker(session.api, cm) if mode == "live" else PaperBroker(cm))
    lot = bindings[0].lot_size
    execution = ExecutionEngine(broker, cm, risk, journal, notifier, lot,
                                total_capital=limits.total_capital, margin=margin)
    execution.trade_id = last_id          # continue numbering, never reuse an id
    engine = ArbEngine(feed, bindings, cfg, cm, limits, risk, execution,
                       journal, notifier, log=log)

    # Final settlement levels, pinned from the live spot at the close of an
    # expiry day so every leg's exit price stays reviewable after the contract
    # is gone.
    settlements = SettlementBook()

    def expiring_pairs(bindings):
        """(underlying, expiry) pairs that settle today: today's chains, plus
        the expiries of every open paper position in the journal - an expiring
        chain leaves the universe an hour before it settles, but the positions
        in it still need their settlement level."""
        from arb import history as _hist
        from arb.instruments import parse_expiry
        today = now_ist().date()
        pairs = {(b.underlying, b.expiry) for b in bindings}
        try:
            for t in _hist.load_trades(days=30):
                o = t.get("opportunity") or {}
                if o.get("expiry") and o.get("underlying"):
                    pairs.add((o["underlying"], o["expiry"]))
        except Exception:
            pass
        out = set()
        for u, e in pairs:
            try:
                if parse_expiry(e).date() == today:
                    out.add((u, e))
            except Exception:
                continue
        return out

    def spot_of(name):
        slot = (getattr(feed, "spot_slots", {}) or {}).get(name)
        if slot is not None and float(feed.store.ltp[slot]) > 0:
            return float(feed.store.ltp[slot])
        return float(spots.get(name, 0.0))

    dash = None
    if not args.no_dashboard:
        candles = CandleStore(session.api, cfg.active_underlyings(),
                              interval=args.candle_interval, log=log)
        candles.attach_feed(feed.store, getattr(feed, "spot_slots", {}),
                        getattr(feed, "future_slots", {})).start()
        dstate = DashboardState(engine, feed, risk, execution, cfg, mode, journal,
                                candles=candles, margin=margin, settlements=settlements)
        dash = Dashboard(dstate, port=args.port).start()
        # Mirror every finished trade into the dashboard: the execution engine
        # publishes each record once the broker's margin figure is on it.
        execution.subscribers.append(dstate.record_trade)
        log(f"dashboard   {dash.url}")

    # Open positions from earlier sessions whose contracts fell outside today's
    # strike window (the universe re-centres on spot each start) still need a
    # live mark for their exit price. Subscribe to those legs too; they carry
    # no chain group, so the scanner never sees them.
    extra = 0
    try:
        from arb import history as _hist
        from arb.feed import BSE_FO, NSE_FO
        from arb.instruments import parse_expiry
        for t in _hist.load_trades(days=30):
            exp = (t.get("opportunity") or {}).get("expiry", "")
            try:
                if not exp or now_ist() >= parse_expiry(exp):
                    continue
            except Exception:
                continue
            for l in t.get("legs", []):
                tok = str(l.get("token", ""))
                if tok and tok not in feed.store.slot_of_token:
                    feed.register(tok, BSE_FO if l.get("exchange") == "BFO" else NSE_FO)
                    extra += 1
    except Exception as e:
        log(f"open-position subscription skipped: {e}")
    if extra:
        log(f"subscribed {extra} extra contract(s) held in open paper positions, for marks")

    feed.start()
    log("websocket connecting...")
    if not feed.connected.wait(timeout=30):
        log("websocket did not connect in 30s; continuing to wait in background")
    if dash:
        dash.state.spots = dict(spots)
    slots = list(range(feed.store._next))
    ready = feed.wait_for_quotes(slots, timeout=25, need=max(4, len(slots) // 4))
    log(f"{ready}/{len(slots)} instruments quoting")

    if notifier:
        notifier.send(
            f"🟢 <b>ARBITRAGE ENGINE UP</b> · <code>{mode.upper()}</code>\n\n"
            f"Account <code>{creds.client_id}</code>\n"
            f"Market <code>{phase}</code> · {now_ist().strftime('%d %b %H:%M IST')}\n"
            f"Indices <code>"
            f"{', '.join(sorted({b.underlying for b in bindings}))}</code>\n"
            f"Chains <code>{len(bindings)}</code>\n"
            f"Instruments <code>{ready}/{len(slots)} quoting</code>\n"
            f"Strategies <code>{', '.join(cfg.strategies)}</code>\n"
            f"Size <code>{args.lots} lot (per-index lot size)</code>\n"
            f"Capital <code>{_inr(limits.total_capital)}</code> · "
            f"stake <code>{_inr(limits.stake_per_trade)}</code> per trade, fixed\n"
            f"Take only <code>{limits.min_excess_return_pa:g}%-{limits.max_excess_return_pa:g}% p.a.</code> "
            f"excess yield on contract value\n\n"
            f"<i>A message follows for every trade taken, with invested amount, "
            f"profit and the day's P&amp;L.</i>")

    recorder = None
    if cfg.record_ticks:
        recorder = TickRecorder(feed.store, bindings).start()
        log(f"recording book snapshots to {recorder.bin_path}")

    stop_at = time.time() + args.duration if args.duration else None

    def should_stop():
        if stop_at and time.time() > stop_at:
            return True
        return market_phase(start=cfg.session_start, end=cfg.session_end) == "closed" \
            and phase == "open"

    running = {"v": True}

    def on_signal(*_):
        running["v"] = False
        engine.stop()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, on_signal)     # ./run.sh restart: a normal stop here

    engine.start(lots=args.lots, min_edge_points=args.min_edge,
                 stop_predicate=should_stop)
    log(f"scanning ({mode}); Ctrl+C to stop")

    last_report = 0.0
    try:
        while running["v"] and engine._running.is_set():
            time.sleep(1.0)
            if dash:
                st = feed.store
                seen = set()
                for b in bindings:
                    if b.underlying in seen:
                        continue
                    mid = len(b.strikes) // 2
                    ce, pe = int(b.ce_slots[mid]), int(b.pe_slots[mid])
                    # Synthetic spot from the ATM call/put mid and the strike:
                    # fresher than another REST poll, and it only feeds the display.
                    if st.bid[ce] > 0 and st.bid[pe] > 0:
                        dash.state.spots[b.underlying] = (
                            float(b.strikes[mid])
                            + 0.5 * (st.bid[ce] + st.ask[ce])
                            - 0.5 * (st.bid[pe] + st.ask[pe]))
                        seen.add(b.underlying)
            if time.time() - last_report >= 60:
                last_report = time.time()
                for und, exp, lvl in settlements.record_todays_expiries(
                        expiring_pairs(bindings), spot_of):
                    log(f"settlement pinned: {und} {exp} at {lvl:,.2f}")
                s = engine.stats.summary()
                log(f"ticks={feed.ticks:,} passes={s['passes']:,} "
                    f"scan p50={s['scan_us_p50']}µs "
                    f"trades={risk.state.trades_today} "
                    f"pnl={_inr(risk.state.session_pnl)} "
                    f"equity={_inr(risk.equity)}")
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        # On an expiry day the scanner stops at session_end (15:15) but the
        # index settles at 15:30. If the stop was the scheduled one - not a
        # Ctrl+C - stay up on the still-live spot feed until then, so the
        # settlement level is pinned from the close itself.
        pairs = expiring_pairs(bindings)
        if pairs and running["v"]:
            from arb.instruments import parse_expiry
            latest = max(parse_expiry(e) for _, e in pairs)
            wait = (latest - now_ist()).total_seconds() + 45.0
            if 0 < wait < 45 * 60:
                log(f"expiry day: holding {wait / 60:.1f} min for the 15:30 close to pin "
                    f"{', '.join(f'{u} {e}' for u, e in sorted(pairs))}")
                end = time.time() + wait
                while running["v"] and time.time() < end:
                    time.sleep(1.0)
        for und, exp, lvl in settlements.record_todays_expiries(pairs, spot_of):
            log(f"settlement pinned: {und} {exp} at {lvl:,.2f}")
        # Let a trade that fired in the last second finish its margin lookup and
        # reach the journal and Telegram before anything else is torn down.
        execution.drain()
        margin.stop()
        feed.stop()
        if dash:
            if getattr(dash.state, "candles", None):
                dash.state.candles.stop()
            dash.stop()
        if recorder:
            recorder.stop()
            log(f"recorded {recorder.snapshots:,} snapshots -> {recorder.meta_path}")
            log(f"calibrate with: venv/bin/python -m arb.research {recorder.meta_path}")
        s = engine.stats.summary()
        log("─" * 60)
        log(f"ticks received     {feed.ticks:,} (dropped {feed.dropped})")
        log(f"scan passes        {s['passes']:,}")
        log(f"scan latency       p50 {s['scan_us_p50']}µs · p99 {s['scan_us_p99']}µs")
        log(f"tick→detect        p50 {s['tick_to_detect_us_p50']}µs · "
            f"p99 {s['tick_to_detect_us_p99']}µs")
        log(f"chains scanned     {engine.chains_scanned:,} "
            f"(skipped {engine.chains_skipped:,} unchanged)")
        log(f"quote health       {engine.quote_health_summary()}")
        log(f"implied rates      "
            f"{ {k: round(v, 4) for k, v in engine.implied_rates.items()} or 'none'}")
        log(f"signals            {engine.signals_by_strategy or 'none'}")
        log(f"rejected           {risk.state.rejects or 'none'}")
        log(f"trades             {risk.state.trades_today}")
        log(f"capital            {_inr(limits.total_capital)} "
            f"({limits.min_excess_return_pa:g}-{limits.max_excess_return_pa:g}% p.a. band)")
        log(f"session P&L        {_inr(risk.state.session_pnl)}  "
            f"({100.0 * risk.state.session_pnl / limits.stake_per_trade:+.3f}% of the "
            f"{_inr(limits.stake_per_trade)} stake)")
        log(f"equity             {_inr(risk.equity)}")
        if engine.best_seen:
            b = engine.best_seen
            log(f"best signal seen   {b.strategy} {b.gross_edge_points:+.2f}pt "
                f"({b.description})")
        if notifier:
            notifier.send(
                f"🔴 <b>ENGINE STOPPED</b>\n\n"
                f"Ticks <code>{feed.ticks:,}</code> · passes <code>{s['passes']:,}</code>\n"
                f"Scan p50 <code>{s['scan_us_p50']}µs</code> / "
                f"p99 <code>{s['scan_us_p99']}µs</code>\n"
                f"Trades <code>{risk.state.trades_today}</code>\n"
                f"Session P&amp;L <code>{_inr(risk.state.session_pnl)}</code> "
                f"(<code>{100.0 * risk.state.session_pnl / limits.stake_per_trade:+.3f}%</code> "
                f"of the {_inr(limits.stake_per_trade)} stake)\n"
                f"Signals <code>{engine.signals_by_strategy or 'none'}</code>\n"
                f"Rejects <code>{risk.state.rejects or 'none'}</code>")
            notifier.flush()
            notifier.stop()
            log(f"telegram           {notifier.sent} sent, {notifier.failed} failed")


if __name__ == "__main__":
    main()
