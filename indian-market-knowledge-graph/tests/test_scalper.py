import struct
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from scalper import backtest
from scalper.config import Config
from scalper.costs import round_trip, trade_costs
from scalper.feed import BarBuilder, Tick, parse_packet
from scalper.risk import DayRisk, Gate, Position, kill_file, size_signal
from scalper.strategies import Signal
from scalper.synthetic import make_market


def test_charges_angel_intraday():
    # ₹10k each way: brokerage 0.1% (₹10) per leg < ₹20 cap
    c = round_trip(10_000, 10_000)
    assert 25 < c < 30
    # big orders hit the ₹20 flat cap per leg
    assert round_trip(100_000, 100_000) < 2 * 20 + 0.00025 * 100_000 + 20
    assert trade_costs(1, 100, 101, 10) == pytest.approx(round_trip(1000, 1010))
    assert trade_costs(-1, 100, 99, 10) == pytest.approx(round_trip(990, 1000))


@pytest.mark.parametrize("seed", range(30))
def test_sizing_never_exceeds_budget(seed):
    rng = np.random.default_rng(seed)
    cfg = Config()
    e = float(rng.uniform(150, 3000))
    side = int(rng.choice([1, -1]))
    risk = e * float(rng.uniform(0.002, 0.01))
    sig = Signal("X", "orb", side, e, e - side * risk, e + side * 3 * risk, risk, "t")
    budget = float(rng.uniform(35, 70))
    sz = size_signal(sig, budget, cfg, 0.05)
    if sz is None:
        return
    slip = cfg.slippage_ticks * 0.05
    exit_px = sig.stop - side * slip
    entry_px = e + side * slip
    realised = side * (exit_px - entry_px) * sz.qty - trade_costs(side, entry_px, exit_px, sz.qty)
    assert sz.net_loss <= budget + 1e-9
    assert -realised <= budget + 1.0          # within a rupee of rounding
    assert sz.qty * e <= cfg.max_exposure


def test_sizing_rejects_trades_that_cannot_beat_costs():
    cfg = Config()
    # 0.05% target on a ₹1000 stock: charges alone exceed the reward
    sig = Signal("X", "orb", 1, 1000, 997, 1000.5, 1, "t")
    assert size_signal(sig, 70, cfg, 0.05) is None


def test_day_risk_budget_and_kill():
    cfg = Config(max_daily_loss=150.0, max_loss_per_trade=70.0, min_trade_budget=35.0)
    now = datetime(2026, 10, 5, 10, 0)
    r = DayRisk(cfg, now.date())
    assert r.trade_budget() == cfg.max_loss_per_trade
    r.on_close(-70, now)
    assert r.trade_budget() == 70            # 150 - 70 = 80 left, capped at 70
    r.on_close(-60, now)                     # 2 losses -> pause
    ok, why = r.can_open(now + timedelta(minutes=1))
    assert not ok and "paused" in why
    assert r.trade_budget() == 20            # below min_trade_budget -> no more trades anyway
    assert not r.check_kill(open_pnl=-19)
    assert r.check_kill(open_pnl=-21)
    assert not r.can_open(now + timedelta(hours=2))[0]


def test_position_breakeven_never_above_price():
    cfg = Config()
    p = Position("X", "1", "orb", 1, 10, 1000.0, 997.0, 1010.0, 2.0, 0.05, None)
    new, why = p.on_bar_close(close=1003.2, high=1003.5, low=1001, atr=2.0, cfg=cfg)
    assert why is None
    if new is not None:
        assert new < 1003.2


def test_gate_requires_positive_shrunk_edge():
    cfg = Config()
    win = [{"strategy": "a", "symbol": "S", "net": 30, "plan_loss": 60}] * 20 + \
          [{"strategy": "a", "symbol": "S", "net": -60, "plan_loss": 60}] * 6
    lucky = [{"strategy": "b", "symbol": "S", "net": 50, "plan_loss": 60}] * 3
    g = Gate.from_trades(win + lucky, cfg)
    assert "a" in g.enabled
    assert "b" not in g.enabled               # 3 trades is not evidence
    assert g.edge_r("missing") < 0


