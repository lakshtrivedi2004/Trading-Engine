"""Daily HTML snapshot (single self-contained file), adapted from the prototype report.

Uses the same engine results as the dashboard. Pattern tests in the snapshot are run with the
settings.toml defaults and are recorded in the test registry like any other run.
"""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .. import engine  # noqa: E402
from ..analysis.decompose import explanatory_power, factor_names  # noqa: E402
from ..analysis.explain import (DISCLAIMER, T_STAT_GUIDE, event_text, explain_day, pct,  # noqa: E402
                                regime_line, verdict_text, vix_line)
from ..analysis.flows import flow_lines  # noqa: E402
from ..analysis.patterns import family_summary  # noqa: E402

INK, MUTED, UP, DOWN, ACCENT, GRID = "#16212B", "#5E6B73", "#1F7A55", "#B3392E", "#2D4A8A", "#D9DEDD"
plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": INK,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "white",
                     "axes.facecolor": "white"})

CSS = """
:root{--bg:#F3F5F4;--ink:#16212B;--muted:#5E6B73;--up:#1F7A55;--down:#B3392E;--accent:#2D4A8A;--rule:#D9DEDD;--panel:#FFFFFF}
@media (prefers-color-scheme:dark){:root{--bg:#121A20;--ink:#E4E9EC;--muted:#97A3AA;--up:#5CC395;--down:#EE7B70;
--accent:#8EA6E8;--rule:#2A353D;--panel:#1A242B}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
main{max-width:1120px;margin:0 auto;padding:40px 16px 80px}
h1,h2,h3{font-family:"Iowan Old Style","Charter","Georgia",serif;font-weight:600;line-height:1.25}
h1{font-size:2rem;margin:0 0 4px}
h2{font-size:1.4rem;margin:56px 0 6px;padding-top:18px;border-top:2px solid var(--ink)}
h3{font-size:1.08rem;margin:28px 0 6px}
.sub{color:var(--muted);margin:0 0 28px}
.lede{font-family:"Iowan Old Style","Charter","Georgia",serif;font-size:1.15rem;line-height:1.55;max-width:70ch}
p,li{max-width:76ch}
.note{color:var(--muted);font-size:.9rem}
.up{color:var(--up)}.down{color:var(--down)}
.tablewrap{overflow-x:auto;margin:10px 0 6px;background:var(--panel);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--rule);white-space:nowrap;vertical-align:top}
td.l,th.l{text-align:left;white-space:normal}
th{font-weight:600;color:var(--muted)}
tr:last-child td{border-bottom:none}
.fig{background:#fff;border:1px solid var(--rule);border-radius:6px;padding:10px;margin:12px 0;overflow-x:auto}
.fig img{max-width:100%;height:auto;display:block}
.warn{border-left:3px solid var(--down);padding:6px 12px;margin:6px 0;background:var(--panel)}
.demo{background:var(--down);color:#fff;padding:10px 14px;border-radius:6px;font-weight:600;margin:0 0 18px}
.disc{border:1px solid var(--rule);background:var(--panel);padding:10px 14px;border-radius:6px}
"""


def esc(s) -> str:
    return html.escape(str(s))


def _img(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return f'<img alt="" src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}">'


def _table(headers, rows, left=(0,)) -> str:
    th = "".join(f'<th class="{"l" if i in left else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"l" if i in left else ""}">{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _chart_decomp(dec) -> str:
    names = [n for n, (c, _) in dec.items() if c is not None and "+" not in n]
    if not names:
        return ""
    fig, axes = plt.subplots(1, len(names), figsize=(3.0 * len(names), 2.9))
    for ax, n in zip(np.atleast_1d(axes), names):
        c = dec[n][0]
        row = c.iloc[-1]
        fac = factor_names(c)
        labels = fac + ["Index-specific", "Actual"]
        vals = np.array([row[f] for f in fac] + [row["index-specific"], row["actual"]]) * 100
        colors = [UP if v >= 0 else DOWN for v in vals]
        colors[-1] = INK
        y = np.arange(len(labels))[::-1]
        ax.barh(y, vals, color=colors, height=0.65)
        ax.set_yticks(y, labels)
        ax.axvline(0, color=MUTED, lw=0.6)
        ax.set_title(f"{n}, {c.index[-1]:%d %b}")
        ax.set_xlabel("contribution, %")
    fig.tight_layout()
    return _img(fig)


def _chart_prices(indices) -> str:
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.2))
    for ax, (n, df) in zip(axes.flat, indices.items()):
        c = df["Close"]
        ax.plot(c.tail(500).index, c.tail(500), color=INK, lw=1.1)
        ax.plot(c.tail(500).index, c.rolling(200).mean().tail(500), color=ACCENT, lw=1, ls="--")
        ax.set_title(f"{n}: last 2 years (dashed = 200-day average)")
        ax.grid(axis="y", color=GRID, lw=0.5)
    for ax in list(axes.flat)[len(indices):]:
        ax.axis("off")
    fig.tight_layout()
    return _img(fig)


