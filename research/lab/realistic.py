"""Re-price every backtest candidate as the option the engine would actually buy.

  strike   ATM +/- 4 steps, delta in [min_delta, risk-cap delta], ask x lot <=
           capital, the most expensive such contract (arb/intraday.py select)
  premium  Black-Scholes mid on trading time, time at THETA_F speed intraday
  IV       20-day realised vol of the index + a premium fitted to the recorded
           books' ATM IVs (NIFTY 0.118, BANKNIFTY 0.150, SENSEX 0.122 at 1-9 DTE);
           x1.15 on expiry day (0DTE IV in the books)
  fills    buy mid + spread/2, sell mid - spread/2 (NIFTY 0.25, BANKNIFTY 2.0,
           SENSEX 1.0 points; FIN/MIDCP 0.5% of premium), Rs 75 fees a lot
  exits    tools/backtest.py's index exits + the premium stop and +1R breakeven
           of arb/positions.py, checked on every bar
"""
import json, math, os, sys
from datetime import date, datetime, timedelta
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tools"))
import backtest as bt
from bsmodel import bs, delta as bs_delta, T_years, expiry_calendar
from arb.config import CostModel, IntradayLimits
from arb.costs import leg_cost
CM = CostModel.load()

bt.LOT.update(FINNIFTY=60, MIDCPNIFTY=120); bt.STEP.update(FINNIFTY=100.0, MIDCPNIFTY=25.0)
L = IntradayLimits.load()
FEE, THETA_F, EXPIRY_IV_MULT, SPREAD_FRAC = 75.0, 0.55, 1.15, 0.005
RULE = {"NIFTY": "weekly_tue", "SENSEX": "weekly_thu", "BANKNIFTY": "monthly_tue",
        "FINNIFTY": "monthly_tue", "MIDCPNIFTY": "monthly_tue"}
IV_REF = {"NIFTY": 0.118, "BANKNIFTY": 0.150, "SENSEX": 0.122}
# BANKNIFTY 0.9 -> 2.0: the median real spread at ATM +/- 3 on the traded expiry across
# 11 live recorded days (research/live_calibration.py, 19 Oct 2026); NIFTY and SENSEX matched.
SPREAD = {"NIFTY": 0.25, "BANKNIFTY": 2.0, "SENSEX": 1.0}
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"]

def load_rows(name):
    # HISTORY_DIR points the lab at another copy of the bars (e.g. .cache/history_oct)
    base = os.environ.get("HISTORY_DIR") or os.path.join(ROOT, ".cache", "history")
    return [r for r in json.load(open(os.path.join(base, f"{name}.json")))
            if "09:15" <= r[0][11:16] <= "15:29"]

class Index:
    def __init__(self, name):
        self.name, self.rows = name, load_rows(name)
        closes = {}
        for r in self.rows: closes[r[0][:10]] = r[4]
        self.days = sorted(date.fromisoformat(d) for d in closes)
        lr = np.diff(np.log(np.array([closes[d.isoformat()] for d in self.days])))
        self.rv = {}
        for k, d in enumerate(self.days):
            # Returns up to the PREVIOUS close: lr[k-1] is the move into day k,
            # which is not known at an intraday entry (review R3).
            w = lr[max(0, k - 21):k - 1] if k >= 1 else lr[:0]
            self.rv[d] = float(np.std(w) * math.sqrt(252)) if len(w) >= 10 else None
        rv_last = next(v for v in (self.rv[d] for d in reversed(self.days)) if v)
        self.iv_add = IV_REF.get(name, IV_REF["BANKNIFTY"]) - rv_last
        self.cal = expiry_calendar(self.days, RULE[name])
        self.step, self.lot = bt.STEP[name], bt.LOT[name]

    def iv(self, d, expiry_today):
        s = min(0.45, max(0.08, (self.rv.get(d) or 0.10) + self.iv_add))
        return s * (EXPIRY_IV_MULT if expiry_today else 1.0)

    def spread(self, mid):
        return SPREAD.get(self.name) or max(0.05, SPREAD_FRAC * mid)

def when(iso, plus=1):
    return datetime.fromisoformat(iso) + timedelta(minutes=plus)

def _exact_rt(px, lot):
    """Exact BUY + SELL charges at one premium, per unit (arb/costs.py:leg_cost
    with config/costs.json) - what the engine books, and with
    exact_trade_costs what it plans with."""
    return (leg_cost(px, lot, "BUY", "OPT", CM) + leg_cost(px, lot, "SELL", "OPT", CM)) / lot


