"""Position sizing, the cost/expectancy gate, daily kill switch and exit rules.

The core guarantee: a trade is only opened if its full stop-out loss
(price risk + slippage + both legs of charges) fits inside BOTH the per-trade
cap and whatever is left of the configured daily budget.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Config
from .costs import trade_costs
from .strategies import Signal


def round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 2)


@dataclass
class Sized:
    qty: int
    net_loss: float      # full stop-out loss incl. charges + slippage
    net_win: float       # target profit after charges + slippage
    costs: float
    edge_r: float        # expected net R per trade (from recent walk-forward stats)
    ev: float            # expected net rupees per trade

    @property
    def net_rr(self) -> float:
        return self.net_win / self.net_loss if self.net_loss > 0 else 0.0


def size_signal(sig: Signal, budget: float, cfg: Config, tick: float, edge_r: float | None = None) -> Sized | None:
    """Largest qty whose stop-out loss <= budget, then reject unless the after-cost maths is positive."""
    slip = cfg.slippage_ticks * tick
    risk_ps = sig.risk + 2 * slip
    reward_ps = sig.reward - 2 * slip
    if risk_ps <= 0 or reward_ps <= 0:
        return None
    qty = min(int(budget / risk_ps), int(cfg.max_exposure / sig.entry))
    while qty > 0:
        stop_exit = sig.stop - sig.side * slip
        costs_loss = trade_costs(sig.side, sig.entry, stop_exit, qty)
        if risk_ps * qty + costs_loss <= budget:
            break
        qty -= 1
    if qty <= 0:
        return None
    tgt_exit = sig.target - sig.side * slip
    costs_win = trade_costs(sig.side, sig.entry, tgt_exit, qty)
    net_loss = risk_ps * qty + costs_loss
    net_win = reward_ps * qty - costs_win
    e = 0.0 if edge_r is None else edge_r
    s = Sized(qty, net_loss, net_win, costs_win, e, e * net_loss)
    if net_win <= 0 or s.net_rr < cfg.min_net_rr or (edge_r is not None and s.ev <= 0):
        return None
    return s


# ---------------------------------------------------------------- strategy gate

def shrunk_mean(total: float, n: int, prior: float = -0.15, strength: int = 15) -> float:
    """Average pulled toward a pessimistic prior (-0.15R) so a lucky small sample can't switch a setup on."""
    return (total + prior * strength) / (n + strength)


@dataclass
class Gate:
    """Which (strategy, symbol) pairs earned the right to trade, from recent walk-forward stats."""
    strategies: dict = field(default_factory=dict)   # name -> {n, wins, pf, exp}
    symbols: dict = field(default_factory=dict)      # "strat|SYM" -> {n, exp}
    enabled: set = field(default_factory=set)
    asof: str = ""
    use: bool = True

    def allows(self, strat: str, sym: str) -> bool:
        if not self.use:
            return True
        if strat not in self.enabled:
            return False
        s = self.symbols.get(f"{strat}|{sym}")
        return not (s and s["n"] >= 4 and s["exp"] < 0)

    def edge_r(self, strat: str) -> float:
        s = self.strategies.get(strat)
        return s["edge_r"] if s else -0.15

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"asof": self.asof, "strategies": self.strategies,
                                    "symbols": self.symbols, "enabled": sorted(self.enabled)}, indent=2))

    @classmethod
    def load(cls, path: Path, use: bool = True) -> "Gate":
        if not path.exists():
            return cls(use=use)
        j = json.loads(path.read_text())
        return cls(j["strategies"], j["symbols"], set(j["enabled"]), j.get("asof", ""), use)

    @classmethod
    def from_trades(cls, trades: list[dict], cfg: Config, asof: str = "") -> "Gate":
        g = cls(asof=asof, use=cfg.use_gate)
        by_s: dict[str, list[float]] = {}
        by_r: dict[str, list[float]] = {}
        by_ss: dict[str, list[float]] = {}
        for t in trades:
            by_s.setdefault(t["strategy"], []).append(t["net"])
            by_r.setdefault(t["strategy"], []).append(t["net"] / t["plan_loss"] if t.get("plan_loss") else 0.0)
            by_ss.setdefault(f"{t['strategy']}|{t['symbol']}", []).append(t["net"])
        for k, pnl in by_s.items():
            gains = sum(x for x in pnl if x > 0)
            losses = -sum(x for x in pnl if x <= 0)
            pf = gains / losses if losses > 0 else (math.inf if gains > 0 else 0.0)
            g.strategies[k] = {"n": len(pnl), "wins": sum(x > 0 for x in pnl), "pf": round(pf, 3) if pf != math.inf else 99.0,
                               "exp": round(sum(pnl) / len(pnl), 2), "net": round(sum(pnl), 2),
                               "edge_r": round(shrunk_mean(sum(by_r[k]), len(pnl)), 4)}
            if len(pnl) >= cfg.gate_min_trades and pf >= cfg.gate_min_pf and g.strategies[k]["edge_r"] > 0:
                g.enabled.add(k)
        for k, pnl in by_ss.items():
            g.symbols[k] = {"n": len(pnl), "exp": round(sum(pnl) / len(pnl), 2)}
        return g


