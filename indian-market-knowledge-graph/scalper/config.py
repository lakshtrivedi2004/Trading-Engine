"""Engine configuration. Every money number is in rupees."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent

# Liquid NIFTY-50 names. Index options/futures do not fit ₹12k capital with a ₹75
# daily stop (one NIFTY option round trip alone can cost ~₹70), so the engine scalps
# cash-segment intraday (MIS) where size can be tuned to the rupee.
DEFAULT_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK",
    "INFY", "BHARTIARTL", "ITC", "BAJFINANCE", "TATASTEEL", "NTPC",
]


@dataclass
class Config:
    # ---- money / risk (hard limits)
    capital: float = 12_000.0
    max_daily_loss: float = 75.0         # realised + open loss, after all charges -> kill switch
    max_loss_per_trade: float = 35.0     # stop-out loss incl. charges + slippage
    min_trade_budget: float = 20.0       # below this remaining budget the engine stops for the day
    leverage: float = 5.0                # MIS exposure multiple (SEBI peak-margin 20%)
    exposure_buffer: float = 0.90        # use at most 90% of capital*leverage
    max_trades_per_day: int = 4
    max_open_positions: int = 1
    max_consecutive_losses: int = 1      # then pause
    pause_minutes_after_losses: int = 30
    cooldown_bars_after_exit: int = 3    # per symbol

    # ---- trade economics (calculation gate)
    min_net_rr: float = 1.2              # net reward / net risk after charges (EV check does the rest)
    min_stop_atr: float = 1.2            # stops narrower than this get widened (1m noise)
    min_stop_pct: float = 0.0025         # ... and never tighter than 0.25% of price (charges ~0.25% round trip)
    slippage_ticks: int = 1              # per side, used in sizing and backtests
    min_atr_pct: float = 0.0006          # skip dead tape (1m ATR < 0.06% of price)
    max_atr_pct: float = 0.0060          # skip news spikes

    # ---- exits
    breakeven_at_r: float = 1.0          # move stop to cost-covering breakeven
    trail_at_r: float = 1.5
    trail_atr_mult: float = 1.0
    time_stop_bars: int = 15             # exit if < 0.3R after this many bars
    time_stop_min_r: float = 0.3

    # ---- session (IST)
    entry_start: str = "09:30"
    entry_end: str = "14:45"
    flatten_at: str = "15:08"            # broker auto square-off is ~15:15-15:20

    # ---- strategy gate (walk-forward on recent history)
    gate_lookback_days: int = 30
    gate_min_trades: int = 8
    gate_min_pf: float = 1.20
    use_gate: bool = True

    # ---- execution
    entry_timeout_s: float = 3.0
    exit_timeout_s: float = 2.0
    entry_buffer_ticks: int = 2          # marketable-limit buffer
    sl_limit_buffer_pct: float = 0.002   # SL-L limit price beyond trigger
    max_orders_per_sec: float = 8.0      # stay under the 10 OPS retail-algo threshold
    reconcile_every_s: float = 5.0

    # ---- instruments
    trade_stocks: bool = True
    indices: list[str] = field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])  # traded via option buying
    allow_existing: bool = False         # live: leave positions that exist at startup alone (never traded/closed)
    aggressive: bool = False             # paper: bypass gate; live: permit index options but retain stock gate
    option_max_loss: float | None = None  # per option trade; None = whatever is left of the daily budget
    index_min_atr_pct: float = 0.00012
    index_max_atr_pct: float = 0.0040
    index_min_stop_atr: float = 1.2
    index_min_stop_pct: float = 0.0006   # stop at least 0.06% of the index (~15 NIFTY points)

    universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    index_token: str = "99926000"        # NIFTY 50 spot, used as a direction filter
    exchange: str = "NSE"

    state_dir: Path = ROOT / "state"
    data_dir: Path = ROOT / "data"
    log_dir: Path = ROOT / "logs"

    @property
    def max_exposure(self) -> float:
        return self.capital * self.leverage * self.exposure_buffer


def load_env(path: Path | None = None) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    path = path or PROJECT / ".env"
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("ANGLE_ONE_", "TELEGRAM_"))})
    return env
