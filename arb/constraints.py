"""Every entry constraint, against its current value, per instrument.

Built for the dashboard's /constraints page, on the HTTP thread and only when
someone is looking at it. It never runs the expensive parts of the decision
(structure analysis, the confluence evaluation): those are read from what the
engine last computed, with their age, so an open browser tab costs the trading
loop nothing it would notice. What IS computed here is the cheap per-strike
arithmetic, done exactly the way ContractSelector.select and plan_trade do it,
so a row that passes here is a row the engine would let through.
"""
import json
import os
import time

from arb.costs import leg_cost
from arb.instruments import market_phase, now_ist
from arb.intraday import atm_index, chain_delta, structural_invalidation
from arb.regime import atr, atr_ratio, session_phase
from arb.smc import BEAR, BULL

MAX_AGE_MS = 400.0          # what hunt() passes to ContractSelector.select
MIN_DEPTH_MULT = 1.0


def _cell(value, ok=None, note=""):
    # Comparisons on the quote store's numpy arrays yield numpy bools and
    # numbers, which the JSON encoder refuses: plain Python types only.
    if hasattr(value, "item"):
        value = value.item()
    return {"v": value, "ok": None if ok is None else bool(ok), "note": note}


def _ago(ts, now):
    if not ts:
        return "never"
    s = now - ts
    return f"{s:.0f}s ago" if s < 120 else f"{s / 60:.0f}m ago"


