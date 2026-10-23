"""
Optional: FII/DII flow analysis (needs data/fii_dii.csv, filled from NSE).

Research basis: flows move prices in inelastic markets (Gabaix & Koijen 2021);
foreign investor flows are a well-documented driver in emerging markets
(e.g. Froot, O'Connell & Seasholes 2001; Richards 2005).
Note: same-day flows and returns influence each other, so a correlation
shows association, not proof of which causes which.
"""
import numpy as np
import pandas as pd


def analyse_flows(flows, nifty_df):
    if flows is None or nifty_df is None:
        return None
    r = nifty_df["Close"].pct_change().rename("ret")
    d = flows.join(r, how="inner").dropna(subset=["ret"])
    if len(d) < 30:
        return {"note": f"Only {len(d)} days of flow data overlap with prices; add more history "
                        f"(60+ days) for meaningful results.", "latest": flows.tail(5)}
    out = {"days": len(d), "latest": flows.tail(5)}
    for col in ["fii_net", "dii_net"]:
        if col in d:
            out[f"{col}_same_day_corr"] = d[col].corr(d["ret"])
            out[f"{col}_next_day_corr"] = d[col].corr(d["ret"].shift(-1))
            out[f"{col}_20d"] = d[col].tail(20).sum()
    if "fii_net" in d and "dii_net" in d:
        out["fii_dii_corr"] = d["fii_net"].corr(d["dii_net"])
    return out


def flow_text(res):
    if res is None:
        return ["No flow data. To add it, fill data/fii_dii.csv from NSE's daily FII/DII "
                "activity page (see README)."]
    if "note" in res:
        return [res["note"]]
    lines = [f"Based on {res['days']} days of FII/DII data."]
    if "fii_net_20d" in res:
        lines.append(f"FIIs net {'bought' if res['fii_net_20d'] > 0 else 'sold'} "
                     f"Rs {abs(res['fii_net_20d']):,.0f} crore over the last 20 sessions; "
                     f"same-day correlation with Nifty: {res['fii_net_same_day_corr']:+.2f}, "
                     f"with next day: {res['fii_net_next_day_corr']:+.2f}.")
    if "dii_net_20d" in res:
        lines.append(f"DIIs net {'bought' if res['dii_net_20d'] > 0 else 'sold'} "
                     f"Rs {abs(res['dii_net_20d']):,.0f} crore over the same period.")
    if "fii_dii_corr" in res and not np.isnan(res["fii_dii_corr"]):
        lines.append(f"FII vs DII flow correlation: {res['fii_dii_corr']:+.2f} "
                     f"(negative means domestic investors tend to absorb foreign selling).")
    return lines
