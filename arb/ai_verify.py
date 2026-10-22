"""AI cross-check for the last-half-hour momentum entry (owner, 22 Oct 2026).

At lhm_entry_at the rule (IntradayEngine._lhm_try) picks CALL or PUT. With
lhm_ai_verify = "on" the order goes out only when an LLM - Gemini on Vertex AI
through services/vertex_ai_mime.py - shown everything the engine knows at that
moment, independently calls the SAME side. "shadow" asks and records but never
blocks; "off" never asks.

What the model sees (lhm_context): the rule's own numbers and the contract it
chose; the index's bars (today in 5-minute bars, the last 30 one-minute bars,
the prior sessions' daily ranges, 4-hour bars) with EMA / RSI / ATR, the
opening range and the day's range position; market structure (arb/smc.py),
the playbook verdict (arb/confluence.py), pace (arb/regime.py); the option
chain off the live websocket book - bid / ask / LTP / OI / volume per strike -
with PCR, max pain and the OI walls; the near future's price, basis and OI;
the other indices; and from Angel's market-data API (angel_market_data, fetched
about 90 s before the entry): Greeks and IV per strike, the exchange's PCR,
OI build-up. Greeks fall back to a Black-Scholes estimate when Angel's are
missing.

Fail closed in "on": no answer inside lhm_ai_timeout_sec, an error, an
unparsable answer, NO_TRADE or a different side all mean no trade.

Every question and answer is journaled (journal/<mode>/ai_lhm_YYYY-MM-DD.jsonl)
so the filter can be judged on its own record - whether it vetoes the losers
more often than the winners. Nothing yet says it does: the rule's backtest
(84 trades, t 2.4) is evidence for the rule, not for the filter.
"""
import json
import math
import os
import threading
import time
from arb.instruments import now_ist, years_to_expiry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SYSTEM = """You are an independent risk reviewer for ONE intraday index-option trade on the Indian market (NSE/BSE).
A rule-based strategy ("last-half-hour momentum") wants to BUY one option at the time given and hold it until the
square-off time given (about 14 minutes), unless its index stop is hit first. It buys a CALL when it expects the index
to rise into the close, a PUT when it expects it to fall.

Decide, from the data only, which side is more likely to make money over that window AFTER costs:
  CALL      - the index rises enough for a bought call to profit
  PUT       - the index falls enough for a bought put to profit
  NO_TRADE  - no clear edge, or the risk is too high

Decide independently - do not simply agree with the strategy. Weigh at least:
  * trend and momentum into the close (1-min, 5-min and 4-hour bars, EMAs, RSI, structure) vs. exhaustion after a long run
  * how far the index has already moved today (move vs. average daily range) and where it sits in the day's range
  * option-chain positioning: OI walls (highest call OI = resistance, highest put OI = support) near spot, PCR, max pain,
    OI build-up (long build-up, short build-up, short covering, long unwinding) on the index future
  * implied volatility and Greeks: theta decay over ~14 minutes, gamma, the bid-ask spread of the chosen contract
  * futures basis, the other indices (breadth), expiry-day effects
  * the stop distance vs. the expected move: a trade whose stop is likely to be hit by normal noise is a NO_TRADE

Use only the data given. Say what is missing in data_gaps. Times are IST. Be concise and specific in reasoning, citing numbers."""

SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["CALL", "PUT", "NO_TRADE"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasoning": {"type": "string"},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["side", "confidence", "reasoning", "key_factors", "risks"],
}

OI_BUILDUP_TYPES = ("Long Built Up", "Short Built Up", "Short Covering", "Long Unwinding")


# ── small numeric helpers ─────────────────────────────────────────────────────
def _num(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _ema(values, n):
    if not values:
        return None
    k, e = 2.0 / (n + 1), float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1 - k)
    return e