def common(e, cfg, L=None):
    """The gates every instrument shares: time, limits, cooldowns, halts.
    With no engine (no session running) the thresholds are still shown, from
    config, with nothing to measure them against."""
    if e is None:
        return [{"param": p, "threshold": t, "current": "— (no session)", "ok": None}
                for p, t in _common_thresholds(L, cfg)]
    L, now = e.L, time.time()
    n = now_ist()
    since_open = (n - n.replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() / 60.0
    to_close = e.book.time_to_close(cfg.session_end)
    since_trade = now - e.last_trade_ts if e.last_trade_ts else None
    since_loss = now - e.last_loss_ts if e.last_loss_ts else None
    cap = e.capital.capital
    day_pct = 100.0 * e.realised_pnl / cap if cap else 0.0
    paused = max(0.0, e.paused_until - now)
    loss_cd = float(getattr(L, "cooldown_after_loss_sec", 0.0))
    rows = [
        ("warmup_minutes", f"≥ {L.warmup_minutes:g} min after 09:15",
         f"{since_open:.0f} min", since_open >= L.warmup_minutes),
        ("no_new_trades_minutes", f"> {L.no_new_trades_minutes:g} min before {cfg.session_end}",
         f"{to_close:.0f} min left", to_close > L.no_new_trades_minutes),
        ("max_open_positions", f"< {L.max_open_positions}", e.book.count(),
         e.book.count() < L.max_open_positions),
        ("max_trades_per_day", f"< {L.max_trades_per_day}", e.trades_today,
         e.trades_today < L.max_trades_per_day),
        ("trade_cooldown_sec", f"≥ {L.trade_cooldown_sec:g} s since last trade",
         "no trade yet" if since_trade is None else f"{since_trade:.0f} s",
         since_trade is None or since_trade >= L.trade_cooldown_sec),
        ("cooldown_after_loss_sec", f"≥ {loss_cd:g} s since a loss",
         "no loss yet" if since_loss is None else f"{since_loss:.0f} s",
         since_loss is None or since_loss >= loss_cd),
        ("max_daily_loss_pct", f"day loss < {L.max_daily_loss_pct:g}%",
         f"{day_pct:+.2f}%" + (f" · HALTED: {e.halt_reason}" if e.halted else ""), not e.halted),
        ("loss_streak_pause_min", f"{getattr(L, 'loss_streak_pause_min', 30.0):g} min pause after "
                                  f"{int(getattr(L, 'max_consecutive_losses', 0) or 0) or '∞'} losses in a row",
         f"paused {paused / 60:.0f} min more" if paused else "not paused", paused <= 0),
        ("max_risk_pct_per_trade", f"{L.max_risk_pct_per_trade:g}% of capital",
         f"₹{cap * L.max_risk_pct_per_trade / 100.0:,.0f} per trade", None),
    ]
    if e.careful:
        rows.append(("grade_after_streak", f"grade ≥ {getattr(L, 'grade_after_streak', 'B')}",
                     "careful mode on", None))
    return [{"param": p, "threshold": t, "current": c, "ok": ok} for p, t, c, ok in rows]


def _common_thresholds(L, cfg):
    """(param, threshold) for every shared gate - the config side of common()."""
    loss_cd = float(getattr(L, "cooldown_after_loss_sec", 0.0))
    return [
        ("warmup_minutes", f"≥ {L.warmup_minutes:g} min after 09:15"),
        ("no_new_trades_minutes", f"> {L.no_new_trades_minutes:g} min before {cfg.session_end}"),
        ("max_open_positions", f"< {L.max_open_positions}"),
        ("max_trades_per_day", f"< {L.max_trades_per_day}"),
        ("trade_cooldown_sec", f"≥ {L.trade_cooldown_sec:g} s since last trade"),
        ("cooldown_after_loss_sec", f"≥ {loss_cd:g} s since a loss"),
        ("max_daily_loss_pct", f"day loss < {L.max_daily_loss_pct:g}%"),
        ("loss_streak_pause_min", f"{getattr(L, 'loss_streak_pause_min', 30.0):g} min pause after "
                                  f"{int(getattr(L, 'max_consecutive_losses', 0) or 0) or '∞'} losses in a row"),
        ("max_risk_pct_per_trade", f"{L.max_risk_pct_per_trade:g}% of capital"),
        ("symbol_cooldown_sec", f"{L.symbol_cooldown_sec:g} s per contract"),
        ("min_structure_bars", f"≥ {L.min_structure_bars} candles"),
        ("min_confirmations", f"≥ {L.min_confirmations}"),
        ("require_order_block", "required" if L.require_order_block else "off"),
        ("min_delta", f"≥ {L.min_delta:g}"),
        ("max_spread_points", f"≤ {L.max_spread_points:g} pts"),
        ("min_stop_atr", f"≥ {L.min_stop_atr:g} ATR"),
        ("min_net_points", f"≥ {L.min_net_points:g} pts · stocks ≥ "
                           f"₹{getattr(L, 'stocks_min_net_inr', 100.0):g}"),
        ("max_cost_frac_of_risk", f"≤ {L.max_cost_frac_of_risk:g}"),
        ("reward_risk", f"1:{L.reward_risk:g}"),
    ]


def _minutes(hhmm):
    hh, mm = (int(x) for x in str(hhmm).split(":"))
    return hh * 60 + mm


def _need(L, name, n=None):
    """The ATR ratio regime_gate demands of `name` right now, and the rule that
    set it: a per-index [first half, second half] pace from atr_by_index, or
    min_atr_ratio raised by the midday rule for an index not listed there."""
    n = n or now_ist()
    halves = (getattr(L, "atr_by_index", None) or {}).get(name) if name else None
    if halves:
        second = n.hour * 60 + n.minute >= _minutes(getattr(L, "atr_half_split", "12:15"))
        return float(halves[1 if second else 0]), \
            f"{'after' if second else 'before'} {getattr(L, 'atr_half_split', '12:15')}", False
    need = float(L.min_atr_ratio)
    mode = str(getattr(L, "midday_mode", "strict"))
    if getattr(L, "skip_midday", False):
        mode = "block"
    midday = mode != "off" and session_phase(now=n) == "midday"
    if midday and mode == "block":
        return need, "midday blocked", True
    if midday:
        return float(getattr(L, "midday_min_atr_ratio", 1.0)), "midday", False
    return need, "min_atr_ratio", False


def _pace(L, rows, name=None):
    """The regime gate's volatility test, as it would judge these bars now."""
    need, why, blocked = _need(L, name)
    if len(rows) < L.atr_period + 1:
        return _cell("warming up", False)
    ar = atr_ratio(rows, period=L.atr_period, baseline_bars=L.atr_baseline_bars)
    if blocked:
        return _cell(f"{ar:.2f}x", False, why)
    return _cell(f"{ar:.2f}x", ar >= need, f"need {need:g}x ({why})")


def _session_rows(rows, end):
    """Bars inside the session only. The candle cache carries flat bars printed
    after the close; judged on those, every index looks dead after hours."""
    cut = _minutes(end)
    return [r for r in rows if _minutes(str(r[0])[11:16]) <= cut] if rows else []


def volatility(L, names, rows_for, end):
    """One row per index (plus the stock desk's shared pace): the pace each is
    held to - its own first-half / second-half ATR ratio - against its current
    reading. Works without a session, from the last cached candles."""
    n = now_ist()
    table = (getattr(L, "atr_by_index", None) or {})
    out = []
    for name in list(names) + ["STOCKS"]:
        halves = table.get(name)
        need, why, blocked = _need(L, name, n)
        row = {"index": name,
               "first": f"{halves[0]:g}x" if halves else "—",
               "second": f"{halves[1]:g}x" if halves else "—",
               "rule": "atr_by_index" if halves else
                       f"min_atr_ratio {L.min_atr_ratio:g}x · midday "
                       f"{getattr(L, 'midday_min_atr_ratio', 1.0):g}x",
               "need": f"{need:g}x ({why})"}
        rows = _session_rows(rows_for(name), end) if name != "STOCKS" else []
        if name == "STOCKS":
            row.update(atr="—", ratio="per stock", bars="—", last="—", ok=None)
        elif len(rows) < L.atr_period + 1:
            row.update(atr="—", ratio="no candles" if not rows else "warming up",
                       bars=len(rows), last=str(rows[-1][0])[:16].replace("T", " ") if rows else "—",
                       ok=None)
        else:
            ar = atr_ratio(rows, period=L.atr_period, baseline_bars=L.atr_baseline_bars)
            row.update(atr=f"{atr(rows, period=L.atr_period):.2f} pts",
                       ratio=f"{ar:.2f}x", bars=len(rows),
                       last=str(rows[-1][0])[:16].replace("T", " "),
                       ok=(not blocked) and ar >= need)
        out.append(row)
    return out


def _judgement(e, name, now):
    """Setup, confirmations and freshness from the engine's last evaluation."""
    L = e.L
    v = e.verdicts.get(name)
    seen = e.evaluated_at.get(name, 0.0)
    judged = e.verdict_at.get(name, 0.0)
    if v is None or judged < seen - 1.0:
        # Looked at since, but stopped before the setup read (regime, bars...).
        why = "not reached (stopped at an earlier check)" if seen else "not evaluated yet"
        return None, _cell("—", None, why), _cell("—", None, why), _ago(seen, now)
    setup = (_cell(" + ".join(v.plays()), True) if v.tradable
             else _cell(v.veto or "no setup", False))
    conf = _cell(v.count, v.count >= L.min_confirmations)
    return v, setup, conf, _ago(judged, now)


def columns(L):
    """Header per column: what it checks and the threshold configured for it."""
    return [
        ("instrument", "Instrument", ""),
        ("evaluated", "Last evaluated", ""),
        ("pace", "Pace (ATR ratio)", "per index - see the table above"),
        ("bars", "Candles", f"≥ {L.min_structure_bars}"),
        ("setup", "Setup", " / ".join(getattr(L, "playbooks", []) or ["any"])),
        ("conf", "Confirmations", f"≥ {L.min_confirmations}"),
        ("zone", "Buying a pullback", "required" if L.require_order_block else "off"),
        ("uses", "Same setup today", f"< {int(getattr(L, 'max_per_playbook_per_day', 0) or 0) or '∞'}"),
        ("delta", "Delta", f"≥ {L.min_delta:g}"),
        ("spread", "Spread", f"≤ {L.max_spread_points:g} pts"),
        ("quote", "Quote", f"≤ {MAX_AGE_MS:g} ms · depth ≥ 1 lot"),
        ("lot", "Lot cost", "≤ free capital"),
        ("risk", "Risk if stopped", f"≤ {L.max_risk_pct_per_trade:g}% of capital"),
        ("stop", "Stop width", f"≥ {L.min_stop_atr:g} ATR"),
        ("net", "Profit after costs", f"≥ {L.min_net_points:g} pts · stocks ≥ "
                                      f"₹{getattr(L, 'stocks_min_net_inr', 100.0):g}"),
        ("costfrac", "Costs / risk", f"≤ {L.max_cost_frac_of_risk:g}"),
        ("cooldown", "Contract cooldown", f"{L.symbol_cooldown_sec:g} s"),
    ]


def _index_rows(e, und, now):
    L = e.L
    b = e._binding_for(und)
    if b is None:
        return []
    rows = e.candles.get(und, limit=400) if e.candles else []
    st = e.structures.get(und)
    v, setup, conf, when = _judgement(e, und, now)
    side = v.side if v is not None and v.side else (st.bias if st is not None and st.bias else 0)
    opt = "PE" if side == BEAR else "CE"
    direction = {BULL: "bullish", BEAR: "bearish"}.get(side, "no direction yet")
    spot = e._spot(und) or (rows[-1][4] if rows else 0.0)
    cv = b.view(e.feed.store, time.perf_counter_ns(), now)
    strikes = list(cv.strikes)
    if not strikes or spot <= 0:
        return []
    a = atm_index(strikes, spot)
    lo, hi = max(0, a - L.max_strike_offset), min(len(strikes) - 1, a + L.max_strike_offset)
    ask = cv.ce_ask if opt == "CE" else cv.pe_ask
    bid = cv.ce_bid if opt == "CE" else cv.pe_bid
    askq = cv.ce_askq if opt == "CE" else cv.pe_askq
    age = cv.ce_age if opt == "CE" else cv.pe_age
    syms = cv.ce_symbol if opt == "CE" else cv.pe_symbol

    pace = _pace(L, rows, und)
    bars = _cell(len(rows), len(rows) >= L.min_structure_bars)
    lead = v.setups[0] if v is not None and v.setups else None
    zone = _cell("—", None, "needs a direction")
    uses = _cell("—", None)
    index_risk, a_idx = 0.0, atr(rows[:-1], period=L.atr_period) if rows else 0.0
    if side and st is not None:
        inzone = st.active_zone(spot, side) is not None or bool(v is not None and v.setups)
        zone = _cell("yes" if inzone else "no", inzone or not L.require_order_block)
        inv = structural_invalidation(st, side, lead.invalidation if lead else None)
        ref = st.closes[-1] if st.closes else spot
        index_risk = abs(ref - inv) if inv else 0.0
    if lead is not None:
        cap = int(getattr(L, "max_per_playbook_per_day", 0) or 0)
        n = e.playbook_count.get((und, lead.name, side), 0)
        uses = _cell(n, not cap or n < cap)

    cap_inr = e.capital.capital * L.max_risk_pct_per_trade / 100.0
    free = e.capital.capital - e.book.deployed()
    out = []
    for i in range(lo, hi + 1):
        px, bd = float(ask[i]), float(bid[i])
        name = f"{und} {strikes[i]:g} {opt}"
        off = i - a
        row = {"instrument": _cell(name, None, f"{'ATM' if off == 0 else f'ATM{off:+d}'} · {direction}"),
               "group": und, "evaluated": _cell(when), "pace": pace, "bars": bars,
               "setup": setup, "conf": conf, "zone": zone, "uses": uses}
        if px <= 0 or bd <= 0:
            row.update({k: _cell("no quote", False) for k in
                        ("delta", "spread", "quote", "lot", "risk", "stop", "net", "costfrac")})
        else:
            d = chain_delta(ask, strikes, i, opt)
            spread = px - bd
            lot = cv.lot_size
            row["delta"] = _cell(f"{d:.2f}", d >= L.min_delta)
            row["spread"] = _cell(f"{spread:.2f}", spread <= L.max_spread_points)
            row["quote"] = _cell(f"{float(age[i]):.0f} ms · {float(askq[i]) / lot:.1f} lots",
                                 float(age[i]) <= MAX_AGE_MS and float(askq[i]) >= lot * MIN_DEPTH_MULT)
            row["lot"] = _cell(f"₹{px * lot:,.0f}", px * lot <= free)
            cost = e._round_trip_points(px, lot)
            if index_risk > 0:
                # plan_trade's premium risk, to the letter.
                prem = max(index_risk * d, spread * 2.0)
                prem = min(prem, px * L.max_risk_frac_of_premium)
                row["risk"] = _cell(f"₹{prem * lot:,.0f}", prem * lot <= cap_inr * 1.05,
                                    f"cap ₹{cap_inr:,.0f}")
                row["stop"] = (_cell(f"{index_risk / a_idx:.2f} ATR", index_risk >= L.min_stop_atr * a_idx)
                               if a_idx > 0 else _cell("no ATR yet", None))
                net = L.reward_risk * prem - cost
                row["net"] = _cell(f"{net:.1f} pts", net >= L.min_net_points, f"at 1:{L.reward_risk:g}")
                row["costfrac"] = _cell(f"{cost / prem:.2f}" if prem > 0 else "—",
                                        prem > 0 and cost <= L.max_cost_frac_of_risk * prem)
            else:
                why = "needs a direction and a stop level"
                row.update({k: _cell("—", None, why) for k in ("risk", "stop", "net", "costfrac")})
        left = L.symbol_cooldown_sec - (now - e._symbol_cooldown.get(syms[i], -1e9))
        row["cooldown"] = _cell(f"{left:.0f} s left" if left > 0 else "clear", left <= 0)
        out.append(row)
    return out


def _stock_rows(e, now):
    L = e.L
    desk = e.stocks
    if desk is None:
        return []
    store = e.feed.store
    cap_inr = e.capital.capital * L.max_risk_pct_per_trade / 100.0
    lev = float(getattr(L, "stocks_leverage", 5.0))
    free = e.capital.capital - e.book.deployed()
    na = _cell("n/a", None, "options only")
    out = []
    for pick in list(desk.active):
        sym = pick.symbol
        slot = desk.slots.get(sym)
        if slot is None:
            continue
        rows = e.candles.get(sym, limit=400) if e.candles else []
        v, setup, conf, when = _judgement(e, sym, now)
        side = v.side if v is not None else 0
        row = {"instrument": _cell(f"{sym} (stock)", None,
                                   {BULL: "long", BEAR: "short"}.get(side, "no direction yet")),
               "group": "Stocks in play", "evaluated": _cell(when),
               "pace": _pace(L, rows, "STOCKS"), "bars": _cell(len(rows), len(rows) >= L.min_structure_bars),
               "setup": setup, "conf": conf, "zone": na, "uses": na, "delta": na}
        bd, ak = float(store.bid[slot]), float(store.ask[slot])
        row["spread"] = _cell(f"₹{ak - bd:.2f}" if bd > 0 and ak > 0 else "no quote", None,
                              "not a stock check")
        row["quote"] = na
        row["lot"] = na
        st = e.stock_structures.get(sym)
        risk = 0.0
        if side and st is not None and bd > 0 and ak > 0:
            lead = v.setups[0] if v.setups else None
            inv = structural_invalidation(st, side, lead.invalidation if lead else None)
            ref = st.closes[-1] if st.closes else float(store.ltp[slot])
            risk = abs(ref - inv) if inv else 0.0
        if risk > 0:
            fill = ak if side > 0 else bd
            qty = min(int(cap_inr // risk), int(max(0.0, free) * lev // fill))
            a1 = atr(rows[:-1], period=L.atr_period)
            row["stop"] = (_cell(f"{risk / a1:.2f} ATR", risk >= L.min_stop_atr * a1)
                           if a1 > 0 else _cell("no ATR yet", None))
            if qty > 0:
                rt = (leg_cost(fill, qty, "BUY", "EQ", e.cm) + leg_cost(fill, qty, "SELL", "EQ", e.cm)
                      + 2 * e.cm.slippage_points() * qty + (ak - bd) * qty)
                row["risk"] = _cell(f"₹{risk * qty:,.0f} · {qty} sh", risk * qty <= cap_inr * 1.05,
                                    f"cap ₹{cap_inr:,.0f}")
                net = L.reward_risk * risk * qty - rt
                need = float(getattr(L, "stocks_min_net_inr", 100.0))
                row["net"] = _cell(f"₹{net:,.0f}", net >= need, f"est. at 1:{L.reward_risk:g}")
                row["costfrac"] = _cell(f"{rt / (risk * qty):.2f}",
                                        rt <= L.max_cost_frac_of_risk * risk * qty)
            else:
                row.update({"risk": _cell("too large", False), "net": _cell("—"), "costfrac": _cell("—")})
        else:
            why = "only computed once a setup is found"
            row.update({k: _cell("—", None, why) for k in ("risk", "stop", "net", "costfrac")})
        left = L.symbol_cooldown_sec - (now - e._symbol_cooldown.get(
            (desk.universe.get(sym) or {}).get("eq_symbol", sym), -1e9))
        row["cooldown"] = _cell(f"{left:.0f} s left" if left > 0 else "clear", left <= 0)
        out.append(row)
    return out


def snapshot(e, cfg):
    now = time.time()
    rows = []
    names = sorted({b.underlying for b in e.bindings})
    for und in names:
        rows.extend(_index_rows(e, und, now))
    rows.extend(_stock_rows(e, now))
    get = (lambda n: e.candles.get(n, limit=400)) if e.candles else (lambda n: [])
    return {"now": now_ist().strftime("%H:%M:%S"),
            "phase": market_phase(start=cfg.session_start, end=cfg.session_end),
            "volatility": volatility(e.L, names, get, cfg.session_end),
            "common": common(e, cfg),
            "columns": [{"key": k, "label": l, "threshold": t} for k, l, t in columns(e.L)],
            "rows": rows}


def offline(cfg, L, saved=None, cache_dir=None):
    """Everything the page can show with no session running: thresholds from
    config, each index's pace from its cached candles, and the per-instrument
    table as the last session left it (`saved`, with the time it was taken)."""
    cache_dir = cache_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "candles")
    interval = getattr(L, "candle_interval", "1m")

    def cached(name):
        try:
            with open(os.path.join(cache_dir, f"{name}_{interval}.json")) as fh:
                return json.load(fh)[-400:]
        except (OSError, ValueError):
            return []

    names = sorted(u.name for u in cfg.active_underlyings())
    saved = saved or {}
    return {"now": now_ist().strftime("%H:%M:%S"), "waiting": True,
            "session_start": cfg.session_start,
            "phase": market_phase(start=cfg.session_start, end=cfg.session_end),
            "volatility": volatility(L, names, cached, cfg.session_end),
            "common": common(None, cfg, L),
            "columns": saved.get("columns") or
                       [{"key": k, "label": l, "threshold": t} for k, l, t in columns(L)],
            "rows": saved.get("rows") or [],
            "saved_at": saved.get("saved_at", "")}
