"""Local Flask dashboard.  Start with:  imr dashboard   (serves on 127.0.0.1 only)

All numbers come from the engine, which reads only the local SQLite database.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, abort, flash, redirect, render_template, request, url_for

from .. import engine
from ..analysis import regime as regime_mod
from ..analysis.decompose import explanatory_power, factor_names
from ..analysis.explain import (DISCLAIMER, T_STAT_GUIDE, event_text, explain_day, pct, regime_line, verdict_text,
                                vix_line)
from ..analysis.flows import flow_lines
from ..analysis.patterns import family_summary, rule_library
from ..log import UserError
from ..paths import Paths, get_paths
from ..settings import load_settings
from . import charts

PAGES = [("today", "Today", "◉"), ("moves", "What moved", "◧"), ("movers", "Stock movers", "⚡"),
         ("forecast", "Forecast lab", "◈"), ("regime", "Regime", "◐"), ("events", "Events", "◷"),
         ("patterns", "Pattern lab", "⬡"), ("flows", "Flows", "⇄"), ("updates", "Updates", "✉")]
VERDICT_CLASS = {"Strong": "v-strong", "Moderate": "v-mod", "Faded": "v-fade"}


class State:
    """In-process cache keyed by database file + modification time (single local user)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict = {}
        self.lab: dict = {}

    def get(self, path: Path, settings, what: str, fn):
        key = (str(path), path.stat().st_mtime, what)
        with self.lock:
            if key not in self.cache:
                self.cache = {k: v for k, v in self.cache.items() if k[:2] == key[:2]}   # drop stale entries
                self.cache[key] = fn()
            return self.cache[key]