# ---------------------------------------------------------------- daily risk state

@dataclass
class DayRisk:
    cfg: Config
    day: date
    realized: float = 0.0
    trades: int = 0
    consecutive_losses: int = 0
    paused_until: object = None   # datetime
    killed: bool = False
    kill_reason: str = ""

    def trade_budget(self) -> float:
        # profits do not loosen the per-trade cap; losses shrink the budget
        return min(self.cfg.max_loss_per_trade, self.cfg.max_daily_loss + min(self.realized, 0.0))

    def can_open(self, now) -> tuple[bool, str]:
        if self.killed:
            return False, f"killed: {self.kill_reason}"
        if self.trades >= self.cfg.max_trades_per_day:
            return False, "max trades reached"
        if self.paused_until and now < self.paused_until:
            return False, f"paused until {self.paused_until:%H:%M}"
        if self.trade_budget() < self.cfg.min_trade_budget:
            return False, "remaining daily budget too small"
        return True, ""

    def on_close(self, net: float, now):
        from datetime import timedelta
        self.realized += net
        self.trades += 1
        self.consecutive_losses = self.consecutive_losses + 1 if net <= 0 else 0
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            self.paused_until = now + timedelta(minutes=self.cfg.pause_minutes_after_losses)
            self.consecutive_losses = 0

    def check_kill(self, open_pnl: float = 0.0) -> bool:
        if not self.killed and self.realized + open_pnl <= -self.cfg.max_daily_loss:
            self.killed = True
            self.kill_reason = f"daily loss {self.realized + open_pnl:.2f} <= -{self.cfg.max_daily_loss:.0f}"
        return self.killed


def kill_file(cfg: Config, day: date, mode: str = "LIVE") -> Path:
    return cfg.state_dir / f"KILLED_{day:%Y%m%d}_{mode.lower()}"


# ---------------------------------------------------------------- open position + exit rules

@dataclass
class Position:
    symbol: str
    token: str
    strategy: str
    side: int
    qty: int
    entry: float
    stop: float
    target: float
    atr: float
    tick: float
    opened: object
    init_risk: float = 0.0
    best: float = 0.0
    bars: int = 0
    be_done: bool = False
    plan_loss: float = 0.0          # full stop-out loss in rupees at entry (the trade's "1R" after charges)
    tradingsymbol: str = ""         # broker symbol (SBIN-EQ, NIFTY06OCT2625200CE ...)
    exchange: str = "NSE"
    kind: str = "EQ"                # charge schedule: EQ / NFO / BFO
    under: str | None = None        # options: underlying key whose spot levels also exit the trade
    under_stop: float | None = None
    under_target: float | None = None
    under_side: int = 0
    sl_order_id: str | None = None
    entry_order_id: str | None = None

    def __post_init__(self):
        if self.tick:
            self.stop, self.target = round_tick(self.stop, self.tick), round_tick(self.target, self.tick)
        self.tradingsymbol = self.tradingsymbol or f"{self.symbol}-EQ"
        self.init_risk = self.init_risk or abs(self.entry - self.stop)
        self.best = self.best or self.entry

    def r_multiple(self, price: float) -> float:
        return self.side * (price - self.entry) / self.init_risk if self.init_risk else 0.0

    def open_pnl(self, price: float) -> float:
        return self.side * (price - self.entry) * self.qty - trade_costs(self.side, self.entry, price, self.qty, self.kind)

    def stop_hit(self, low: float, high: float) -> bool:
        return low <= self.stop if self.side > 0 else high >= self.stop

    def target_hit(self, low: float, high: float) -> bool:
        return high >= self.target if self.side > 0 else low <= self.target

    def on_bar_close(self, close: float, high: float, low: float, atr: float, cfg: Config) -> tuple[float | None, str | None]:
        """Ratchet stop (breakeven / ATR trail) and evaluate the time stop. Returns (new_stop, exit_reason)."""
        self.bars += 1
        self.best = max(self.best, high) if self.side > 0 else min(self.best, low)
        best_r = self.r_multiple(self.best)
        new_stop = self.stop
        if not self.be_done:
            cps = trade_costs(self.side, self.entry, self.entry, self.qty, self.kind) / self.qty + self.tick
            moved = self.side * (self.best - self.entry)
            # only lock "breakeven after charges" once the move has comfortably paid them
            if best_r >= cfg.breakeven_at_r and moved >= 1.5 * cps:
                be = self.entry + self.side * cps
                if self.side * (close - be) > 0.25 * (atr if atr == atr else self.init_risk):
                    new_stop = max(new_stop, be) if self.side > 0 else min(new_stop, be)
                    self.be_done = True
        if best_r >= cfg.trail_at_r and atr == atr:
            trail = self.best - self.side * cfg.trail_atr_mult * atr
            new_stop = max(new_stop, trail) if self.side > 0 else min(new_stop, trail)
        new_stop = round_tick(new_stop, self.tick)
        changed = new_stop if self.side * (new_stop - self.stop) >= self.tick - 1e-9 else None   # only real tightening
        if self.bars >= cfg.time_stop_bars and self.r_multiple(close) < cfg.time_stop_min_r:
            return changed, "time_stop"
        return changed, None