def _rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = float(b) - float(a)
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _fold(rows, minutes):
    """1-minute rows [ts, o, h, l, c, ...] folded into `minutes` bars from 09:15."""
    out, cur = [], None
    for r in rows:
        ts = str(r[0])
        m = (int(ts[11:13]) * 60 + int(ts[14:16]) - 555) // minutes
        key = (ts[:10], m)
        if cur is None or cur[0] != key:
            if cur:
                out.append(cur[1])
            hh, mm = divmod(555 + m * minutes, 60)
            cur = (key, [f"{ts[:10]} {hh:02d}:{mm:02d}", float(r[1]), float(r[2]), float(r[3]), float(r[4])])
        else:
            b = cur[1]
            b[2], b[3], b[4] = max(b[2], float(r[2])), min(b[3], float(r[3])), float(r[4])
    if cur:
        out.append(cur[1])
    return [[b[0]] + [round(x, 2) for x in b[1:]] for b in out]


# ── Black-Scholes fallback Greeks (data for the model only, never for trading) ──
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(S, K, T, price, opt, r=0.065):
    """IV (bisection) and delta / gamma / theta per day / vega per 1 vol point
    from a mid price. None when the price is below intrinsic or inputs are bad."""
    if not (S > 0 and K > 0 and T > 0 and price and price > 0):
        return None
    intrinsic = max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    if price <= intrinsic + 1e-6:
        return None

    def px(sig):
        v = sig * math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / v
        d2 = d1 - v
        if opt == "CE":
            return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
    lo, hi = 0.005, 3.0
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if px(mid) > price:
            hi = mid
        else:
            lo = mid
    sig = 0.5 * (lo + hi)
    v = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / v
    d2 = d1 - v
    delta = _ncdf(d1) if opt == "CE" else _ncdf(d1) - 1.0
    gamma = _npdf(d1) / (S * v)
    vega = S * _npdf(d1) * math.sqrt(T) / 100.0
    if opt == "CE":
        theta = -S * _npdf(d1) * sig / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)
    else:
        theta = -S * _npdf(d1) * sig / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)
    return {"iv_pct": round(100 * sig, 2), "delta": round(delta, 4), "gamma": round(gamma, 6),
            "theta_per_day": round(theta / 365.0, 3), "vega_per_vol_pt": round(vega, 3)}


# ── Angel One market-data API ─────────────────────────────────────────────────
def angel_market_data(api, und, expiry, log=print, gap=1.1):
    """Greeks per strike, the exchange's PCR and OI build-up from Angel's
    market-data endpoints (rest/secure/angelbroking/marketData/v1/...). Calls
    are paced `gap` seconds apart: they share the API key's budget with the
    engine. Each failure is recorded, never raised."""
    out = {"fetched_at": now_ist().strftime("%H:%M:%S"), "errors": {}}
    if api is None:
        out["errors"]["all"] = "no Angel session (replay or not logged in)"
        return out

    def call(name, fn):
        try:
            r = fn()
        except Exception as e:
            out["errors"][name] = f"{type(e).__name__}: {str(e)[:160]}"
            return None
        finally:
            time.sleep(gap)
        if not isinstance(r, dict) or not r.get("status", True) or r.get("data") is None:
            out["errors"][name] = str((r or {}).get("message") if isinstance(r, dict) else r)[:160]
            return None
        return r.get("data")

    rows = call("option_greeks", lambda: api.optionGreek({"name": und, "expirydate": expiry}))
    if rows:
        out["greeks"] = {
            (_num(g.get("strikePrice"), 1), str(g.get("optionType", "")).upper()): {
                "delta": _num(g.get("delta"), 4), "gamma": _num(g.get("gamma"), 6),
                "theta": _num(g.get("theta"), 3), "vega": _num(g.get("vega"), 3),
                "iv_pct": _num(g.get("impliedVolatility"), 2),
                "trade_volume": _num(g.get("tradeVolume"), 0)}
            for g in rows if isinstance(g, dict)}
    pcr = call("put_call_ratio", lambda: api.putCallRatio())
    if pcr:
        mine = [p for p in pcr if isinstance(p, dict) and _is_index_symbol(p.get("tradingSymbol"), und)]
        out["exchange_pcr"] = [{"symbol": p.get("tradingSymbol"), "pcr": _num(p.get("pcr"), 3)} for p in mine]
    build = {}
    for kind in OI_BUILDUP_TYPES:
        data = call(f"oi_buildup:{kind}", lambda k=kind: api.oIBuildup({"expirytype": "NEAR", "datatype": k}))
        if data:
            rows_k = [d for d in data if isinstance(d, dict)]
            build[kind] = {
                "index_future": [_buildup_row(d) for d in rows_k
                                 if _is_index_symbol(d.get("tradingSymbol"), und)],
                "count": len(rows_k),
                "top5": [_buildup_row(d) for d in rows_k[:5]],
            }
    if build:
        out["oi_buildup_near"] = build
    return out


