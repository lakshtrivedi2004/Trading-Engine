"""Event-driven 1-minute backtester sharing the live engine's strategy, sizing and exit code.

Fill model (deliberately pessimistic):
  * signal on bar close -> fill at NEXT bar open +/- slippage
  * if a bar touches both stop and target, the stop wins
  * stop gaps fill at the open; every exit pays slippage; Angel charges on every trade
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

import pandas as pd

from .config import Config
from .costs import trade_costs
from .features import add_features, index_bias
from .risk import DayRisk, Gate, Position, round_tick, size_signal
from .strategies import scan


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class Book:
    trades: list[dict] = field(default_factory=list)

    def close(self, pos: Position, ts, price: float, reason: str) -> dict:
        gross = pos.side * (price - pos.entry) * pos.qty
        costs = trade_costs(pos.side, pos.entry, price, pos.qty)
        t = {"symbol": pos.symbol, "strategy": pos.strategy, "side": pos.side, "qty": pos.qty,
             "entry_ts": pos.opened, "exit_ts": ts, "entry": pos.entry, "exit": price, "reason": reason,
             "gross": round(gross, 2), "costs": round(costs, 2), "net": round(gross - costs, 2),
             "r": round(pos.r_multiple(price), 2), "plan_loss": round(pos.plan_loss, 2)}
        self.trades.append(t)
        return t


def prepare(bars: dict[str, pd.DataFrame], index: pd.DataFrame | None) -> tuple[dict, pd.Series | None]:
    feats = {s: add_features(df) for s, df in bars.items() if len(df) > 60}
    bias = index_bias(index) if index is not None and len(index) else None
    return feats, bias


def _step_position(pos: Position, r: pd.Series, ts, cfg: Config, slip: float, flat_t: time):
    """Advance an open position by one bar. Returns (exit_price, reason) or (None, None)."""
    o, h, l, c = r["open"], r["high"], r["low"], r["close"]
    if pos.stop_hit(l, h):
        gap = (o <= pos.stop) if pos.side > 0 else (o >= pos.stop)
        px = o if gap else pos.stop
        return px - pos.side * slip, "stop"
    if pos.target_hit(l, h):
        return pos.target - pos.side * slip, "target"
    new_stop, why = pos.on_bar_close(c, h, l, r["atr"], cfg)
    if new_stop is not None:
        pos.stop = new_stop
    if why:
        return c - pos.side * slip, why
    if ts.time() >= flat_t:
        return c - pos.side * slip, "eod"
    return None, None


def run(bars: dict[str, pd.DataFrame], index: pd.DataFrame | None, cfg: Config, ticks: dict[str, float] | None = None,
        gate: Gate | None = None, portfolio: bool = True, prepared=None) -> list[dict]:
    """portfolio=True mimics the live engine (one book, daily kill switch, trade caps).
    portfolio=False trades every symbol independently to collect per-strategy statistics."""
    feats, bias = prepared or prepare(bars, index)
    ticks = ticks or {}
    start_t, end_t, flat_t = _t(cfg.entry_start), _t(cfg.entry_end), _t(cfg.flatten_at)
    book = Book()
    timeline = sorted(set().union(*[set(f.index) for f in feats.values()])) if feats else []
    pos_of: dict[str, Position] = {}
    pending: dict[str, tuple] = {}            # symbol -> (signal, sized) waiting for next open
    fired: dict[tuple, set] = {}
    cooldown: dict[str, int] = {}
    day_risk: DayRisk | None = None
    loc = {s: {ts: i for i, ts in enumerate(f.index)} for s, f in feats.items()}

    for ts in timeline:
        d = ts.date()
        if day_risk is None or day_risk.day != d:
            day_risk = DayRisk(cfg, d)
            pending.clear()
        for sym, f in feats.items():
            i = loc[sym].get(ts)
            if i is None:
                continue
            r = f.iloc[i]
            tick = ticks.get(sym, 0.05)
            slip = cfg.slippage_ticks * tick
            # 1) fill pending entry at this bar's open
            if sym in pending:
                sig, sz = pending.pop(sym)
                fill = round_tick(r["open"] + sig.side * slip, tick)
                if (sig.side > 0 and fill <= sig.stop) or (sig.side < 0 and fill >= sig.stop) or ts.date() != sig.ts.date():
                    continue
                pos_of[sym] = Position(sym, "", sig.strategy, sig.side, sz.qty, fill, sig.stop, sig.target,
                                       sig.atr, tick, ts, init_risk=abs(fill - sig.stop), plan_loss=sz.net_loss)
            # 2) manage open position
            pos = pos_of.get(sym)
            if pos is not None and pos.opened != ts:
                px, why = _step_position(pos, r, ts, cfg, slip, flat_t)
                if px is not None:
                    t = book.close(pos, ts, round_tick(px, tick), why)
                    del pos_of[sym]
                    cooldown[sym] = cfg.cooldown_bars_after_exit
                    if portfolio:
                        day_risk.on_close(t["net"], ts.to_pydatetime())
                        day_risk.check_kill()
            elif pos is not None and pos.opened == ts:
                # entry bar: stop can still be hit after the open fill
                if pos.stop_hit(r["low"], r["high"]):
                    t = book.close(pos, ts, round_tick(pos.stop - pos.side * slip, tick), "stop")
                    del pos_of[sym]
                    if portfolio:
                        day_risk.on_close(t["net"], ts.to_pydatetime())
                        day_risk.check_kill()
            if cooldown.get(sym):
                cooldown[sym] -= 1

        # 3) new signals on this bar's close
        if not (start_t <= ts.time() <= end_t):
            continue
        if portfolio:
            ok, _ = day_risk.can_open(ts.to_pydatetime())
            if not ok or len(pos_of) + len(pending) >= cfg.max_open_positions:
                continue
        b = int(bias.get(ts, 0)) if bias is not None else 0
        cands = []
        for sym, f in feats.items():
            i = loc[sym].get(ts)
            if i is None or sym in pos_of or sym in pending or cooldown.get(sym):
                continue
            fs = fired.setdefault((sym, ts.date()), set())
            for sig in scan(f, i, sym, b, fs, None, cfg.min_atr_pct, cfg.max_atr_pct,
                            cfg.min_stop_atr, cfg.min_stop_pct):
                if gate is not None and not gate.allows(sig.strategy, sym):
                    continue
                budget = day_risk.trade_budget() if portfolio else cfg.max_loss_per_trade
                e = gate.edge_r(sig.strategy) if (gate is not None and gate.use) else None
                sz = size_signal(sig, budget, cfg, ticks.get(sym, 0.05), e)
                if sz:
                    cands.append((sz.edge_r * min(sz.net_rr, 3.0), sig, sz))
        cands.sort(key=lambda x: -x[0])
        for _, sig, sz in cands:
            if sig.symbol in pending:
                continue
            pending[sig.symbol] = (sig, sz)
            fired[(sig.symbol, ts.date())].add(f"{sig.strategy}{sig.side:+d}")
            if portfolio and len(pos_of) + len(pending) >= cfg.max_open_positions:
                break

    # close anything left at the final bar
    for sym, pos in list(pos_of.items()):
        f = feats[sym]
        book.close(pos, f.index[-1], f["close"].iloc[-1], "end")
    return book.trades


def summarize(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    t = pd.DataFrame(trades)

    def agg(g):
        wins = g[g.net > 0].net.sum()
        losses = -g[g.net <= 0].net.sum()
        return pd.Series({"trades": len(g), "win%": round(100 * (g.net > 0).mean(), 1),
                          "net": round(g.net.sum(), 2), "exp/trade": round(g.net.mean(), 2),
                          "pf": round(wins / losses, 2) if losses else float("inf"),
                          "avg_R": round(g.r.mean(), 2), "costs": round(g.costs.sum(), 2)})

    out = t.groupby("strategy").apply(agg, include_groups=False)
    out.loc["ALL"] = agg(t)
    return out


def daily(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    t = pd.DataFrame(trades)
    t["day"] = pd.to_datetime(t["exit_ts"]).dt.date
    return t.groupby("day").agg(trades=("net", "size"), net=("net", "sum"), worst=("net", "min")).round(2)