def test_ws_packet_parse_and_bars():
    def pkt(token, ts_ms, ltp, vol):
        b = bytearray(123)
        b[0], b[1] = 2, 1
        b[2:2 + len(token)] = token.encode()
        struct.pack_into("<qqq", b, 27, 1, ts_ms, int(round(ltp * 100)))
        struct.pack_into("<q", b, 67, vol)
        return bytes(b)

    base = int(datetime(2026, 10, 5, 4, 0).timestamp() * 1000)  # any epoch
    t = parse_packet(pkt("3045", base, 812.35, 1000))
    assert t.token == "3045" and t.ltp == pytest.approx(812.35) and t.day_volume == 1000
    bb = BarBuilder()
    t0 = datetime(2026, 10, 5, 10, 0, 5)
    assert bb.on_tick(Tick("1", t0, 100, 1000)) is None
    bb.on_tick(Tick("1", t0 + timedelta(seconds=20), 101, 1300))
    bb.on_tick(Tick("1", t0 + timedelta(seconds=40), 99.5, 1500))
    done = bb.on_tick(Tick("1", t0 + timedelta(seconds=60), 100.2, 1600))
    assert done["open"] == 100 and done["high"] == 101 and done["low"] == 99.5 and done["close"] == 99.5
    assert done["volume"] == 500


def test_backtest_portfolio_respects_daily_loss():
    cfg = Config()
    cfg.use_gate = False
    bars, idx = make_market(days=6, symbols=("AAA", "BBB", "CCC"), seed=3)
    trades = backtest.run(bars, idx, cfg, portfolio=True)
    if trades:
        d = backtest.daily(trades)
        # realised slippage on gaps can overshoot slightly, never by a whole trade
        assert d.net.min() >= -(cfg.max_daily_loss + 25)
        assert d.trades.max() <= cfg.max_trades_per_day


def test_paper_engine_stop_and_kill(tmp_path, monkeypatch):
    from scalper import engine as eng

    cfg = Config(state_dir=tmp_path / "s", data_dir=tmp_path / "d", log_dir=tmp_path / "l")
    e = eng.Engine(cfg, {"ANGLE_ONE_API": "k", "ANGLE_ONE_ID": "c", "ANGLE_ONE_PIN": "1", "ANGLE_ONE_TOTP": "JBSWY3DPEHPK3PXP"})
    e.inst = {"X": {"token": "1", "tradingsymbol": "X-EQ", "tick": 0.05, "lot": 1}}
    e.tok2sym = {"1": "X"}
    sig = Signal("X", "orb", 1, 1000, 995, 1015, 2, "t")
    now = datetime(2026, 10, 5, 10, 0)
    for k in range(3):
        if e.risk.killed:
            break
        sz = size_signal(sig, e.risk.trade_budget(), cfg, 0.05)
        e.pos = e.exec.open(sig, sz.qty, e.inst["X"], 1000, now)
        e._manage_tick(994.0)                  # through the stop
        assert e.pos is None
    assert e.risk.realized < 0
    assert e.risk.trades >= 2
    # two max-size losers + charges must not exceed 150 without the kill switch firing
    assert e.risk.killed or e.risk.realized > -cfg.max_daily_loss
    assert e.risk.trade_budget() < cfg.min_trade_budget or not e.risk.can_open(now)[0] or e.risk.killed


def test_only_one_engine_at_a_time(tmp_path):
    """paper + live share one Angel login: a second engine must be refused before it logs in/out."""
    from scalper import engine as eng

    cfg = Config(state_dir=tmp_path / "s", data_dir=tmp_path / "d", log_dir=tmp_path / "l")
    env = {"ANGLE_ONE_API": "k", "ANGLE_ONE_ID": "c", "ANGLE_ONE_PIN": "1", "ANGLE_ONE_TOTP": "JBSWY3DPEHPK3PXP"}
    paper = eng.Engine(cfg, env, live=False)
    paper._acquire_lock()
    with pytest.raises(SystemExit, match="PAPER engine"):
        eng.Engine(cfg, env, live=True)._acquire_lock()
    paper._lock_fh.close()
    eng.Engine(cfg, env, live=True)._acquire_lock()          # free again once the first one stops


def test_paper_and_live_state_are_separate(tmp_path):
    from scalper import engine as eng

    cfg = Config(state_dir=tmp_path / "s", data_dir=tmp_path / "d", log_dir=tmp_path / "l")
    env = {"ANGLE_ONE_API": "k", "ANGLE_ONE_ID": "c", "ANGLE_ONE_PIN": "1", "ANGLE_ONE_TOTP": "JBSWY3DPEHPK3PXP"}
    p, l = eng.Engine(cfg, env, live=False), eng.Engine(cfg, env, live=True)
    assert p.state_path != l.state_path and p.journal != l.journal and p.status_path != l.status_path
    assert kill_file(cfg, p.today, p.tag) != kill_file(cfg, l.today, l.tag)
    assert not p.exec.live and l.exec.live