def merge_angel(ctx, angel):
    """Angel's market data into a snapshot built without it: its Greeks
    replace the Black-Scholes estimates in the chain, the rest is added."""
    if not angel:
        return ctx
    greeks = angel.get("greeks") or {}
    for row in ((ctx.get("option_chain") or {}).get("rows") or []):
        for opt in ("CE", "PE"):
            g = greeks.get((round(float(row["strike"]), 1), opt))
            if g and isinstance(row.get(opt), dict):
                row[opt]["greeks"] = dict(g, source="angel")
    ctx["angel_market_data"] = {k: v for k, v in angel.items() if k != "greeks"}
    ctx["angel_market_data"]["greeks_from_angel"] = len(greeks)
    return ctx


def _is_index_symbol(symbol, und):
    """NIFTY27OCT26FUT is NIFTY's; NIFTYNXT5027OCT26FUT is not: the expiry's
    digits must follow the name at once."""
    sym = str(symbol or "").upper()
    return sym.startswith(und) and sym[len(und):len(und) + 1].isdigit()


def _buildup_row(d):
    return {"symbol": d.get("tradingSymbol"), "ltp": _num(d.get("ltp")),
            "price_change_pct": _num(d.get("percentChange")),
            "oi": _num(d.get("opnInterest"), 0), "oi_change": _num(d.get("netChangeOpnInterest"), 0)}


# ── the snapshot the model is shown ───────────────────────────────────────────
def option_chain(store, b, spot, angel=None, now_ns=None):
    """The bound expiry's ladder off the live book, with Greeks (Angel's, else
    a Black-Scholes estimate), PCR, max pain and the OI walls."""
    now_ns = now_ns or time.perf_counter_ns()
    T = years_to_expiry(b.expiry)
    greeks = (angel or {}).get("greeks") or {}
    rows, ce_oi, pe_oi, ce_vol, pe_vol = [], 0.0, 0.0, 0.0, 0.0
    for i, k in enumerate(b.strikes):
        k = float(k)
        row = {"strike": k}
        for opt, slots in (("CE", b.ce_slots), ("PE", b.pe_slots)):
            s = int(slots[i])
            bid, ask, ltp = float(store.bid[s]), float(store.ask[s]), float(store.ltp[s])
            oi, vol = float(store.oi[s]), float(store.volume[s])
            mid = 0.5 * (bid + ask) if bid > 0 and ask >= bid else ltp
            leg = {"bid": _num(bid), "ask": _num(ask), "ltp": _num(ltp), "oi": _num(oi, 0),
                   "volume": _num(vol, 0), "day_high": _num(store.day_high[s]),
                   "day_low": _num(store.day_low[s]),
                   "quote_age_s": _num((now_ns - int(store.recv_ns[s])) / 1e9, 1)}
            g = greeks.get((round(k, 1), opt))
            if g:
                leg["greeks"] = dict(g, source="angel")
            else:
                est = bs_greeks(spot, k, T, mid, opt)
                if est:
                    leg["greeks"] = dict(est, source="black-scholes estimate")
            row[opt] = leg
            if opt == "CE":
                ce_oi, ce_vol = ce_oi + oi, ce_vol + vol
            else:
                pe_oi, pe_vol = pe_oi + oi, pe_vol + vol
        rows.append(row)
    pain = None
    if ce_oi + pe_oi > 0:
        def payout(x):
            return sum(max(0.0, x - r["strike"]) * (r["CE"]["oi"] or 0) +
                       max(0.0, r["strike"] - x) * (r["PE"]["oi"] or 0) for r in rows)
        pain = min((r["strike"] for r in rows), key=payout)
    # No OI on the feed (a replay, an unquoted book) is no wall, not the first strike.
    ce_wall = max(rows, key=lambda r: r["CE"]["oi"] or 0)["strike"] if rows and ce_oi > 0 else None
    pe_wall = max(rows, key=lambda r: r["PE"]["oi"] or 0)["strike"] if rows and pe_oi > 0 else None
    atm = min(rows, key=lambda r: abs(r["strike"] - spot)) if rows else None
    straddle = None
    if atm and atm["CE"]["ltp"] and atm["PE"]["ltp"]:
        straddle = round(atm["CE"]["ltp"] + atm["PE"]["ltp"], 2)
    return {
        "expiry": b.expiry, "days_to_expiry": round(T * 365, 2), "lot_size": int(b.lot_size),
        "strikes_shown": len(rows), "note": "only the subscribed ladder around ATM, not the whole chain",
        "pcr_oi": _num(pe_oi / ce_oi, 3) if ce_oi > 0 else None,
        "pcr_volume": _num(pe_vol / ce_vol, 3) if ce_vol > 0 else None,
        "max_pain": pain, "highest_call_oi_strike": ce_wall, "highest_put_oi_strike": pe_wall,
        "atm_strike": atm["strike"] if atm else None, "atm_straddle": straddle,
        "rows": rows,
    }