def build_report(ctx, settings) -> str:
    dec = engine.run_decompositions(ctx, settings)
    reg, vix = engine.run_regime(ctx)
    ev = engine.run_events(ctx, settings)
    res, notes, total = engine.run_patterns(ctx, settings, register=True)
    fl = engine.run_flows(ctx)
    kinds = engine.factor_kinds(settings)
    asof = ctx.asof
    o = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
         f"content='width=device-width,initial-scale=1'><title>India index research {asof:%d %b %Y}</title>"
         f"<style>{CSS}</style></head><body><main>"]
    if ctx.is_demo:
        o.append("<div class='demo'>SIMULATED DEMO DATA - every number in this report is made up.</div>")
    o.append(f"<h1>India index research: {asof:%A, %d %B %Y}</h1>")
    o.append(f"<p class='sub'>Nifty 50, Sensex, Bank Nifty, Bankex. End-of-day data from the local database "
             f"({esc(ctx.db_path.name)}, mode {ctx.mode}).</p><p class='disc'>{esc(DISCLAIMER)}</p>")
    o.append("<div class='lede'>" + "".join(f"<p>{esc(explain_day(n, dec[n][0], kinds))}</p>"
                                            for n in ctx.indices if n in dec) + "</div>")
    o += [f"<p>{esc(regime_line(n, r))}</p>" for n, r in reg.iterrows()] + [f"<p>{esc(vix_line(vix))}</p>"]
    h = ctx.health
    if h is not None and len(h):
        probs = h[h["status"] != "ok"]
        o.append(f"<h3>Data health ({len(probs)} issue(s))</h3>" +
                 "".join(f"<div class='warn'>{esc(m)}</div>" for m in probs["message"]))
    if len(ctx.excluded):
        o.append("<p class='note'>Factor days excluded as data errors: " + esc(", ".join(
            f"{r.series} {r.date:%Y-%m-%d}" for r in ctx.excluded.itertuples())) + ".</p>")

    o.append("<h2>What moved the markets</h2><p>Contribution of each factor to the latest day's move, using "
             "sensitivities estimated on the previous year only.</p>")
    o.append(f"<div class='fig'>{_chart_decomp(dec)}</div>")
    for n, (c, _) in dec.items():
        if c is None:
            continue
        fac = factor_names(c)
        rows = []
        for d, r in c.tail(settings.report["recent_days"]).iloc[::-1].iterrows():
            top = max(fac, key=lambda f: abs(r[f]))
            rows.append([f"{d:%d %b %Y}", pct(r["actual"]), pct(r["explained"]), pct(r["index-specific"]),
                         f"{esc(top)} ({pct(r[top])})", "yes" if abs(r["index-specific"]) > 2 * r["resid_sd"] else ""])
        o.append(f"<h3>{esc(n)}</h3>" + _table(["Date", "Actual", "Explained", "Index-specific", "Biggest factor",
                                                 "Unusual?"], rows, left=(0, 4)))
    rows = [[esc(n), pct(explanatory_power(c), 0, False), pct(explanatory_power(c, 252), 0, False),
             esc(", ".join(f"{k} {v:+.2f}" for k, v in b.iloc[-1].items()))]
            for n, (c, b) in dec.items() if c is not None]
    o.append("<h3>Explanatory power (out-of-sample R²)</h3>" +
             _table(["Index", "All history", "Last year", "Current betas"], rows, left=(0, 3)))

    o.append("<h2>Market regime</h2>")
    o.append(_table(["Index", "Close", "1 day", "1 month", "1 year", "Trend", "Volatility", "From peak"],
                    [[esc(n), f"{r['close']:,.0f}", pct(r["ret_1d"]), pct(r["ret_1m"]), pct(r["ret_1y"]),
                      esc(r["trend"]), f"{esc(r['vol_regime'])} ({r['vol_pct']:.0f}th pct)", pct(r["drawdown"], 1)]
                     for n, r in reg.iterrows()], left=(0, 5, 6)))
    o.append(f"<div class='fig'>{_chart_prices(ctx.indices)}</div>")

    o.append("<h2>Events</h2><p>Average path across past occurrences, in excess of a normal day measured over the 250 "
             "sessions before each event.</p>")
    for n, rr in ev.items():
        if not rr:
            continue
        o.append(f"<h3>{esc(n)}</h3>" + _table(
            ["Event", "Cases", "Day 0 excess", "t", "Next 5 days", "t", "Warning"],
            [[esc(lbl), r["n"], pct(r["day0_mean"]), f"{r['day0_t']:.1f}", pct(r["post5_mean"]),
              f"{r['post5_t']:.1f}", esc(r["warning"])] for lbl, r in rr.items()], left=(0, 6)))
    if "Nifty 50" in ev:
        o.append("<ul>" + "".join(f"<li>{esc(event_text(lbl, r))}</li>" for lbl, r in ev["Nifty 50"].items()) + "</ul>")

    o.append(f"<h2>Pattern tests</h2><p>In-sample: first {settings.research['train_fraction']:.0%} of history; "
             f"out-of-sample: the rest. Costs {settings.research['cost_per_side']:.2%} per side. Tests run on this "
             f"database, all time: <b>{total}</b>.</p>")
    if not res.empty:
        o.append(_table(["Index", "Rule", "t in", "t out", "Verdict"],
                        [[esc(r["index"]), esc(r["rule"]), f"{r['t_in']:+.1f}",
                          "–" if pd.isna(r["t_out"]) else f"{r['t_out']:+.1f}", esc(r["verdict"])]
                         for _, r in res.iterrows()], left=(0, 1, 4)))
        fs = family_summary(res)
        o.append("<h3>Near-duplicates merged</h3>" + _table(
            ["Rule", "Family", "Indices", "Best verdict", "All agree"],
            [[esc(r.rule), esc(r.family), esc(r.indices), esc(r.best_verdict), "yes" if r.all_agree else "no"]
             for r in fs.itertuples()], left=(0, 1, 2, 3)))
        o.append("<ul>" + "".join(f"<li><b>{esc(v)}</b>: {esc(verdict_text(v))}</li>"
                                  for v in res["verdict"].unique()) + "</ul>")
        o.append(f"<p class='note'>{esc(T_STAT_GUIDE)}</p>")
    o += [f"<div class='warn'>{esc(n)}</div>" for n in notes]

    o.append("<h2>FII/DII flows</h2>" + "".join(f"<p>{esc(x)}</p>" for x in flow_lines(fl)))
    o.append(f"<h2>Method and limits</h2><ul><li>US markets close after India, so US moves count on the next Indian "
             f"session. No rule or explanation uses information from the future; every trading rule passes an "
             f"automatic look-ahead check.</li><li>Explaining a move after it happens is far easier than predicting "
             f"the next one.</li><li>Yahoo Finance data is free but imperfect: see the data-health notes and verify "
             f"anything important against NSE/BSE.</li><li>Patterns can fade once known (McLean &amp; Pontiff 2016).</li>"
             f"<li>{esc(DISCLAIMER)}</li></ul></main></body></html>")
    return "\n".join(o)


def write_report(ctx, settings, paths) -> Path:
    html_text = build_report(ctx, settings)
    out = paths.reports_dir / f"{'demo_' if ctx.is_demo else ''}report_{ctx.asof:%Y-%m-%d}.html"
    out.write_text(html_text, encoding="utf-8")
    return out
