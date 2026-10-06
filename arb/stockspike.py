"""Every stock watched every minute: a sudden ATR spike, and the stock's option.

Owner, 6 Oct 2026: "keep watch on every stock and trade them when ATR goes high
all of a sudden ... after selecting the most volatile stocks, process their
option chain". The stock desk (arb/stockdesk.py) re-ranks the universe every
30 minutes; a stock whose range explodes between two rescans was invisible to
the engine until the next one. This module is the per-minute check:

  spike(rows)            short-window ATR over the stock's own recent normal
                         (median true range), on CLOSED one-minute bars only
  pick_stock_option(...) the CE (long) / PE (short) of that stock the engine
                         could buy: nearest-ATM strike whose one lot fits the
                         capital and whose loss at the stop, costs included,
                         fits the per-trade risk cap. Pure arithmetic on an
                         NSE chain (arb/nse.py:option_chain).

Nothing here sends an order. The desk feeds spiking stocks to the engine's
existing stock path (same gates, stops, sizing and risk cap as every stock
pick); the option side is shadow-only until a backtest and the owner say
otherwise.
"""
from statistics import median

from arb.costs import leg_cost
from arb.intraday import chain_delta


def true_ranges(rows):
    out = []
    for k in range(1, len(rows)):
        h, lo, pc = rows[k][2], rows[k][3], rows[k - 1][4]
        out.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return out


def spike(rows, fast=5, base=120, min_base=30):
    """(ratio, move) for the last `fast` closed bars, or None if history is short.

    ratio = mean true range of the last `fast` bars / median true range of up
    to `base` bars before them; move = close now / close `fast` bars ago - 1.
    `rows` must be closed bars ([iso, o, h, l, c, v], oldest first)."""
    trs = true_ranges(rows)
    if len(trs) < fast + min_base:
        return None
    prior = trs[-(fast + base):-fast]
    if len(prior) < min_base:
        return None
    b = median(prior)
    if b <= 0:
        return None
    ratio = sum(trs[-fast:]) / fast / b
    c0 = rows[-1 - fast][4]
    move = rows[-1][4] / c0 - 1 if c0 > 0 else 0.0
    return ratio, move


def _px(leg, key):
    try:
        return float((leg or {}).get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def pick_stock_option(chain, side, risk_points, capital, cap_inr, lot, cm, otm=3):
    """The option to buy for a stock setup, or why there is none.

    `chain` is arb/nse.py:option_chain's dict; `side` +1 long (CE) / -1 short
    (PE); `risk_points` the stock's stop distance in rupees per share. The
    loss at the stop is |delta| x risk_points x lot plus the round trip
    (fees both ways and the spread), delta taken model-free from the chain
    (arb/intraday.py:chain_delta). Tries ATM then up to `otm` strikes out of
    the money; returns (choice or None, [reasons for every strike skipped])."""
    rows = sorted(chain.get("rows") or [], key=lambda r: float(r.get("strikePrice") or 0))
    und = float(chain.get("underlying") or 0.0)
    if not rows or und <= 0 or lot <= 0 or risk_points <= 0:
        return None, ["no chain / no lot / no stop"]
    opt = "CE" if side > 0 else "PE"
    strikes = [float(r["strikePrice"]) for r in rows]
    mids = []
    for r in rows:
        leg = r.get(opt) or {}
        b, a, l_ = _px(leg, "buyPrice1"), _px(leg, "sellPrice1"), _px(leg, "lastPrice")
        mids.append((b + a) / 2 if b > 0 and a > 0 else l_)
    atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - und))
    step = 1 if side > 0 else -1
    order = [atm + step * k for k in range(otm + 1) if 0 <= atm + step * k < len(rows)]
    why = []
    for i in order:
        leg = rows[i].get(opt) or {}
        bid, ask = _px(leg, "buyPrice1"), _px(leg, "sellPrice1")
        if ask <= 0 or bid <= 0:
            why.append(f"{strikes[i]:g}{opt}: no two-sided quote")
            continue
        lot_cost = ask * lot
        if lot_cost > capital:
            why.append(f"{strikes[i]:g}{opt}: one lot Rs {lot_cost:,.0f} > capital Rs {capital:,.0f}")
            continue
        d = chain_delta(mids, strikes, i, opt)
        if d <= 0:
            why.append(f"{strikes[i]:g}{opt}: no delta")
            continue
        costs = (leg_cost(ask, lot, "BUY", "OPT", cm) + leg_cost(bid, lot, "SELL", "OPT", cm)
                 + (ask - bid) * lot)
        loss = d * risk_points * lot + costs
        if loss > cap_inr:
            why.append(f"{strikes[i]:g}{opt}: loss at stop Rs {loss:,.0f} > cap Rs {cap_inr:,.0f}")
            continue
        return {"strike": strikes[i], "opt": opt, "bid": bid, "ask": ask, "lot": lot,
                "lot_cost": lot_cost, "delta": d, "loss_at_stop": loss, "costs": costs,
                "expiry": chain.get("expiry"), "underlying": und,
                "iv": _px(leg, "impliedVolatility")}, why
    return None, why
