"""Index-option expression of an index signal (NIFTY / BANKNIFTY / SENSEX).

LONG signal -> buy CE, SHORT signal -> buy PE (buying only: ₹12k can't margin writing).

Strike choice is pure arithmetic:
  1. pull the option chain around ATM for the nearest usable expiry from the scrip master
  2. live LTPs (one quote call) -> implied vol -> Black-Scholes delta  (tradekb.options)
  3. premium stop  = premium - |delta| * (spot stop distance)
     premium tgt   = premium + |delta| * (spot target distance)
  4. keep strikes whose full stop-out loss (incl. option charges + slippage) fits the budget,
     whose net R:R after charges clears the minimum, and whose premium fits the capital
  5. take the highest-delta survivor (most responsive to the index move)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tradekb.options import black_scholes, implied_vol

from .costs import trade_costs
from .risk import round_tick

log = logging.getLogger("scalper.options")

# underlying key -> (spot exchange, spot token, option segment, scrip-master name)
INDICES = {
    "NIFTY": ("NSE", "99926000", "NFO", "NIFTY"),
    "BANKNIFTY": ("NSE", "99926009", "NFO", "BANKNIFTY"),
    "SENSEX": ("BSE", "99919000", "BFO", "SENSEX"),
}


@dataclass
class OptionPlan:
    underlying: str
    tradingsymbol: str
    token: str
    exchange: str
    kind: str            # CE / PE
    strike: float
    expiry: str
    lot: int
    qty: int
    tick: float
    premium: float
    prem_stop: float
    prem_target: float
    delta: float
    iv: float
    net_loss: float
    net_win: float

    @property
    def net_rr(self) -> float:
        return self.net_win / self.net_loss if self.net_loss > 0 else 0.0


class OptionChain:
    def __init__(self, scrip_master: Path):
        rows = json.loads(scrip_master.read_text())
        self.by_name: dict[str, list[dict]] = {}
        names = {v[3]: k for k, v in INDICES.items()}
        segs = {v[2] for v in INDICES.values()}
        for r in rows:
            if r.get("instrumenttype") == "OPTIDX" and r.get("exch_seg") in segs and r.get("name") in names:
                self.by_name.setdefault(names[r["name"]], []).append(r)

    def contracts(self, und: str, now: datetime) -> tuple[str, list[dict]]:
        """Nearest expiry still worth trading (expiry-day contracts dropped after 13:00)."""
        rows = self.by_name.get(und, [])
        exps = sorted({r["expiry"] for r in rows}, key=lambda e: datetime.strptime(e, "%d%b%Y"))
        for e in exps:
            d = datetime.strptime(e, "%d%b%Y")
            if d.date() > now.date() or (d.date() == now.date() and now.hour < 13):
                return e, [r for r in rows if r["expiry"] == e]
        return "", []


def _days_to_expiry(expiry: str, now: datetime) -> float:
    exp = datetime.strptime(expiry, "%d%b%Y").replace(hour=15, minute=30)
    return max((exp - now).total_seconds() / 86400.0, 0.02)


def plan_option(chain: OptionChain, client, und: str, side: int, spot: float, stop_dist: float, target_dist: float,
                budget: float, capital: float, min_net_rr: float, slippage_ticks: int, now: datetime,
                strikes_each_side: int = 8, exclude: set[str] | None = None) -> OptionPlan | None:
    expiry, rows = chain.contracts(und, now)
    if not rows:
        log.info("%s: no usable expiry", und)
        return None
    kind = "CE" if side > 0 else "PE"
    rows = [r for r in rows if r["symbol"].endswith(kind) and r["token"] not in (exclude or ())]   # never touch user positions
    for r in rows:
        r["_k"] = float(r["strike"]) / 100.0
    rows.sort(key=lambda r: abs(r["_k"] - spot))
    cands = sorted(rows[: 2 * strikes_each_side + 1], key=lambda r: r["_k"])
    seg = INDICES[und][2]
    try:
        q = client.quote([r["token"] for r in cands], exchange=seg, mode="LTP")
    except Exception as e:  # noqa: BLE001
        log.warning("option quote failed: %s", e)
        return None
    days = _days_to_expiry(expiry, now)
    best: OptionPlan | None = None
    for r in cands:
        ltp = float((q.get(r["token"]) or {}).get("ltp") or 0)
        lot = int(float(r["lotsize"]))
        tick = float(r.get("tick_size") or 5) / 100.0 or 0.05
        if ltp < 3 or ltp * lot > capital:
            continue
        iv = implied_vol(ltp, spot, r["_k"], days, kind="call" if kind == "CE" else "put")
        if iv != iv or iv <= 0.01:
            continue
        delta = abs(black_scholes(spot, r["_k"], days, iv, kind="call" if kind == "CE" else "put").delta)
        if delta < 0.08:
            continue
        slip = slippage_ticks * tick
        prem_stop = round_tick(max(ltp - delta * stop_dist, tick), tick)
        prem_tgt = round_tick(ltp + delta * target_dist, tick)
        best_for_strike = None
        for lots in range(1, 6):
            qty = lot * lots
            if ltp * qty > capital:
                break
            loss = (ltp - prem_stop + 2 * slip) * qty + trade_costs(1, ltp, prem_stop - slip, qty, seg)
            if loss > budget:
                break
            win = (prem_tgt - ltp - 2 * slip) * qty - trade_costs(1, ltp, prem_tgt - slip, qty, seg)
            best_for_strike = OptionPlan(und, r["symbol"], r["token"], seg, kind, r["_k"], expiry, lot, qty, tick,
                                         ltp, prem_stop, prem_tgt, delta, iv, loss, win)
        p = best_for_strike
        if p is None or p.net_win <= 0 or p.net_rr < min_net_rr:
            continue
        if best is None or (p.delta, p.net_rr) > (best.delta, best.net_rr):
            best = p
    if best is None:
        log.info("%s %s: no strike fits ₹%.0f budget with R:R >= %.2f after option charges", und, kind, budget, min_net_rr)
    return best
