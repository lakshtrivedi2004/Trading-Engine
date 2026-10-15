"""Builds the HTML research report (single self-contained file)."""
import base64
import html
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import config
from decompose import explain_day, explanatory_power
from events import event_text
from flows import flow_text
from regime import regime_text

INK, MUTED, UP, DOWN, ACCENT, GRID = "#16212B", "#5E6B73", "#1F7A55", "#B3392E", "#2D4A8A", "#D9DEDD"
plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "axes.facecolor": "white",
})


def _img(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f'<img alt="" src="data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}">'


def pct(x, d=2, sign=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def cls(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return "up" if x > 0 else ("down" if x < 0 else "")


def esc(s):
    return html.escape(str(s))


# ------------------------------------------------------------------ charts
def chart_latest_decomposition(decomps):
    names = [n for n, (c, _) in decomps.items() if c is not None]
    if not names:
        return ""
    fig, axes = plt.subplots(1, len(names), figsize=(2.6 * len(names), 2.8), sharey=False)
    axes = np.atleast_1d(axes)
    for ax, n in zip(axes, names):
        c = decomps[n][0]
        row = c.iloc[-1]
        fac = [k[5:] for k in c.columns if k.startswith("move:")]
        labels = fac + ["Index-specific", "Actual move"]
        vals = [row[f] for f in fac] + [row["index-specific"], row["actual"]]
        colors = [UP if v >= 0 else DOWN for v in vals]
        colors[-1] = INK
        y = np.arange(len(labels))[::-1]
        ax.barh(y, np.array(vals) * 100, color=colors, height=0.65)
        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        ax.axvline(0, color=MUTED, lw=0.6)
        ax.set_title(f"{n}, {c.index[-1]:%d %b}")
        ax.set_xlabel("contribution, %")
    fig.tight_layout()
    return _img(fig)


def chart_prices(indices):
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.2))
    for ax, (n, df) in zip(axes.flat, indices.items()):
        c = df["Close"]
        dma = c.rolling(200).mean()
        c2, d2 = c.tail(500), dma.tail(500)
        ax.plot(c2.index, c2.values, color=INK, lw=1.1)
        ax.plot(d2.index, d2.values, color=ACCENT, lw=1, ls="--")
        ax.set_title(f"{n}: last 2 years (dashed = 200-day average)")
        ax.grid(axis="y", color=GRID, lw=0.5)
    for ax in list(axes.flat)[len(indices):]:
        ax.axis("off")
    fig.tight_layout()
    return _img(fig)


def chart_events(event_results, keep_words=("US fell", "US rose", "VIX", "big down")):
    names = [n for n in event_results if event_results[n]]
    if not names:
        return ""
    fig, axes = plt.subplots(1, len(names), figsize=(2.9 * len(names), 2.8), sharey=True)
    axes = np.atleast_1d(axes)
    palette = [DOWN, UP, ACCENT, "#8A6D1F", MUTED, "#6B3FA0"]
    for ax, n in zip(axes, names):
        shown = [(lbl, r) for lbl, r in event_results[n].items()
                 if any(w in lbl for w in keep_words)][:4]
        for (lbl, r), col in zip(shown, palette):
            short = lbl.replace(f"{n} ", "")
            ax.plot(r["path"].index, r["path"].values * 100, color=col, lw=1.4,
                    label=f"{short} (n={r['n']})")
        ax.axvline(0, color=MUTED, lw=0.6)
        ax.axhline(0, color=MUTED, lw=0.6)
        ax.set_title(n)
        ax.set_xlabel("days from event")
        ax.grid(color=GRID, lw=0.4)
    axes[0].set_ylabel("cumulative excess return, %")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False, fontsize=8,
               bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    return _img(fig)


# ------------------------------------------------------------------ html
CSS = """
:root{--bg:#F3F5F4;--ink:#16212B;--muted:#5E6B73;--up:#1F7A55;--down:#B3392E;--accent:#2D4A8A;
--rule:#D9DEDD;--panel:#FFFFFF}
@media (prefers-color-scheme:dark){:root{--bg:#121A20;--ink:#E4E9EC;--muted:#97A3AA;--up:#5CC395;
--down:#EE7B70;--accent:#8EA6E8;--rule:#2A353D;--panel:#1A242B}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
font-variant-numeric:tabular-nums}
main{max-width:1120px;margin:0 auto;padding:40px 22px 80px}
h1,h2,h3{font-family:"Iowan Old Style","Charter","Georgia",serif;font-weight:600;line-height:1.25}
h1{font-size:2rem;margin:0 0 4px}
h2{font-size:1.4rem;margin:56px 0 6px;padding-top:18px;border-top:2px solid var(--ink)}
h3{font-size:1.08rem;margin:28px 0 6px}
.sub{color:var(--muted);margin:0 0 28px}
.lede{font-family:"Iowan Old Style","Charter","Georgia",serif;font-size:1.2rem;line-height:1.55;max-width:70ch}
.lede p{margin:0 0 14px}
p,li{max-width:76ch}
.note{color:var(--muted);font-size:.9rem}
.up{color:var(--up)}.down{color:var(--down)}
.tablewrap{overflow-x:auto;margin:10px 0 6px;background:var(--panel);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--rule);white-space:nowrap;vertical-align:top}
td.l{white-space:normal;min-width:11ch}
td.l:nth-child(2){min-width:24ch}
td.l:last-child{min-width:19ch}
th{font-weight:600;color:var(--muted);background:var(--panel)}
td:first-child,th:first-child,td.l,th.l{text-align:left}
tr:last-child td{border-bottom:none}
.fig{background:#fff;border:1px solid var(--rule);border-radius:6px;padding:10px;margin:12px 0;overflow-x:auto}
.fig img{max-width:100%;height:auto;display:block}
.v-strong{color:var(--up);font-weight:600}.v-mod{color:var(--accent);font-weight:600}
.v-fade{color:var(--down)}.v-none{color:var(--muted)}
.warn{border-left:3px solid var(--down);padding:6px 12px;margin:8px 0;background:var(--panel)}
"""


def _table(headers, rows, left_cols=1, left=()):
    th = "".join(f'<th class="{"l" if (i < left_cols or i in left) else ""}">{esc(h)}</th>'
                 for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(rows_i) + "</tr>" for rows_i in rows)
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _td(val, c="", left=False):
    k = " ".join(x for x in [c, "l" if left else ""] if x)
    return f'<td class="{k}">{val}</td>'


def _vclass(v):
    if v.startswith("Strong") or v.startswith("Better"):
        return "v-strong"
    if v.startswith("Moderate") or v.startswith("Mixed"):
        return "v-mod"
    if v.startswith("Faded"):
        return "v-fade"
    return "v-none"


def build_report(asof, indices, decomps, regimes, vix, event_results, timing, cond,
                 flow_res, notes, demo=False):
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>India index research, {asof:%d %b %Y}</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>India index research: {asof:%A, %d %B %Y}</h1>")
    out.append(f"<p class='sub'>Nifty 50, Sensex, Bank Nifty and Bankex. "
               f"{'SIMULATED DEMO DATA. ' if demo else ''}Data: Yahoo Finance, end-of-day.</p>")

    # ---- Today in plain language
    out.append("<div class='lede'>")
    for n, (c, _) in decomps.items():
        out.append(f"<p>{esc(explain_day(n, c))}</p>")
    out.append("</div>")
    for line in regime_text(regimes, vix):
        out.append(f"<p>{esc(line)}</p>")
    if notes:
        out.append("<h3>Data notes</h3>" + "".join(f"<div class='warn'>{esc(n)}</div>" for n in notes))

    # ---- What moved the markets
    out.append("<h2>What moved the markets</h2>")
    out.append("<p>Each bar shows how much of the day's move came from each factor, using "
               "sensitivities estimated on the previous year only. The index-specific part is "
               "what the factors cannot explain: domestic news, flows, positioning, or noise.</p>")
    out.append(f"<div class='fig'>{chart_latest_decomposition(decomps)}</div>")
    for n, (c, _) in decomps.items():
        if c is None:
            continue
        rows = []
        fac = [k[5:] for k in c.columns if k.startswith("move:")]
        for d, r in c.tail(config.REPORT_DAYS).iloc[::-1].iterrows():
            top = max(fac, key=lambda f: abs(r[f]))
            unusual = "yes" if abs(r["index-specific"]) > 2 * r["resid_sd"] else ""
            rows.append([_td(f"{d:%d %b %Y}", left=True), _td(pct(r["actual"]), cls(r["actual"])),
                         _td(pct(r["explained"]), cls(r["explained"])),
                         _td(pct(r["index-specific"]), cls(r["index-specific"])),
                         _td(f"{esc(top)} ({pct(r[top])})", left=True), _td(unusual)])
        out.append(f"<h3>{esc(n)}: last {config.REPORT_DAYS} sessions</h3>")
        out.append(_table(["Date", "Actual", "Explained by factors", "Index-specific",
                           "Biggest factor", "Unusual?"], rows, left=(4,)))

    # ---- How much is explainable
    out.append("<h3>How much of daily movement the factors explain</h3>")
    rows = []
    for n, (c, b) in decomps.items():
        if c is None:
            continue
        lb = b.iloc[-1]
        sens = ", ".join(f"{k} {v:+.2f}" for k, v in lb.items())
        rows.append([_td(esc(n), left=True), _td(pct(explanatory_power(c), 0, False)),
                     _td(pct(explanatory_power(c, 252), 0, False)), _td(esc(sens), left=True)])
    out.append(_table(["Index", "All history", "Last year", "Current sensitivities (beta)"], rows, left=(3,)))
    out.append("<p class='note'>Out-of-sample R²: share of daily variance explained using only "
               "past data. Bank indices score high because they are explained by the broad index, "
               "which already contains banks. For Nifty and Sensex, a low number is normal and "
               "matches the research: most daily movement in a broad index is not explained by "
               "global markets, oil, rates or the rupee. A beta of 0.30 on the S&P 500 means a 1% "
               "US move overnight has typically gone with a 0.30% move here.</p>")

    # ---- Regime
    out.append("<h2>Market regime</h2>")
    rows = []
    for n, r in regimes.iterrows():
        rows.append([_td(esc(n), left=True), _td(f"{r['close']:,.0f}"),
                     _td(pct(r["ret_1d"]), cls(r["ret_1d"])), _td(pct(r["ret_1m"]), cls(r["ret_1m"])),
                     _td(pct(r["ret_1y"]), cls(r["ret_1y"])), _td(esc(r["trend"]), left=True),
                     _td(f"{esc(r['vol_regime'])} ({r['vol_pct']:.0f}th pct)", left=True),
                     _td(pct(r["drawdown"], 1)),
                     _td(num(r.get("corr_nifty_60d", np.nan)))])
    out.append(_table(["Index", "Close", "1 day", "1 month", "1 year", "Trend", "Volatility",
                       "From peak", "Corr. w/ Nifty (60d)"], rows, left=(5, 6)))
    out.append(f"<div class='fig'>{chart_prices(indices)}</div>")

    # ---- Events
    out.append("<h2>How the indices behave around events</h2>")
    out.append("<p>Each line is the average path across all past occurrences, in excess of a "
               "normal day. Overlapping events within 5 days are counted once.</p>")
    out.append(f"<div class='fig'>{chart_events(event_results)}</div>")
    for n, res in event_results.items():
        if not res:
            continue
        rows = []
        for lbl, r in res.items():
            rows.append([_td(esc(lbl), left=True), _td(r["n"]),
                         _td(pct(r["day0_mean"]), cls(r["day0_mean"])), _td(num(r["day0_t"], 1)),
                         _td(f"{r['move_size_ratio']:.1f}x"),
                         _td(pct(r["post5_mean"]), cls(r["post5_mean"])), _td(num(r["post5_t"], 1))])
        out.append(f"<h3>{esc(n)}</h3>")
        out.append(_table(["Event", "Cases", "Day 0 excess", "t-stat", "Day size vs normal",
                           "Next 5 days", "t-stat"], rows))
    nifty_res = event_results.get("Nifty 50", {})
    if nifty_res:
        out.append("<h3>In plain language (Nifty 50)</h3><ul>")
        out += [f"<li>{esc(event_text(l, r))}</li>" for l, r in nifty_res.items()]
        out.append("</ul>")
    out.append("<p class='note'>A |t-stat| of 2 or more means the effect is unlikely to be "
               "chance. Events with few cases (like budgets or elections) rarely reach that bar; "
               "treat them as descriptive history, not reliable patterns.</p>")

    # ---- Patterns
    out.append("<h2>Pattern tests</h2>")
    out.append(f"<p>Rules are tested on the first {config.TRAIN_FRACTION:.0%} of history, then "
               f"checked on the remaining {1 - config.TRAIN_FRACTION:.0%} they have never seen. "
               f"Costs of {config.COST_PER_SIDE:.2%} per trade are deducted.</p>")
    if timing is not None and len(timing):
        out.append("<h3>Timing rules (in the market or out of it)</h3>")
        rows = []
        for _, r in timing.iterrows():
            rows.append([_td(esc(r["index"]), left=True), _td(esc(r["rule"]), left=True),
                         _td(f"{num(r['sharpe_out'])} vs {num(r['bh_sharpe_out'])}"),
                         _td(f"{pct(r['maxdd_out'], 0)} vs {pct(r['bh_maxdd_out'], 0)}"),
                         _td(pct(r["time_invested"], 0, False)), _td(f"{r['trades_per_year']:.1f}"),
                         _td(f"<span class='{_vclass(r['verdict'])}'>{esc(r['verdict'])}</span>", left=True)])
        out.append("<p class='note'>All figures are out-of-sample. Sharpe ratio = return per unit "
                   "of risk; higher is better.</p>")
        out.append(_table(["Index", "Rule", "Sharpe: rule vs hold", "Worst fall: rule vs hold",
                           "Invested", "Trades/yr", "Verdict"], rows, left_cols=2, left=(6,)))
    if cond is not None and len(cond):
        out.append("<h3>Recurring effects</h3>")
        rows = []
        for _, r in cond.iterrows():
            rows.append([_td(esc(r["index"]), left=True), _td(esc(r["pattern"]), left=True),
                         _td(f"{pct(r['diff_in'], 3)} (t {num(r['t_in'], 1)}, n {r['n_in']})"),
                         _td(f"{pct(r['diff_out'], 3)} (t {num(r['t_out'], 1)}, n {r['n_out']})"),
                         _td(f"<span class='{_vclass(r['verdict'])}'>{esc(r['verdict'])}</span>", left=True)])
        out.append("<p class='note'>Average return on these days minus the average on all other "
                   "days, with t-statistic and number of cases.</p>")
        out.append(_table(["Index", "Pattern", "In-sample", "Out-of-sample", "Verdict"], rows,
                          left_cols=2, left=(4,)))
        n_tests = len(cond) + (0 if timing is None else len(timing))
        out.append(f"<p class='note'>{n_tests} tests were run. By chance alone, about 1 in 20 "
                   f"tests reaches |t| ≥ 2 even when no real effect exists, which is why a "
                   f"pattern needs to hold out-of-sample and across related indices before you "
                   f"trust it. Nifty/Sensex and Bank Nifty/Bankex are near-copies, so agreement "
                   f"between them is not independent confirmation.</p>")

    # ---- Flows
    out.append("<h2>Foreign and domestic institutional flows</h2>")
    out += [f"<p>{esc(l)}</p>" for l in flow_text(flow_res)]

    # ---- Method
    out.append("<h2>Method and limits</h2><ul>"
               "<li>US markets close after India, so overnight US moves are matched to the "
               "next Indian session. No rule or explanation uses information from the future.</li>"
               "<li>Explaining a move after it happens is far easier than predicting the next "
               "one. Low explanatory power for the broad indices is a real finding, not a bug.</li>"
               "<li>Yahoo Finance data is free but imperfect: check the data notes above, and "
               "verify anything you plan to act on against NSE or BSE.</li>"
               "<li>Past patterns can disappear once widely known (McLean &amp; Pontiff 2016). "
               "Re-run the tests regularly and watch whether out-of-sample results hold.</li>"
               "<li>This is a research tool, not investment advice.</li></ul>")
    out.append("</main></body></html>")
    return "\n".join(out)