def create_app(paths: Paths | None = None) -> Flask:
    paths = paths or get_paths()
    app = Flask(__name__)
    app.secret_key = "local-only-dashboard"
    state = State()

    # ------------------------------------------------------------ helpers
    def settings():
        return load_settings(paths.settings_file)

    def modes() -> list[str]:
        return [m for m, demo in (("real", False), ("demo", True)) if paths.db_file(demo).exists()]

    def current_mode() -> str:
        avail = modes()
        if not avail:
            raise UserError("No database found.", "Run 'imr update' (real data) or 'imr demo' (simulated) first.")
        m = request.args.get("mode") or request.cookies.get("mode") or avail[0]
        return m if m in avail else avail[0]

    def ctx_for(mode: str):
        s = settings()
        path = paths.db_file(mode == "demo")
        ctx = state.get(path, s, "ctx", lambda: engine.load_context(path, s))
        return ctx, s

    def cached(ctx, s, what, fn):
        return state.get(ctx.db_path, s, what, fn)

    def page(template: str, **kw):
        mode = kw.pop("_mode")
        ctx = kw.get("ctx")
        resp = app.make_response(render_template(template, mode=mode, modes=modes(), pages=PAGES,
                                                 asof=ctx.asof if ctx is not None else None,
                                                 disclaimer=DISCLAIMER, endpoint=request.endpoint, **kw))
        resp.set_cookie("mode", mode, samesite="Lax")
        return resp

    @app.template_filter("pct")
    def _pct(x, d=2, sign=True):
        return pct(x, d, sign)

    @app.template_filter("num")
    def _num(x, d=2):
        return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{d}f}"

    @app.template_filter("sign")
    def _sign(x):
        return "" if x is None or (isinstance(x, float) and np.isnan(x)) else ("up" if x > 0 else "down" if x < 0 else "")

    @app.template_filter("plain")
    def _plain(t):
        import html as _h
        import re as _re
        return _h.unescape(_re.sub(r"<[^>]+>", "", str(t)))

    @app.template_filter("vclass")
    def _vclass(v):
        return next((c for k, c in VERDICT_CLASS.items() if str(v).startswith(k)), "v-none")

    @app.errorhandler(UserError)
    def _user_error(e):
        return render_template("error.html", problem=e.problem, next_step=e.next_step, pages=PAGES, modes=[],
                               mode="", disclaimer=DISCLAIMER, endpoint=None, asof=None), 503

    # ------------------------------------------------------------ pages
    @app.route("/")
    def today():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        dec = cached(ctx, s, "dec", lambda: engine.run_decompositions(ctx, s))
        reg, vix = cached(ctx, s, "regime", lambda: engine.run_regime(ctx))
        kinds = engine.factor_kinds(s)
        explanations = [(n, explain_day(n, dec.get(n, (None, None))[0], kinds),
                         explain_day(n, dec[f"{n} + flows"][0], kinds) if f"{n} + flows" in dec else None)
                        for n in ctx.indices]
        h = ctx.health
        scan = cached(ctx, s, "scan", lambda: engine.run_scan(ctx, s))
        fc = cached(ctx, s, "forecast", lambda: engine.run_forecasts(ctx, s))
        return page("today.html", _mode=mode, ctx=ctx, reg=reg, explanations=explanations, scan=scan, fc=fc,
                    regime_lines=[regime_line(n, r) for n, r in reg.iterrows()], vix=vix_line(vix),
                    health=h, problems=h[h["status"] != "ok"] if len(h) else h,
                    missing=[n for n in s.indices if n not in ctx.indices], excluded=ctx.excluded)

    @app.route("/moves")
    def moves():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        dec = cached(ctx, s, "dec", lambda: engine.run_decompositions(ctx, s))
        avail = [n for n, (c, _) in dec.items() if c is not None]
        if not avail:
            raise UserError("Not enough data to decompose any index.", "Run 'imr update' and check Data health.")
        name = request.args.get("index", avail[0])
        name = name if name in avail else avail[0]
        c, b = dec[name]
        days = list(c.index[::-1][:250])
        day = pd.to_datetime(request.args.get("day", ""), errors="coerce")
        day = day if day in c.index else days[0]
        n = max(5, min(request.args.get("n", s.report["recent_days"], type=int), 60))
        fac = factor_names(c)
        row = c.loc[day]
        bar = charts.hbar(fac + ["Index-specific", "Actual move"],
                          [row[f] * 100 for f in fac] + [row["index-specific"] * 100, row["actual"] * 100],
                          f"{name}, {day:%d %b %Y}: contribution to the move (%)")
        bar["datasets"][0]["color"][-1] = charts.MUTED
        recent = []
        for d, r in c.tail(n).iloc[::-1].iterrows():
            top = max(fac, key=lambda f: abs(r[f]))
            recent.append({"date": d, "actual": r["actual"], "explained": r["explained"], "spec": r["index-specific"],
                           "top": top, "top_val": r[top], "unusual": abs(r["index-specific"]) > 2 * r["resid_sd"]})
        power = [{"name": k, "all": explanatory_power(cc), "year": explanatory_power(cc, 252),
                  "betas": ", ".join(f"{f} {v:+.2f}" for f, v in bb.iloc[-1].items())}
                 for k, (cc, bb) in dec.items() if cc is not None]
        beta_chart = charts.line({col: b[col] for col in b.columns}, "Sensitivities (rolling 1-year betas)",
                                 y_title="beta")
        return page("moves.html", _mode=mode, ctx=ctx, avail=avail, name=name, days=days, day=day, n=n,
                    text=explain_day(name, c, engine.factor_kinds(s), day), bar=bar, recent=recent, power=power,
                    beta_chart=beta_chart, bar_height=34 * (len(fac) + 2) + 60)

    @app.route("/regime")
    def regime():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        reg, vix = cached(ctx, s, "regime", lambda: engine.run_regime(ctx))
        names = list(ctx.indices)
        name = request.args.get("index", names[0])
        name = name if name in names else names[0]
        years = max(1, min(request.args.get("years", 3, type=int), 16))
        h = regime_mod.history(ctx.indices[name])
        h = h[h.index >= h.index[-1] - pd.DateOffset(years=years)]
        price = charts.line({"Close": h["close"], "50-day avg": h["dma50"], "200-day avg": h["dma200"]},
                            f"{name} with 50- and 200-day averages", colors=[charts.INK, charts.ACCENT, charts.GOLD])
        dd = charts.line({"Drawdown %": h["drawdown"] * 100}, "Distance from peak (%)", unit="pct",
                         colors=[charts.DOWN])
        vol = charts.line({"Volatility percentile": h["vol_pct"]}, "Volatility percentile (vs history up to each day)",
                          colors=[charts.ACCENT], y_min=0, y_max=100)
        vix_chart = None
        if "India VIX" in ctx.factors:
            v = ctx.factors["India VIX"]["Close"]
            v = v[v.index >= v.index[-1] - pd.DateOffset(years=years)]
            vix_chart = charts.line({"India VIX": v}, "India VIX", colors=[charts.DOWN])
        cm = cached(ctx, s, "corr", lambda: regime_mod.correlation_matrix(ctx.indices, ctx.factors))
        return page("regime.html", _mode=mode, ctx=ctx, reg=reg, lines=[regime_line(n, r) for n, r in reg.iterrows()],
                    vix=vix_line(vix), names=names, name=name, years=years, price=price, dd=dd, vol=vol,
                    vix_chart=vix_chart, cm=cm, corr_color=charts.corr_color)

    @app.route("/events")
    def events():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        ev = cached(ctx, s, "events", lambda: engine.run_events(ctx, s))
        names = [n for n in ev if ev[n]]
        if not names:
            raise UserError("No event studies could be computed.", "Run 'imr update' and check Data health.")
        sel_idx = [n for n in request.args.getlist("index") if n in names] or names[:1]
        cats = sorted({r["category"] for n in names for r in ev[n].values()})
        sel_cat = [c for c in request.args.getlist("cat") if c in cats] or cats
        labels = sorted({lbl for n in sel_idx for lbl, r in ev[n].items() if r["category"] in sel_cat})
        sel_lbl = [x for x in request.args.getlist("event") if x in labels] or labels[:4]
        rows, paths_, texts = [], {}, []
        for n in sel_idx:
            for lbl in sel_lbl:
                r = ev[n].get(lbl)
                if not r:
                    continue
                paths_[f"{n}: {lbl} (n={r['n']})"] = r["path"] * 100
                rows.append({"index": n, "label": lbl, **r})
                texts.append((n, event_text(lbl, r), r["warning"]))
        chart = charts.offsets_line(paths_, "Average path around the event (excess over a normal day)")
        notes = []
        if not (ctx.events["type"].str.lower() == "rbi policy").any():
            notes.append("RBI policy days: add dates from rbi.org.in to config/events.csv (type 'RBI policy').")
        if not ctx.expiry:
            notes.append("F&O expiry: no expiry rules loaded (config/expiry_rules.csv).")
        return page("events.html", _mode=mode, ctx=ctx, names=names, sel_idx=sel_idx, cats=cats, sel_cat=sel_cat,
                    labels=labels, sel_lbl=sel_lbl, rows=rows, path_chart=chart, texts=texts, notes=notes)

    @app.route("/patterns", methods=["GET", "POST"])
    def patterns():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        r = s.research
        form = {"cost_bp": r["cost_per_side"] * 1e4, "train": r["train_fraction"], "gap": r["split_gap_days"],
                "wf": r["walk_forward"]}
        if request.method == "POST":
            try:
                form = {"cost_bp": float(request.form["cost_bp"]), "train": float(request.form["train"]),
                        "gap": int(request.form["gap"]), "wf": request.form.get("wf") == "on"}
                if not (0 <= form["cost_bp"] <= 100 and 0.5 <= form["train"] <= 0.85 and 1 <= form["gap"] <= 20):
                    raise ValueError
            except (KeyError, ValueError):
                flash("Settings out of range: cost 0-100 bp, in-sample share 0.50-0.85, gap 1-20 sessions.")
                return redirect(url_for("patterns", mode=mode))
            res, notes, total = engine.run_patterns(ctx, s, register=True, cost=form["cost_bp"] / 1e4,
                                                    train_fraction=form["train"], gap=form["gap"],
                                                    walk_forward=form["wf"])
            state.lab[mode] = {"res": res, "notes": notes, "total": total, "form": form}
            return redirect(url_for("patterns", mode=mode))
        lab = state.lab.get(mode)
        if lab:
            form = lab["form"]
        ctxt = {"form": form, "lab": lab, "rules": rule_library(), "verdict_text": verdict_text,
                "t_guide": T_STAT_GUIDE}
        if lab and not lab["res"].empty:
            res = lab["res"]
            sel = [i for i in request.args.getlist("index") if i in set(res["index"])] or sorted(res["index"].unique())
            show = res[res["index"].isin(sel)]
            n_fam = res[["rule_id", "family"]].drop_duplicates().shape[0]
            s_idx = request.args.get("s_index", show["index"].iloc[0])
            opts = show[show["index"] == s_idx] if s_idx in set(show["index"]) else show[show["index"] == show["index"].iloc[0]]
            s_idx = opts["index"].iloc[0]
            s_rule = request.args.get("s_rule", opts["rule_id"].iloc[0])
            s_rule = s_rule if s_rule in set(opts["rule_id"]) else opts["rule_id"].iloc[0]
            stab = engine.run_stability(ctx, s, res, s_idx, s_rule, cost=lab["form"]["cost_bp"] / 1e4)
            stab_chart = None
            if len(stab):
                st = stab.set_index("date")
                stab_chart = charts.line({"rolling t-statistic": st["t"], "+2": pd.Series(2.0, index=st.index),
                                          "-2": pd.Series(-2.0, index=st.index)},
                                         "Rolling t-statistic (~2-year windows)",
                                         colors=[charts.ACCENT, charts.MUTED, charts.MUTED], dashed=("+2", "-2"))
            ctxt.update(show=show, sel=sel, all_idx=sorted(res["index"].unique()), n_fam=n_fam,
                        lucky=0.05 * n_fam, counts=res["verdict"].value_counts().to_dict(),
                        families=family_summary(show), s_idx=s_idx, s_rule=s_rule, opts=opts,
                        train_end=opts.set_index("rule_id").loc[s_rule, "train_end"], stab_chart=stab_chart)
        return page("patterns.html", _mode=mode, ctx=ctx, **ctxt)

    @app.route("/movers")
    def movers():
        from ..analysis.scanner import SCAN_NOTE
        mode = current_mode()
        ctx, s = ctx_for(mode)
        scan = cached(ctx, s, "scan", lambda: engine.run_scan(ctx, s))
        sf = cached(ctx, s, "stock_fc", lambda: engine.run_stock_forecasts(ctx, s, scan))
        tab = request.args.get("tab", "gainers")
        tabs = [("gainers", "▲ Gainers"), ("losers", "▼ Losers"), ("unusual", "⚡ Unusual"), ("volatile", "🌪 Most volatile")]
        tab = tab if tab in dict(tabs) else "gainers"
        rows = scan["lists"][tab] if scan and scan.get("asof") is not None else None
        exp = sf["tomorrow"] if sf and "tomorrow" in sf else None
        return page("movers.html", _mode=mode, ctx=ctx, scan=scan, rows=rows, tab=tab, tabs=tabs, note=SCAN_NOTE,
                    cfg=s.scanner, sf=sf, exp=exp)

    @app.route("/forecast")
    def forecast():
        from ..analysis.forecast import FORECAST_NOTE
        mode = current_mode()
        ctx, s = ctx_for(mode)
        fc = cached(ctx, s, "forecast", lambda: engine.run_forecasts(ctx, s))
        names = list(fc)
        name = request.args.get("index", names[0])
        name = name if name in names else names[0]
        f = fc[name]
        hist = f["size"]["history"].tail(500)
        size_chart = charts.line({"Actual |move| %": hist["y"] * 100, "Forecast %": hist["pred"] * 100,
                                  "Naive 20-day avg %": hist["base"] * 100},
                                 "Move size: forecast vs what actually happened (last 500 sessions, out-of-sample)",
                                 unit="pct", colors=[charts.MUTED, charts.ACCENT, charts.VIOLET], dashed=("Naive 20-day avg %",))
        size_chart["datasets"][0]["borderWidth"] = 1
        dh = f["direction"]["history"]
        roll = ((dh["p"] > 0.5) == (dh["y"] == 1)).astype(float).rolling(250).mean() * 100
        base_roll = ((dh["base"] > 0.5) == (dh["y"] == 1)).astype(float).rolling(250).mean() * 100
        dir_chart = charts.line({"Model hit rate %": roll, "Baseline hit rate %": base_roll, "Coin flip": roll * 0 + 50},
                                "Direction: rolling 1-year hit rate (out-of-sample)", colors=[charts.ACCENT, charts.VIOLET,
                                charts.MUTED], dashed=("Coin flip",), y_min=35, y_max=65)
        return page("forecast.html", _mode=mode, ctx=ctx, fc=fc, names=names, name=name, f=f, size_chart=size_chart,
                    dir_chart=dir_chart, note=FORECAST_NOTE)

    @app.route("/updates", methods=["GET", "POST"])
    def updates():
        from ..data import db as dbm
        from ..notify import dispatch
        from ..notify.bot import heartbeat_file
        from ..notify.telegram import TelegramClient, TelegramError, load_secret
        mode = current_mode()
        ctx, s = ctx_for(mode)
        secret = load_secret(paths)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "test":
                if not secret:
                    flash("Telegram is not set up yet: run 'imr telegram setup' in a terminal.")
                else:
                    try:
                        client = TelegramClient(secret.token)
                        for c in secret.chat_ids:
                            client.send(c, "🔔 Test message from the IndexLab dashboard.")
                        flash("Test message sent.")
                    except TelegramError as exc:
                        flash(f"Sending failed: {exc}")
            elif action == "send_now":
                res = dispatch.after_update(paths, s, demo=(mode == "demo"))
                flash("Updates processed: " + (", ".join(f"{k} → {st}" for k, st in res) or "nothing due"))
            return redirect(url_for("updates", mode=mode))
        with dbm.connect(ctx.db_path) as conn:
            notes = dispatch.read_notifications(conn)
        hb = heartbeat_file(paths)
        return page("updates.html", _mode=mode, ctx=ctx, notes=notes, secret=secret, tg=s.telegram,
                    heartbeat=hb.read_text().strip() if hb.exists() else None)

    @app.route("/flows")
    def flows():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        res = cached(ctx, s, "flows", lambda: engine.run_flows(ctx))
        daily = cum = None
        table = []
        if res is not None:
            t = res["table"]
            daily = charts.bars({"FII net": t["fii_net"].tail(250), "DII net": t["dii_net"].tail(250)},
                                "Daily net flows, Rs crore (last 250 days)", unit="crore",
                                colors=[charts.DOWN, charts.ACCENT])
            nifty = ctx.indices.get("Nifty 50")
            y2 = {"Nifty 50": nifty["Close"].reindex(t.index)} if nifty is not None else None
            cum = charts.line({"FII cumulative": t["fii_cum"], "DII cumulative": t["dii_cum"]},
                              "Cumulative net flows (Rs crore) and Nifty 50 (dashed, right axis)", unit="crore",
                              colors=[charts.DOWN, charts.ACCENT], y2=y2, y2_title="Nifty 50")
            for d, r in t.tail(20).iloc[::-1].iterrows():
                table.append({"date": d, **r.to_dict(), "source": ctx.flows["source"].get(d, "")})
        return page("flows.html", _mode=mode, ctx=ctx, lines=flow_lines(res), daily=daily, cum=cum, table=table,
                    min_hist=s.min_flow_history)

    @app.route("/report", methods=["POST"])
    def report():
        mode = current_mode()
        ctx, s = ctx_for(mode)
        from ..report.html import write_report
        out = write_report(ctx, s, paths)
        flash(f"Report saved: {out}")
        return redirect(url_for("today", mode=mode))

    @app.route("/reports/<path:name>")
    def report_file(name):
        f = (paths.reports_dir / name).resolve()
        if paths.reports_dir.resolve() not in f.parents or not f.exists():
            abort(404)
        return f.read_text(encoding="utf-8")

    return app


def serve(port: int = 8501, open_browser: bool = True) -> None:
    import logging
    import webbrowser

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app = create_app()
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard running at {url}  (press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
