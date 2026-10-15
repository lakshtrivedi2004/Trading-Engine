"""Fundamental valuation (Varsity M3, M13): ratios, CAPM, WACC, FCFF, terminal value, DCF."""
from __future__ import annotations

from dataclasses import dataclass, field


def capm(rf: float, beta: float, rm: float) -> float:
    """[capm] Re = Rf + beta*(Rm - Rf)."""
    return rf + beta * (rm - rf)


def wacc(debt: float, equity: float, cost_debt: float, cost_equity: float, tax: float) -> float:
    """[wacc] wd*kd*(1-t) + we*ke."""
    v = debt + equity
    return debt / v * cost_debt * (1 - tax) + equity / v * cost_equity


def fcff(ebit: float, tax: float, dep_amort: float, capex: float, delta_wc: float, deferred_tax: float = 0.0) -> float:
    """[fcff_fcfe] EBIT(1-t) + D&A + deferred tax - capex - change in working capital."""
    return ebit * (1 - tax) + dep_amort + deferred_tax - capex - delta_wc


def terminal_value(last_cf: float, growth: float, rate: float) -> float:
    """[terminal_value] Gordon growth; requires rate > growth."""
    if rate <= growth:
        raise ValueError("discount rate must exceed terminal growth")
    return last_cf * (1 + growth) / (rate - growth)


@dataclass
class DCFResult:
    pv_cashflows: list[float]
    terminal_value: float
    pv_terminal: float
    enterprise_value: float
    equity_value: float
    fair_price: float
    tv_share: float
    details: dict = field(default_factory=dict)

    def upside(self, market_price: float) -> float:
        return self.fair_price / market_price - 1


def dcf(cashflows: list[float], rate: float, terminal_growth: float, net_debt: float, shares: float) -> DCFResult:
    """[dcf]/[dcf_model_implementation] discount explicit-period cash flows + TV -> fair price.

    net_debt = debt - cash. Varsity: 5 explicit years, g ~ long-term inflation, DCF fails on negative CFs."""
    pvs = [cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cashflows)]
    tv = terminal_value(cashflows[-1], terminal_growth, rate)
    pv_tv = tv / (1 + rate) ** len(cashflows)
    ev = sum(pvs) + pv_tv
    eq = ev - net_debt
    return DCFResult(pvs, tv, pv_tv, ev, eq, eq / shares, pv_tv / ev)


def dcf_sensitivity(cashflows, rates, growths, net_debt, shares) -> dict:
    """Fair price grid over (rate, growth) - Varsity stresses TV sensitivity."""
    return {(r, g): dcf(cashflows, r, g, net_debt, shares).fair_price for r in rates for g in growths if r > g}


def margin_of_safety_price(fair_price: float, mos: float = 0.30) -> float:
    """[margin_of_safety] buy only below fair*(1-mos) (Varsity suggests ~30%)."""
    return fair_price * (1 - mos)


# ---------------------------------------------------------------- ratios [financial_ratios]

def ratios(f: dict) -> dict:
    """Compute Varsity's core ratios from a dict of statement items (missing keys are skipped).

    Expected keys (INR, same period): revenue, cogs, ebitda, pat, interest, ebit, total_assets, equity,
    debt, current_assets, current_liabilities, inventory, receivables, price, shares, book_value."""
    out = {}

    def put(name, fn):
        try:
            out[name] = fn()
        except (KeyError, ZeroDivisionError, TypeError):
            pass
    put("ebitda_margin", lambda: f["ebitda"] / f["revenue"])
    put("pat_margin", lambda: f["pat"] / f["revenue"])
    put("gross_margin", lambda: (f["revenue"] - f["cogs"]) / f["revenue"])
    put("roe", lambda: f["pat"] / f["equity"])
    put("roa", lambda: (f["pat"] + f["interest"] * (1 - f.get("tax_rate", 0.25))) / f["total_assets"])
    put("roce", lambda: f["ebit"] / (f["equity"] + f["debt"]))
    put("interest_coverage", lambda: f["ebit"] / f["interest"])
    put("debt_to_equity", lambda: f["debt"] / f["equity"])
    put("debt_to_asset", lambda: f["debt"] / f["total_assets"])
    put("financial_leverage", lambda: f["total_assets"] / f["equity"])
    put("current_ratio", lambda: f["current_assets"] / f["current_liabilities"])
    put("asset_turnover", lambda: f["revenue"] / f["total_assets"])
    put("inventory_days", lambda: f["inventory"] / f["cogs"] * 365)
    put("receivable_days", lambda: f["receivables"] / f["revenue"] * 365)
    put("eps", lambda: f["pat"] / f["shares"])
    put("pe", lambda: f["price"] / (f["pat"] / f["shares"]))
    put("pb", lambda: f["price"] / (f["equity"] / f["shares"]))
    put("ps", lambda: f["price"] / (f["revenue"] / f["shares"]))
    if {"pat_margin", "asset_turnover", "financial_leverage"} <= out.keys():
        out["roe_dupont"] = out["pat_margin"] * out["asset_turnover"] * out["financial_leverage"]
    return out