def reprice(t, ix, capital, next_expiry_on_expiry_day=False, costs_inside=False,
            slip_spreads=1.0, slip_frac=0.01, exact_costs=None):
    """costs_inside (owner's F5, 1 Oct 2026): the round trip and an exit slip of
    slip_spreads x spread + slip_frac x premium count INSIDE the per-trade cap,
    so a stopped-out trade loses at most the cap, costs included.

    exact_costs (default: the config's exact_trade_costs): charge the exact
    BUY + SELL fees instead of a flat Rs 75 a lot, and apply the engine's own
    plan_trade friction test (max_cost_frac_of_risk) - without it the model
    took trades the live engine refuses."""
    if exact_costs is None:
        exact_costs = bool(getattr(L, "exact_trade_costs", False))
    rows, lot, step = ix.rows, ix.lot, ix.step
    i, j, side = t["i"], t["j"], t["side"]
    S0 = rows[i][4]; opt = "CE" if side > 0 else "PE"
    d0 = date.fromisoformat(t["day"])
    exp = ix.cal.get(d0)
    if exp is None: return None
    expiry_today = exp == d0
    if expiry_today and next_expiry_on_expiry_day:
        later = [x for x in ix.days if x > d0]
        exp = ix.cal.get(later[0]) if later else exp
        if exp == d0: return None
    t0 = when(rows[i][0]); T0 = T_years(t0, exp, ix.days)
    sig = ix.iv(d0, exp == d0)
    cap_inr = capital * L.max_risk_pct_per_trade / 100.0
    idx_risk = t["risk"]; max_delta = cap_inr / (idx_risk * lot)
    atm = round(S0 / step) * step
    best = None
    for off in range(-L.max_strike_offset, L.max_strike_offset + 1):
        K = atm + off * step
        mid = bs(S0, K, T0, sig, opt)
        if mid <= 0.05: continue
        sp = ix.spread(mid); ask = mid + sp / 2
        dlt = abs(bs_delta(S0, K, T0, sig, opt))
        md = max_delta
        if costs_inside:
            fee = _exact_rt(ask, lot) if exact_costs else FEE / lot
            extra = fee + slip_spreads * sp + slip_frac * ask
            md = (cap_inr / lot - extra) / idx_risk
        if ask * lot > capital or dlt < L.min_delta or dlt > md or sp > L.max_spread_points:
            continue
        if best is None or ask > best[2]:
            best = (K, mid, ask, dlt, sp)
    if best is None: return None
    K, mid0, ask0, dlt, sp = best
    prem_risk = min(max(idx_risk * dlt, 2 * sp), ask0 * L.max_risk_frac_of_premium)
    fee_pts = _exact_rt(ask0, lot) if exact_costs else FEE / lot
    extra = (fee_pts + slip_spreads * sp + slip_frac * ask0) if costs_inside else 0.0
    if costs_inside:
        if (prem_risk + extra) * lot > cap_inr + 1e-6:
            return None
    elif prem_risk * lot > cap_inr * 1.05: return None
    cost_pts = fee_pts if exact_costs else FEE / lot + sp
    if 2 * prem_risk - cost_pts < L.min_net_points: return None
    frac = float(getattr(L, "max_cost_frac_of_risk", 0.0) or 0.0)
    if exact_costs and frac > 0 and cost_pts > frac * (prem_risk + extra):
        return None
    stop = ask0 - prem_risk
    if stop <= 0.05: return None
    trailed, exit_k, exit_px, why = False, None, None, t["why"]
    S_exit = S0 + side * (t["net"] + bt.FEE) / (t["delta"] * lot)
    for k in range(i + 1, j + 1):
        S = S_exit if k == j else rows[k][4]
        Tk = T0 - THETA_F * (T0 - T_years(when(rows[k][0]), exp, ix.days))
        bid = bs(S, K, max(Tk, 0.0), sig, opt) - sp / 2
        if not trailed and bid - ask0 >= prem_risk:
            stop, trailed = max(stop, ask0 + cost_pts), True
        if bid <= stop and k < j:
            exit_k, exit_px, why = k, bid, ("prem_trail" if trailed else "prem_stop"); break
    if exit_k is None:
        Tj = T0 - THETA_F * (T0 - T_years(when(rows[j][0]), exp, ix.days))
        exit_k, exit_px = j, bs(S_exit, K, max(Tj, 0.0), sig, opt) - sp / 2
    fees = (leg_cost(ask0, lot, "BUY", "OPT", CM) + leg_cost(max(exit_px, 0.05), lot, "SELL", "OPT", CM)
            if exact_costs else FEE)
    return dict(net=(exit_px - ask0) * lot - fees, held=exit_k - i, why=why, expiry_day=expiry_today,
                risk_inr=(prem_risk + extra) * lot,
                dte=(exp - d0).days, prem=ask0, lot_cost=ask0 * lot, strike=K, delta=dlt)

_C = None
def load_all(capital=13466.9, next_expiry=False, names=None):
    global _C
    if _C is None:
        _C = json.load(open(os.path.join(HERE, "candidates.json")))
    names = names or INDICES
    IX = {n: Index(n) for n in names}
    out, dropped = [], {}
    for t in _C:
        if t["u"] not in IX: continue
        r = reprice(t, IX[t["u"]], capital, next_expiry)
        if r is None:
            dropped[t["u"]] = dropped.get(t["u"], 0) + 1; continue
        u = dict(t); u["net_old"] = t["net"]; u.update(r); out.append(u)
    return out, dropped, IX
