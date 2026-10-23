"""End-to-end example: a Varsity-style swing bot driven by the knowledge graph + tradekb.

Runs on synthetic data so it works offline; swap `load_prices()` for your broker/NSE data
(DataFrame with open/high/low/close/volume, DatetimeIndex).

    python3 examples/example_bot.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tradekb import KG, candles, indicators as ind, risk  # noqa: E402

CAPITAL = 500_000
RISK_PER_TRADE = 0.015          # [position_sizing] author example 1.5%


def load_prices(n=750, seed=11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 500 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.007, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.007, n)))
    v = rng.integers(1e5, 5e5, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v},
                        index=pd.bdate_range("2023-01-02", periods=n))


def main():
    kg = KG()
    df = load_prices()
    pats = candles.detect_all(df)
    plans = candles.trade_plans(df, pats)
    rsi = ind.rsi(df.close)
    macd = ind.macd(df.close)
    vol_avg = df.volume.rolling(10).mean()

    print(kg.card("ta_checklist", full=False), "\n")
    trades = []
    for _, p in plans[plans.tradable & (plans.side == "long")].iterrows():
        i = df.index.get_loc(p.time)
        if i < 60 or i + 1 >= len(df):
            continue
        hist = df.iloc[: i + 1]
        sr = ind.support_resistance(hist.iloc[-250:], k=3, tol=0.015, min_touches=2)
        supports = sr[sr.kind == "support"].level
        nearest_sr = float(supports.iloc[(supports - p.stop).abs().argmin()]) if len(supports) else None
        resist = sr[sr.kind == "resistance"].level
        target = float(resist.min()) if len(resist) else p.entry + 2 * (p.entry - p.stop)
        rrr = risk.reward_risk(p.entry, p.stop, target)
        check = risk.ta_checklist(
            has_pattern=True, stop=p.stop, nearest_sr=nearest_sr,
            volume_ok=bool(df.volume.iloc[i] > vol_avg.iloc[i]), rrr=rrr,
            indicators_confirm=bool(rsi.iloc[i] < 70 and macd.macd.iloc[i] > macd.signal.iloc[i]))
        if not check["take"]:
            continue
        qty = int(risk.position_size_risk(CAPITAL, RISK_PER_TRADE, p.entry, p.stop) * check["size_mult"])
        # simulate: exit at stop or target, else after 20 bars
        fut = df.iloc[i + 1: i + 21]
        hit_stop = fut.index[fut.low <= p.stop]
        hit_tgt = fut.index[fut.high >= target]
        if len(hit_stop) and (not len(hit_tgt) or hit_stop[0] <= hit_tgt[0]):
            exit_px = p.stop
        elif len(hit_tgt):
            exit_px = target
        else:
            exit_px = float(fut.close.iloc[-1])
        trades.append({"date": p.time.date(), "pattern": p.pattern, "entry": round(p.entry, 2),
                       "stop": round(p.stop, 2), "target": round(target, 2), "rrr": round(rrr, 2),
                       "score": check["score"], "qty": qty, "pnl": round((exit_px - p.entry) * qty, 0)})

    t = pd.DataFrame(trades).sort_values("date") if trades else pd.DataFrame()
    if t.empty:
        print("no trades passed the checklist")
        return
    print(t.tail(15).to_string(index=False))
    s = risk.trade_stats(t.pnl)
    print(f"\ntrades={s['trades']} win_rate={s['win_rate']:.0%} payoff={s['payoff_ratio']:.2f} "
          f"expectancy=Rs{s['expectancy']:.0f} kelly={s['kelly']:.2%} total=Rs{t.pnl.sum():,.0f}")
    print("\nSynthetic data - this demonstrates wiring, not an edge. Backtest on real data with costs first.")


if __name__ == "__main__":
    main()
