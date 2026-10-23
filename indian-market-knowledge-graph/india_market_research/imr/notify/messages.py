"""Builds every Telegram message (HTML) from engine results. The same texts appear on the dashboard."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd

from .. import engine
from ..analysis.explain import DISCLAIMER, explain_day, pct, regime_line, vix_line
from ..analysis.scanner import SCAN_NOTE

E = html.escape
FOOT = f"\n\n<i>{E(DISCLAIMER)}</i>"


def _demo(ctx) -> str:
    return "🧪 <b>SIMULATED DEMO DATA</b>\n" if ctx.is_demo else ""


def _arrow(x) -> str:
    return "🟢" if x > 0 else "🔴" if x < 0 else "⚪"


def forecast_block(fc: dict | None) -> list[str]:
    if not fc:
        return []
    lines = ["", "<b>Next session (forecast, with its track record)</b>"]
    for n, f in fc.items():
        d, z = f["direction"], f["size"]
        if "tomorrow" not in z:
            continue
        size_ok = "✓ beats naive" if z["verdict"].startswith("Beats") else "≈ naive"
        dir_txt = (f"P(up) {d['tomorrow']:.0%} — " + ("model beats baseline" if d["verdict"].startswith("Beats")
                   else "no edge, treat as a coin flip")) if "tomorrow" in d else ""
        lines.append(f"• <b>{E(n)}</b>: typical move ±{z['tomorrow'] * 100:.2f}% ({size_ok}); {E(dir_txt)}")
    return lines


def forecasts(ctx, fc: dict) -> str:
    lines = [_demo(ctx) + f"🔮 <b>Forecasts for the session after {ctx.asof:%d %b}</b>"]
    for n, f in fc.items():
        d, z = f["direction"], f["size"]
        lines += ["", f"<b>{E(n)}</b>"]
        if "tomorrow" in z:
            lines.append(f"Move size: ±{z['tomorrow'] * 100:.2f}% · {E(z['verdict'])} (error {z['mae'] * 100:.2f}% vs "
                         f"{z['baseline_mae'] * 100:.2f}% naive)")
        if "tomorrow" in d:
            lines.append(f"Direction: P(up) {d['tomorrow']:.0%} · {E(d['verdict'])} (right {d['hit_rate']:.1%} vs "
                         f"{d['baseline_hit_rate']:.1%} baseline on {d['n']:,} unseen days)")
    return "\n".join(lines) + FOOT


def daily_summary(ctx, settings, dec: dict, reg: pd.DataFrame, vix: dict | None, scan: dict | None,
                  fc: dict | None = None) -> str:
    kinds = engine.factor_kinds(settings)
    lines = [_demo(ctx) + f"📊 <b>India indices — {ctx.asof:%a %d %b %Y}</b>", ""]
    for n, r in reg.iterrows():
        lines.append(f"{_arrow(r['ret_1d'])} <b>{E(n)}</b> {r['close']:,.0f} ({pct(r['ret_1d'])}) · {E(r['trend'])}, "
                     f"vol {E(r['vol_regime'].lower())}")
    lines += ["", "<b>Why they moved</b>"]
    for n in ctx.indices:
        c = dec.get(n, (None, None))[0]
        lines.append("• " + E(explain_day(n, c, kinds)))
    lines += ["", "🌡 " + E(vix_line(vix))]
    lines += forecast_block(fc)
    if scan and scan.get("asof") is not None:
        k = settings.telegram["top_movers_in_summary"]
        g, lo = scan["lists"]["gainers"].head(k), scan["lists"]["losers"].head(k)
        lines += ["", f"<b>BSE movers {scan['asof']:%d %b}</b> ({scan['eligible']} liquid stocks scanned)"]
        lines.append("▲ " + ", ".join(f"{E(r.symbol)} {pct(r.ret, 1)}" for r in g.itertuples()))
        lines.append("▼ " + ", ".join(f"{E(r.symbol)} {pct(r.ret, 1)}" for r in lo.itertuples()))
        lines.append("<i>Watchlist, not advice.</i>")
    problems = ctx.health[ctx.health["status"] == "fail"] if len(ctx.health) else ctx.health
    if len(problems):
        lines += ["", f"⚠️ {len(problems)} data problem(s) - see /health"]
    return "\n".join(lines) + FOOT


def unusual_alerts(ctx, dec: dict) -> list[tuple[str, str]]:
    """[(dedupe_key, text)] for indices whose unexplained move today is > 2x normal."""
    out = []
    for n in ctx.indices:
        c = dec.get(n, (None, None))[0]
        if c is None or c.empty:
            continue
        row, d = c.iloc[-1], c.index[-1]
        if row["resid_sd"] > 0 and abs(row["index-specific"]) > 2 * row["resid_sd"]:
            ratio = abs(row["index-specific"]) / row["resid_sd"]
            out.append((f"unusual:{n}:{d:%Y-%m-%d}",
                        _demo(ctx) + f"🚨 <b>Unusual move: {E(n)}</b> ({d:%d %b})\n"
                        f"Moved {pct(row['actual'])}; global factors explain {pct(row['explained'])}, leaving "
                        f"<b>{pct(row['index-specific'])}</b> unexplained — {ratio:.1f}x its normal size.\n"
                        f"Look for domestic news, heavy FII/DII flows or positioning." + FOOT))
    return out


IMPORTANT_CHECKS = {"availability", "stale", "download", "revisions"}


def data_problems(ctx, rep=None) -> tuple[str, str] | None:
    h = ctx.health
    if h is None or h.empty:
        return None
    bad = h[(h["status"] == "fail") | ((h["status"] == "warn") & h["check_name"].isin(IMPORTANT_CHECKS))]
    nse_fail = [m for m in (rep.messages if rep else []) if "could not be fetched" in m]
    if bad.empty and not nse_fail:
        return None
    lines = [_demo(ctx) + "🛠 <b>Data problems after the update</b>"]
    lines += [f"• {E(m)}" for m in bad["message"].head(10)] + [f"• {E(m)}" for m in nse_fail]
    lines.append("\nDetails and what to do: dashboard → Today → Data health.")
    key = "data:" + "|".join(sorted(bad["series"] + ":" + bad["check_name"])) + ("|nse" if nse_fail else "") + \
          f":{pd.Timestamp(h['check_time'].iloc[0]).date()}"
    return key, "\n".join(lines)


def weekly_patterns(ctx, current: pd.DataFrame, previous: pd.DataFrame | None, tests_total: int) -> str:
    counts = current["verdict"].value_counts().to_dict()
    lines = [_demo(ctx) + f"🧪 <b>Weekly pattern check</b> ({len(current)} tests, {tests_total} run all time)",
             " · ".join(f"{k}: {v}" for k, v in counts.items())]
    if previous is not None and len(previous):
        m = current.merge(previous, on=["index", "rule_id"], suffixes=("", "_prev"))
        changed = m[m["verdict"] != m["verdict_prev"]]
        if len(changed):
            lines.append("\n<b>Verdicts that changed since last week</b>")
            lines += [f"• {E(r.index)} — {E(r.rule)}: {E(r.verdict_prev)} → <b>{E(r.verdict)}</b>"
                      for r in changed.itertuples()]
        else:
            lines.append("\nNo verdict changed since last week.")
    else:
        lines.append("\nFirst weekly check: next week's message will show what changed.")
    strong = current[current["verdict"].str.startswith(("Strong", "Moderate"))]
    if len(strong):
        lines.append("\n<b>Currently holding out-of-sample</b>")
        lines += [f"• {E(r.index)} — {E(r.rule)} ({E(r.verdict)})" for r in strong.itertuples()]
    lines.append("\nA pattern that keeps holding over many weeks deserves attention; one good week means little.")
    return "\n".join(lines) + FOOT


def movers(scan: dict | None, top: int = 10) -> str:
    if not scan or scan.get("asof") is None:
        return "No BSE stock data yet. It is downloaded by the evening update (imr update)."
    L = scan["lists"]
    lines = [f"📈 <b>BSE movers — {scan['asof']:%a %d %b %Y}</b>",
             f"{scan['eligible']} liquid stocks scanned out of {scan['universe']} traded.", ""]

    def block(title, df, fmt):
        out = [f"<b>{title}</b>"]
        for r in df.head(top).itertuples():
            flag = f" ⛔{E(r.circuit)}" if r.circuit else ""
            out.append(f"{E(r.symbol)} {fmt(r)}{flag}")
        return out + [""]
    lines += block("▲ Biggest gainers", L["gainers"], lambda r: f"{pct(r.ret, 1)} · ₹{r.close:,.1f} · {r.volume_x:.1f}x vol")
    lines += block("▼ Biggest losers", L["losers"], lambda r: f"{pct(r.ret, 1)} · ₹{r.close:,.1f} · {r.volume_x:.1f}x vol")
    lines += block("⚡ Most unusual vs own volatility", L["unusual"],
                   lambda r: f"{pct(r.ret, 1)} = {r.z:+.1f}σ")
    lines += block("🌪 Most volatile (20 days)", L["volatile"], lambda r: f"{r.vol_ann:.0%}/yr · ATR {r.atr_pct:.1%}")
    return "\n".join(lines) + f"<i>{E(SCAN_NOTE)}</i>"


def health(ctx) -> str:
    h = ctx.health
    if h is None or h.empty:
        return "No data-health results yet."
    bad = h[h["status"] != "ok"]
    icon = {"warn": "🟠", "fail": "🔴"}
    lines = [f"🩺 <b>Data health</b>: {len(h)} checks, {len(bad)} issue(s)"]
    lines += [f"{icon[r.status]} {E(r.message)}" for r in bad.itertuples()]
    return "\n".join(lines)


def regime(ctx, reg, vix) -> str:
    return "\n".join([_demo(ctx) + "🌡 <b>Regime</b>"] + ["• " + E(regime_line(n, r)) for n, r in reg.iterrows()]
                     + ["• " + E(vix_line(vix))])
