"""FII/DII flow analysis.

Research basis: flows move prices in inelastic markets (Gabaix & Koijen 2021); foreign investor
flows are a documented driver in emerging markets (Froot, O'Connell & Seasholes 2001; Richards
2005). Same-day flows and returns influence each other, so a correlation shows association, not
which causes which. Next-day correlation uses flows known at day t and the return of day t+1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def analyse_flows(flows: pd.DataFrame | None, nifty: pd.DataFrame | None, min_days: int = 30) -> dict | None:
    if flows is None or nifty is None or flows.empty:
        return None
    r = nifty["Close"].pct_change().rename("ret")
    d = flows[["fii_net", "dii_net"]].join(r, how="inner")
    d["ret_next"] = r.shift(-1).reindex(d.index)
    out = {"days": len(d), "first": flows.index[0], "last": flows.index[-1],
           "sources": flows["source"].value_counts().to_dict() if "source" in flows else {},
           "table": flows[["fii_net", "dii_net"]].copy()}
    out["table"]["fii_20d"] = flows["fii_net"].rolling(20, min_periods=1).sum()
    out["table"]["dii_20d"] = flows["dii_net"].rolling(20, min_periods=1).sum()
    out["table"]["fii_cum"] = flows["fii_net"].cumsum()
    out["table"]["dii_cum"] = flows["dii_net"].cumsum()
    for col in ("fii_net", "dii_net"):
        out[f"{col}_20d"] = flows[col].tail(20).sum()
    if len(d.dropna(subset=["ret"])) < min_days:
        out["note"] = (f"Only {len(d)} day(s) of flow data overlap with prices. Correlations need {min_days}+ "
                       f"days; history builds up as you run 'imr update' each evening, or import older days into "
                       f"config/fii_dii_manual.csv.")
        return out
    for col in ("fii_net", "dii_net"):
        out[f"{col}_same_day_corr"] = d[col].corr(d["ret"])
        out[f"{col}_next_day_corr"] = d[col].corr(d["ret_next"])
    out["fii_dii_corr"] = d["fii_net"].corr(d["dii_net"])
    return out


def flow_lines(res: dict | None) -> list[str]:
    if res is None:
        return ["No FII/DII flow data stored yet. Run 'imr update' each evening (after ~6:30 pm IST) so NSE's "
                "latest day is saved, or import history into config/fii_dii_manual.csv."]
    src = ", ".join(f"{v} from {k}" for k, v in res.get("sources", {}).items())
    lines = [f"{res['days']} day(s) of FII/DII data ({src}), {res['first']:%d %b %Y} to {res['last']:%d %b %Y}."]
    fii, dii = res["fii_net_20d"], res["dii_net_20d"]
    lines.append(f"Over the last 20 sessions stored, FIIs net {'bought' if fii > 0 else 'sold'} Rs {abs(fii):,.0f} "
                 f"crore and DIIs net {'bought' if dii > 0 else 'sold'} Rs {abs(dii):,.0f} crore.")
    if "note" in res:
        lines.append(res["note"])
        return lines
    lines.append(f"Same-day correlation with Nifty: FII {res['fii_net_same_day_corr']:+.2f}, DII "
                 f"{res['dii_net_same_day_corr']:+.2f}. Next-day: FII {res['fii_net_next_day_corr']:+.2f}, "
                 f"DII {res['dii_net_next_day_corr']:+.2f}. Same-day links are association, not cause.")
    if not np.isnan(res["fii_dii_corr"]):
        lines.append(f"FII vs DII correlation {res['fii_dii_corr']:+.2f} (negative = domestic investors tend to "
                     f"absorb foreign selling).")
    return lines