def lhm_context(e, und, rows, sig, trend, cand, plan, spot, b, angel=None):
    """Everything the engine knows at the decision, as JSON-ready data.
    `e` is the IntradayEngine; `rows` the 1-minute bars up to the decision bar."""
    from arb.confluence import evaluate
    from arb.regime import atr, atr_ratio, trend_4h
    from arb.smc import analyse
    L = e.L
    side, close, stop, move, adr = sig
    day = str(rows[-1][0])[:10]
    today = [r for r in rows if str(r[0])[:10] == day]
    prev = [r for r in rows if str(r[0])[:10] < day]
    closes1 = [float(r[4]) for r in today]
    five = _fold(today, 5)
    closes5 = [x[4] for x in five]
    days = {}
    for r in prev:
        d = days.setdefault(str(r[0])[:10], [float(r[1]), float(r[2]), float(r[3]), float(r[4])])
        d[1], d[2], d[3] = max(d[1], float(r[2])), min(d[2], float(r[3])), float(r[4])
    for k, hl in (e._lhm_ranges(und, day) or {}).items():
        if str(k) < day and str(k) not in days:
            days[str(k)] = [None, float(hl[0]), float(hl[1]), None]
    hi, lo = max(float(r[2]) for r in today), min(float(r[3]) for r in today)
    orb = today[:15]
    pc = float(prev[-1][4]) if prev else None
    st = analyse(rows[-400:], strength=L.swing_strength, min_bars=L.min_structure_bars)
    try:
        v = evaluate(rows[-400:], spot, L, strike_step=e._strike_step(b))
        verdict = {"tradable": v.tradable, "side": v.side, "grade": v.grade, "veto": v.veto,
                   "setups": [s.name for s in v.setups], "reason": v.reason}
    except Exception as ex:
        verdict = {"error": repr(ex)[:120]}
    a1 = atr(rows[:-1], period=int(L.atr_period))
    ctx = {
        "time_ist": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "index": und, "spot": _num(spot),
        "decision_bar_close": _num(close),
        "square_off_at": _minus(e.cfg.session_end, float(L.squareoff_minutes)),
        "minutes_to_square_off": _num(e.book.time_to_close(e.cfg.session_end) - float(L.squareoff_minutes), 1),
        "strategy": {
            "name": "last-half-hour momentum",
            "rule": (f"at {L.lhm_entry_at}: if the index has moved at least {L.lhm_adr_frac:g} x its "
                     f"{int(L.lhm_adr_days)}-session average daily range from the previous close and the "
                     f"4-hour trend agrees, buy in that direction; stop {L.lhm_stop_atr:g} x 1-min ATR away "
                     f"on the index; no target; out at the square-off"),
            "side": "CALL" if side > 0 else "PUT",
            "move_from_prev_close": _num(move), "average_daily_range": _num(adr),
            "move_in_adr": _num(move / adr if adr else None, 3), "trend_4h": trend,
            "index_stop": _num(stop), "stop_distance_points": _num(abs(close - stop)),
            "atr_1m": _num(a1, 3),
            "backtest_note": "a year of NIFTY: 84 trades, positive, t 2.4 after ~80 variants - unproven",
        },
        "contract": {
            "symbol": cand.symbol, "strike": cand.strike, "type": cand.opt, "expiry": cand.expiry,
            "bid": _num(cand.bid), "ask": _num(cand.ask), "spread": _num(cand.ask - cand.bid),
            "chain_delta": _num(cand.delta, 3), "lot_size": cand.lot_size,
            "premium_stop": _num(plan.get("stop")), "risk_inr": _num(plan.get("planned_risk_inr", plan.get("risk_inr")), 0),
            "round_trip_cost_points": _num(plan.get("cost_points_round_trip")),
        },
        "index_today": {
            "open": _num(today[0][1]), "high": _num(hi), "low": _num(lo), "last": _num(closes1[-1]),
            "prev_close": _num(pc), "gap_pct": _num(100 * (float(today[0][1]) - pc) / pc if pc else None, 3),
            "change_pct": _num(100 * (closes1[-1] - pc) / pc if pc else None, 3),
            "position_in_day_range": _num((closes1[-1] - lo) / (hi - lo) if hi > lo else None, 3),
            "opening_range_15m": {"high": _num(max(float(r[2]) for r in orb)),
                                  "low": _num(min(float(r[3]) for r in orb))} if orb else None,
            "bars_5m": five,
            "last_30_bars_1m": [[str(r[0])[11:16]] + [_num(x) for x in r[1:5]] for r in today[-30:]],
        },
        "indicators": {
            "ema20_5m": _num(_ema(closes5[-60:], 20)), "ema50_5m": _num(_ema(closes5, 50)),
            "rsi14_5m": _num(_rsi(closes5), 1), "rsi14_1m": _num(_rsi(closes1), 1),
            "ema9_1m": _num(_ema(closes1[-60:], 9)), "ema21_1m": _num(_ema(closes1[-90:], 21)),
            "pace_atr_ratio": _num(atr_ratio(rows[-400:]), 3),
        },
        "structure_1m": {"bias": st.bias, "last_event": st.last_event, "reason": st.reason},
        "playbook_verdict_1m": verdict,
        "prior_sessions_daily": [[k] + [_num(x) for x in v] for k, v in sorted(days.items())[-10:]],
        "bars_4h": _bars_4h(rows),
        "option_chain": _safe(lambda: option_chain(e.feed.store, b, spot, angel)),
        "angel_market_data": {"note": "added when fetched"},
        "engine_today": {"trades": e.trades_today, "realised_pnl_inr": _num(e.realised_pnl, 0),
                         "loss_streak": e.loss_streak, "open_positions": e.book.count()},
    }
    fut = getattr(b, "fut_slot", None)
    if fut is not None:
        def future(s=int(fut), store=e.feed.store):
            fl = float(store.ltp[s])
            return {"symbol": getattr(b, "fut_symbol", ""), "ltp": _num(fl), "bid": _num(store.bid[s]),
                    "ask": _num(store.ask[s]), "oi": _num(store.oi[s], 0),
                    "volume": _num(store.volume[s], 0), "basis_points": _num(fl - spot if fl > 0 else None)}
        ctx["future"] = _safe(future)
    others = {}
    for o in sorted({x.underlying for x in e.bindings} - {und}):
        try:
            orows = e.candles.get(o, limit=3000) or []
            otoday = [r for r in orows if str(r[0])[:10] == day]
            oprev = [r for r in orows if str(r[0])[:10] < day]
            opc = float(oprev[-1][4]) if oprev else None
            if otoday and opc:
                others[o] = {"last": _num(otoday[-1][4]),
                             "change_pct": _num(100 * (float(otoday[-1][4]) - opc) / opc, 3),
                             "trend_4h": trend_4h(orows, int(getattr(L, "trend_4h_bars", 5) or 5))}
        except Exception:
            continue
    ctx["other_indices"] = others
    return ctx


