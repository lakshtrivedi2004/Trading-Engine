"""CLI.

    python -m scalper backtest --days 45     # walk-forward test on Angel 1-min history (no orders)
    python -m scalper paper                   # live market data, simulated fills (no orders)
    python -m scalper live                    # REAL ORDERS with real money
    python -m scalper sim                     # offline smoke test on synthetic data
    python -m scalper dashboard               # http://127.0.0.1:8050 (run next to paper/live)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

import pandas as pd

from . import backtest
from .config import Config, load_env
from .risk import Gate


def _logging(cfg: Config, tag: str):
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.log_dir / f"{tag}_{datetime.now():%Y%m%d}.log"),
    ])


def _print_report(title: str, trades: list[dict]):
    print(f"\n=== {title} ===")
    if not trades:
        print("no trades")
        return
    with pd.option_context("display.width", 160, "display.max_columns", 20):
        print(backtest.summarize(trades))
        d = backtest.daily(trades)
        print(f"\ndays: {len(d)} | green days: {(d.net > 0).sum()} | worst day ₹{d.net.min():.2f} | "
              f"best day ₹{d.net.max():.2f} | total ₹{d.net.sum():.2f}")


def walk_forward(bars: dict, index: pd.DataFrame, cfg: Config, ticks: dict):
    """Split the history: first 2/3 builds the gate, last 1/3 is traded out-of-sample like the live engine."""
    days = sorted({ts.date() for df in bars.values() for ts in df.index})
    if len(days) < 6:
        raise SystemExit("need at least 6 trading days of history")
    cut = days[int(len(days) * 2 / 3)]
    cut_ts = pd.Timestamp(cut)
    prepared = backtest.prepare(bars, index)
    all_ind = backtest.run(bars, index, cfg, ticks, portfolio=False, prepared=prepared)
    _print_report(f"ALL setups, every symbol independently, {days[0]} -> {days[-1]} (no gate)", all_ind)

    train = [t for t in all_ind if pd.Timestamp(t["entry_ts"]) < cut_ts]
    gate = Gate.from_trades(train, cfg, asof=str(cut))
    print(f"\ngate built on {days[0]} -> {days[days.index(cut) - 1]}: enabled = {sorted(gate.enabled) or 'NONE'}")
    for k, v in gate.strategies.items():
        print(f"  {k:14s} n={v['n']:4d} win={v['wins'] / v['n']:.0%} pf={v['pf']:.2f} exp=₹{v['exp']:.2f} edge={v['edge_r']:+.3f}R")

    feats, bias = prepared
    test_feats = {s: f[f.index >= cut_ts - pd.Timedelta(days=4)] for s, f in feats.items()}
    oos = backtest.run(bars, index, cfg, ticks, gate=gate, portfolio=True, prepared=(test_feats, bias))
    oos = [t for t in oos if pd.Timestamp(t["entry_ts"]) >= cut_ts]
    _print_report(f"OUT-OF-SAMPLE engine replay {cut} -> {days[-1]} (gate + ₹{cfg.max_daily_loss:.0f} kill switch)", oos)
    if oos:
        pd.DataFrame(oos).to_csv(cfg.log_dir / "backtest_oos_trades.csv", index=False)
    return all_ind, oos


VIX_TOKEN = "99926017"   # INDIA VIX on NSE (Angel scrip master)


def fetch_history(cfg: Config, days: int, include_today: bool = False) -> tuple[dict, pd.DataFrame, pd.DataFrame | None, dict]:
    """Download 1-min history once per day per symbol into data/hist/ (resumable, skips failures)."""
    from .broker import AngelClient, BrokerError, load_instruments
    hist_dir = cfg.data_dir / "hist"
    hist_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{datetime.now():%Y%m%d}_{days}d"
    inst = load_instruments(cfg.data_dir, cfg.universe, cfg.exchange)
    want = {**{s: i["token"] for s, i in inst.items()}, "NIFTY": cfg.index_token, "INDIAVIX": VIX_TOKEN}
    missing = [k for k in want if not (hist_dir / f"{k}_{tag}.csv").exists()]
    if missing:
        c = AngelClient(load_env())
        c.login()
        try:
            end = datetime.now()
            start = (end - timedelta(days=days)).replace(hour=9, minute=15)
            for k in missing:
                try:
                    df = c.candles(want[k], start, end, cfg.exchange)
                except BrokerError as e:
                    print(f"{k:12s} FAILED: {e}")
                    continue
                df.to_csv(hist_dir / f"{k}_{tag}.csv")
                print(f"{k:12s} {len(df):6d} bars")
        finally:
            c.logout()
    out = {}
    for k in want:
        f = hist_dir / f"{k}_{tag}.csv"
        if f.exists():
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if not include_today:
                df = df[df.index.date < datetime.now().date()]
            out[k] = df
    index = out.pop("NIFTY", pd.DataFrame())
    vix = out.pop("INDIAVIX", None)
    return out, index, vix, {s: inst[s]["tick"] for s in out if s in inst}


def cmd_backtest(args, cfg: Config):
    bars, index, vix, ticks = fetch_history(cfg, args.days, args.include_today)
    if not bars:
        raise SystemExit("no history downloaded")
    print(f"symbols: {', '.join(bars)}")
    walk_forward(bars, index, cfg, ticks)


def cmd_diag(args, cfg: Config):
    """Read-only account check: why are orders rejected? (no orders placed)"""
    import fcntl
    from .broker import AngelClient
    env = load_env()
    c = AngelClient(env)
    print(f"X-ClientPublicIP sent : {c.s.headers['X-ClientPublicIP']}   (STATIC_IP in .env: {env.get('STATIC_IP', '-')})")
    print(f"X-ClientLocalIP sent  : {c.s.headers['X-ClientLocalIP']}")
    c.login()
    engine_running = False
    lock = cfg.state_dir / "engine.lock"
    if lock.exists():
        with lock.open("a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                engine_running = True
    try:
        rms = c.rms()
        print(f"funds                 : available cash {rms.get('availablecash')} | net {rms.get('net')} | "
              f"intraday limit {rms.get('availableintradaypayin')}")
        book = c.order_book() or []
        print(f"orders today          : {len(book)}")
        for o in book[-15:]:
            print(f"  {o.get('updatetime') or o.get('ordertime', '')!s:20s} {o.get('transactiontype'):4s} {o.get('tradingsymbol'):24s} "
                  f"x{o.get('quantity'):>5s} {o.get('ordertype'):16s} {o.get('producttype'):10s} -> {o.get('status'):10s} {o.get('text', '')}")
        if not book:
            print("  (no orders reached Angel's order book -> rejected at the API gateway: IP/headers/API-key permissions)")
        pos = [p for p in c.positions() if int(float(p.get("netqty") or 0))]
        print(f"open positions        : {[(p['tradingsymbol'], p['netqty'], p.get('producttype')) for p in pos] or 'none'}")
    finally:
        if engine_running:
            print("(engine is running -> NOT logging out, so its session stays alive)")
        else:
            c.logout()


def cmd_sim(args, cfg: Config):
    from .synthetic import make_market
    bars, index = make_market(days=args.days, symbols=cfg.universe[:6], seed=args.seed)
    walk_forward(bars, index, cfg, {s: 0.05 for s in bars})


def cmd_run(args, cfg: Config, live: bool):
    from .engine import Engine
    if live and not args.yes:
        print(f"LIVE MODE: real orders, capital ₹{cfg.capital:,.0f}, daily stop ₹{cfg.max_daily_loss:.0f}.")
        if cfg.aggressive:
            print("AGGRESSIVE: index options enabled: "
                  f"{', '.join(cfg.indices) or 'none'}. Stock entries still need a positive recent after-cost edge. "
                  f"The ₹{cfg.max_daily_loss:.0f} kill switch still applies.")
        if input("Type LIVE to continue: ").strip() != "LIVE":
            raise SystemExit("aborted")
    import signal

    def _term(*_):                      # SIGTERM / closed terminal -> same clean shutdown as Ctrl+C
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGHUP, _term)
    Engine(cfg, load_env(), live=live).run()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scalper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--days", type=int, default=45)
    b.add_argument("--include-today", action="store_true")
    sub.add_parser("diag", help="read-only account/order check (why orders get rejected)")
    s = sub.add_parser("sim")
    s.add_argument("--days", type=int, default=24)
    s.add_argument("--seed", type=int, default=7)
    db = sub.add_parser("dashboard", help="local read-only web dashboard")
    db.add_argument("--port", type=int, default=8050)
    db.add_argument("--host", default="127.0.0.1")
    db.add_argument("--state-dir")
    db.add_argument("--log-dir")
    for name in ("paper", "live"):
        p = sub.add_parser(name)
        p.add_argument("--no-gate", action="store_true", help="paper only: trade every setup even without recent edge")
        p.add_argument("--aggressive", action="store_true",
                       help="paper: bypass the strategy gate; live: enable index options while keeping the stock gate")
        p.add_argument("--indices", default="NIFTY,BANKNIFTY,SENSEX", help="index options to trade ('' = none)")
        p.add_argument("--no-stocks", action="store_true", help="index options only")
        p.add_argument("--allow-existing", action="store_true",
                       help="live: start even if positions are open; they are left alone (never traded or closed)")
        p.add_argument("--yes", action="store_true", help="skip the LIVE confirmation prompt")
    for p in sub.choices.values():
        p.add_argument("--capital", type=float)
        p.add_argument("--max-daily-loss", type=float)
        p.add_argument("--symbols", help="comma separated, e.g. SBIN,ICICIBANK")
    args = ap.parse_args(argv)

    cfg = Config()
    if args.capital:
        cfg.capital = args.capital
    if args.max_daily_loss:
        cfg.max_daily_loss = args.max_daily_loss
        cfg.max_loss_per_trade = min(cfg.max_loss_per_trade, args.max_daily_loss)
    if args.symbols:
        cfg.universe = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    if getattr(args, "aggressive", False):
        cfg.aggressive = True
        # Let paper runs collect samples from all setups. Live stock entries always
        # require recent positive after-cost evidence; aggressive only enables options.
        if args.cmd == "paper":
            cfg.use_gate = False
    if hasattr(args, "indices"):
        cfg.indices = [x.strip().upper() for x in args.indices.split(",") if x.strip()]
    if getattr(args, "allow_existing", False):
        cfg.allow_existing = True
    if getattr(args, "no_stocks", False):
        cfg.trade_stocks = False
    if getattr(args, "no_gate", False):
        if args.cmd == "live":
            raise SystemExit("--no-gate is paper-only; live stock entries always require recent positive after-cost evidence")
        cfg.use_gate = False
    if args.cmd == "dashboard":
        from pathlib import Path
        from .dashboard import serve
        cfg.state_dir = Path(args.state_dir) if args.state_dir else cfg.state_dir
        cfg.log_dir = Path(args.log_dir) if args.log_dir else cfg.log_dir
        serve(cfg, args.host, args.port)
        return
    _logging(cfg, args.cmd)

    if args.cmd == "backtest":
        cmd_backtest(args, cfg)
    elif args.cmd == "diag":
        cmd_diag(args, cfg)
    elif args.cmd == "sim":
        cmd_sim(args, cfg)
    else:
        cmd_run(args, cfg, live=args.cmd == "live")


if __name__ == "__main__":
    main()
