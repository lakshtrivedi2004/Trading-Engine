"""The option a signal names, from Angel One's scrip master.

The index's options on the nearest expiry AFTER today: a BTST is held
overnight, so a contract expiring today (it settles at the close) is never
bought. The exchange comes from the master itself: NIFTY, BANKNIFTY, FINNIFTY,
MIDCPNIFTY trade on NFO (NSE), SENSEX and BANKEX on BFO (BSE).
"""
from datetime import date

from arb.instruments import parse_expiry


def find_option(master, index, strike, side, today):
    """{token, symbol, exchange, expiry, lot_size, tick, strike, side, index}
    for the nearest expiry after `today`, or None with no such contract."""
    index, side = str(index).upper(), str(side).upper()
    want = round(float(strike) * 100)
    best = None
    for x in master:
        if x.get("name") != index or x.get("instrumenttype") != "OPTIDX":
            continue
        sym = str(x.get("symbol", ""))
        if not sym.endswith(side):
            continue
        try:
            if round(float(x.get("strike") or 0)) != want:
                continue
            exp = parse_expiry(x["expiry"]).date()
        except (TypeError, ValueError, KeyError):
            continue
        if exp <= today:
            continue
        if best is None or exp < best[0]:
            best = (exp, x)
    if best is None:
        return None
    exp, x = best
    tick = float(x.get("tick_size") or 5) / 100.0
    return {"token": str(x["token"]), "symbol": str(x["symbol"]), "exchange": str(x["exch_seg"]),
            "expiry": str(x["expiry"]), "expiry_date": exp.isoformat(), "lot_size": int(float(x["lotsize"])),
            "tick": tick if tick > 0 else 0.05, "strike": float(strike), "side": side, "index": index}


def tick_round(px, tick=0.05, up=True):
    """A price on the exchange's tick: up for a sell target, as close as allowed."""
    import math
    steps = px / tick
    return round((math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)) * tick, 2)


def days_left(contract, today):
    return (date.fromisoformat(contract["expiry_date"]) - today).days