def _safe(fn):
    """One optional section of the snapshot: its error instead of its data
    rather than no snapshot at all."""
    try:
        return fn()
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {str(ex)[:160]}"}


def _bars_4h(rows):
    out, cur = [], None
    for r in rows:
        ts = str(r[0])
        key = ts[:10] + (" 09:15" if ts[11:16] < "13:15" else " 13:15")
        if cur is None or cur[0] != key:
            if cur:
                out.append(cur)
            cur = [key, float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        else:
            cur[2], cur[3], cur[4] = max(cur[2], float(r[2])), min(cur[3], float(r[3])), float(r[4])
    if cur:
        out.append(cur)
    return [[b[0]] + [round(x, 2) for x in b[1:]] for b in out[-8:]]


# ── asking the model ──────────────────────────────────────────────────────────
def normalise(answer):
    """The model's JSON, checked: side in CALL / PUT / NO_TRADE, confidence 0-100."""
    if not isinstance(answer, dict):
        raise ValueError(f"not a JSON object: {str(answer)[:120]}")
    side = str(answer.get("side", "")).strip().upper().replace(" ", "_")
    if side not in ("CALL", "PUT", "NO_TRADE"):
        raise ValueError(f"side {answer.get('side')!r} is not CALL, PUT or NO_TRADE")
    try:
        conf = int(round(float(answer.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0
    lst = lambda k: [str(x) for x in (answer.get(k) or []) if str(x).strip()][:12]
    return {"side": side, "confidence": max(0, min(100, conf)),
            "reasoning": str(answer.get("reasoning", "")).strip(),
            "key_factors": lst("key_factors"), "risks": lst("risks"), "data_gaps": lst("data_gaps")}


class AIVerifier:
    """One question per (day, index), asked on a thread of its own so the
    engine never waits on the network; the answer is read back with get().
    Market data from Angel can be fetched ahead (prefetch) for the same key."""

    def __init__(self, L, log=print, journal_dir=None, client_factory=None):
        self.L, self.log, self.journal_dir = L, log, journal_dir
        self._factory = client_factory or self._default_client
        self._client = None
        self._lock = threading.Lock()
        self._asked, self._results, self._pre = {}, {}, {}

    def mode(self):
        m = str(getattr(self.L, "lhm_ai_verify", "off") or "off").lower()
        return m if m in ("off", "shadow", "on") else "off"

    def timeout(self):
        return float(getattr(self.L, "lhm_ai_timeout_sec", 90.0) or 90.0)

    def _default_client(self):
        from services.vertex_ai_mime import VertexAILLM
        cred = str(getattr(self.L, "lhm_ai_credentials", "") or os.getenv("VERTEX_SERVICE_ACCOUNT_FILE", ""))
        if cred and not os.path.isabs(cred):
            cred = os.path.join(ROOT, cred)
        return VertexAILLM(service_account_file=cred,
                           location=str(getattr(self.L, "lhm_ai_location", "global") or "global"),
                           model_name=str(self.L.lhm_ai_model), max_retries=0,
                           timeout_sec=self.timeout())

    def client(self):
        with self._lock:
            if self._client is None:
                self._client = self._factory()
            return self._client

    # Angel's market data and the client's login, ahead of the decision
    def prefetch(self, key, fetch):
        with self._lock:
            if key in self._pre:
                return
            self._pre[key] = None

        def work():
            data = None
            try:
                data = fetch()
            except Exception as ex:
                data = {"errors": {"all": repr(ex)[:160]}}
            try:
                self.client()
            except Exception as ex:
                self.log(f"[ai] model client not ready: {ex!r}"[:300])
            with self._lock:
                self._pre[key] = data
        threading.Thread(target=work, name=f"ai-prefetch-{key}", daemon=True).start()

    def prefetched(self, key):
        with self._lock:
            return self._pre.get(key)

    def _wait_prefetch(self, key, wait=12.0):
        """A prefetch still running is waited for (briefly) rather than repeated."""
        end = time.time() + wait
        while time.time() < end:
            with self._lock:
                if key not in self._pre or self._pre[key] is not None:
                    return self._pre.get(key)
            time.sleep(0.2)
        return None

    def asked(self, key):
        with self._lock:
            return key in self._asked

    def asked_info(self, key):
        with self._lock:
            return dict(self._asked[key]) if key in self._asked else None

    def refuse(self, key, why, strategy_side):
        """Record a failure before anything could be asked (no data snapshot)."""
        with self._lock:
            self._asked.setdefault(key, {"t0": time.time(), "at": now_ist().strftime("%H:%M:%S"),
                                         "side": strategy_side})
            self._results.setdefault(key, {"ok": False, "error": why, "latency_s": 0.0})

    def waited(self, key):
        with self._lock:
            a = self._asked.get(key)
        return time.time() - a["t0"] if a else 0.0

    def get(self, key):
        """The result dict, or None while the question is open."""
        with self._lock:
            return self._results.get(key)

    def expire(self, key, why):
        """The engine stopped waiting: what arrives later is journaled, not used."""
        res = {"ok": False, "error": why, "latency_s": round(self.waited(key), 1)}
        with self._lock:
            if key not in self._results:
                self._results[key] = res
                return res
            return self._results[key]

    def ask(self, key, context, strategy_side, fetch=None):
        """Start the question; the answer lands in get(key). `fetch` gets
        Angel's market data when no prefetch has it."""
        with self._lock:
            if key in self._asked:
                return
            self._asked[key] = {"t0": time.time(), "at": now_ist().strftime("%H:%M:%S"),
                                "side": strategy_side}
        threading.Thread(target=self._run, args=(key, context, strategy_side, fetch),
                         name=f"ai-{key}", daemon=True).start()

    def _run(self, key, context, strategy_side, fetch=None):
        t0 = time.time()
        res = {"ok": False}
        try:
            angel = self._wait_prefetch(key)
            if angel is None and fetch is not None:
                try:
                    angel = fetch()
                except Exception as ex:
                    angel = {"errors": {"all": repr(ex)[:160]}}
            merge_angel(context, angel)
            client = self.client()
            prompt = ("Decide for this trade. Strategy side: " + strategy_side + ".\n\nDATA (JSON):\n"
                      + json.dumps(context, separators=(",", ":"), default=str))
            answer, meta = client.query_json(prompt, schema=SCHEMA, system_instruction=SYSTEM,
                                             model_name=str(self.L.lhm_ai_model),
                                             thinking_level=str(getattr(self.L, "lhm_ai_thinking", "high") or "high"))
            res = dict(normalise(answer), ok=True, **{k: meta.get(k) for k in (
                "model", "input_tokens", "output_tokens", "thinking_tokens", "cost_usd")})
            res["prompt_chars"] = len(prompt)
        except Exception as ex:
            res = {"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:300]}"}
        res["latency_s"] = round(time.time() - t0, 1)
        res["answered_at"] = now_ist().strftime("%H:%M:%S")
        late = False
        with self._lock:
            if key in self._results:
                late = True                     # the engine already gave up on it
            else:
                self._results[key] = res
        self._journal(key, context, strategy_side, res, late)

    def _journal(self, key, context, strategy_side, res, late):
        if not self.journal_dir:
            return
        try:
            os.makedirs(self.journal_dir, exist_ok=True)
            day = key.split(":", 1)[0]
            with open(os.path.join(self.journal_dir, f"ai_lhm_{day}.jsonl"), "a") as fh:
                fh.write(json.dumps({"key": key, "strategy_side": strategy_side, "late": late,
                                     "result": res, "context": context}, default=str) + "\n")
        except OSError as ex:
            self.log(f"[ai] journal write failed: {ex}")


def summary(res, asked=None):
    """What the dashboard shows of an AI result."""
    if res is None:
        return {"state": "pending", "asked_at": (asked or {}).get("at")} if asked else None
    out = {"state": "answered" if res.get("ok") else "failed"}
    for k in ("side", "confidence", "reasoning", "key_factors", "risks", "data_gaps", "error",
              "latency_s", "answered_at", "model", "cost_usd", "input_tokens", "output_tokens"):
        if res.get(k) not in (None, "", []):
            out[k] = res[k]
    if asked:
        out["asked_at"] = asked.get("at")
    return out


def _minus(hhmm, minutes):
    """'15:15' less 1 minute -> '15:14'."""
    h, m = (int(x) for x in str(hhmm).split(":"))
    t = h * 60 + m - int(round(minutes))
    return f"{t // 60:02d}:{t % 60:02d}"
