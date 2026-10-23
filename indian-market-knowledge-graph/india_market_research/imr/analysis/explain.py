"""Every plain-language sentence the platform shows, in one place."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .decompose import factor_names
from .stats import VERDICT_EXPLAINED

DISCLAIMER = ("This platform explains past moves and tests patterns on history. Its forecasts are statistical "
              "estimates, always shown with their measured accuracy - they are not certainties and not buy/sell advice.")


def pct(x, d=2, sign=True) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def fmt_move(kind: str, v: float) -> str:
    return f"{v:+.2f} pts" if kind == "diff" else f"{v * 100:+.2f}%"


def explain_day(index_name: str, contrib: pd.DataFrame | None, kinds: dict, date=None) -> str:
    if contrib is None or contrib.empty:
        return f"{index_name}: not enough data to explain the move."
    row = contrib.iloc[-1] if date is None else contrib.loc[date]
    d = contrib.index[-1] if date is None else pd.Timestamp(date)
    parts = sorted(((f, row[f], row[f"move:{f}"]) for f in factor_names(contrib)), key=lambda x: -abs(x[1]))
    actual = row["actual"]
    text = [f"On {d:%a %d %b %Y}, {index_name} {'rose' if actual >= 0 else 'fell'} {abs(actual) * 100:.2f}%."]
    drivers = [p for p in parts if abs(p[1]) >= 0.0005][:3]
    if drivers:
        bits = [f"{f} ({fmt_move(kinds.get(f, 'ret'), mv)}) contributed {c * 100:+.2f}%" for f, c, mv in drivers]
        text.append("Main measurable drivers: " + "; ".join(bits) + ".")
    else:
        text.append("None of the tracked global or macro factors moved enough to matter.")
    spec, sd = row["index-specific"], row["resid_sd"]
    text.append(f"The part not explained by these factors was {spec * 100:+.2f}%.")
    if sd > 0 and abs(spec) > 2 * sd:
        text.append("That unexplained part is unusually large (over 2x its normal size): look for domestic news, "
                    "heavy institutional flows or positioning.")
    return " ".join(text)


def regime_line(name: str, r) -> str:
    return (f"{name}: {r['trend'].lower()} (vs 50-day {pct(r['vs_dma50'], 1)}, vs 200-day {pct(r['vs_dma200'], 1)}); "
            f"volatility {r['vol_regime'].lower()} ({r['vol20'] * 100:.1f}% annualised, higher than "
            f"{r['vol_pct']:.0f}% of its history); {abs(r['drawdown']) * 100:.1f}% below its peak.")


def vix_line(v: dict | None) -> str:
    if not v:
        return "India VIX: no data."
    return (f"India VIX is {v['level']:.1f} ({v['change_1d']:+.2f} on the day), higher than {v['pct_rank']:.0f}% "
            f"of its history; 1-year average {v['avg_1y']:.1f}.")


def event_text(label: str, r: dict) -> str:
    direction = "up" if r["day0_mean"] > 0 else "down"
    sig = "statistically clear" if abs(r["day0_t"]) >= 2 else "not statistically clear"
    after = r["post5_mean"]
    follow = "continued in the same direction" if np.sign(after) == np.sign(r["day0_mean"]) else "partly reversed"
    follow_sig = "" if abs(r["post5_t"]) >= 2 else " (weak, could be chance)"
    return (f"{label} ({r['n']} cases): on the day the index moved {direction} {abs(r['day0_mean']) * 100:.2f}% "
            f"beyond normal ({sig}); a typical such day was {r['move_size_ratio']:.1f}x the size of a normal day. "
            f"Over the next 5 days it {follow} by {abs(after) * 100:.2f}%{follow_sig}.")


def verdict_text(v: str) -> str:
    return VERDICT_EXPLAINED.get(v, v)


T_STAT_GUIDE = (
    "The t-statistic measures how far an effect is from zero relative to its noise. Around |t| = 2 an effect "
    "would appear by pure chance about 1 time in 20 - and this platform runs dozens of tests, so several will "
    "cross 2 by luck. That is why a discovery needs |t| ≥ 3 in the older (in-sample) data AND must hold in the "
    "same direction on newer (out-of-sample) data. All t-statistics here use Newey-West standard errors, which "
    "allow for volatility clustering and day-to-day dependence.")
