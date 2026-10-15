"""Options math from Varsity M5/M6: Black-Scholes, Greeks, IV, SD ranges, max pain, PCR."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, |err| < 1.2e-9)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


@dataclass
class BSResult:
    price: float
    delta: float
    gamma: float
    theta: float   # per calendar day
    vega: float    # per 1 vol point (1%)
    rho: float     # per 1% rate change
    d1: float
    d2: float


def black_scholes(spot: float, strike: float, days: float, vol: float, rate: float = 0.065,
                  div_yield: float = 0.0, kind: str = "call") -> BSResult:
    """[black_scholes] European option price and Greeks [delta, gamma, theta, vega].

    vol and rate are annual decimals (0.18 = 18%); days = calendar days to expiry (Varsity uses 365)."""
    t = max(days, 1e-9) / 365.0
    sq = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - div_yield + 0.5 * vol * vol) * t) / sq
    d2 = d1 - sq
    df_r, df_q = math.exp(-rate * t), math.exp(-div_yield * t)
    if kind == "call":
        price = spot * df_q * norm_cdf(d1) - strike * df_r * norm_cdf(d2)
        delta = df_q * norm_cdf(d1)
        theta = (-spot * df_q * norm_pdf(d1) * vol / (2 * math.sqrt(t)) - rate * strike * df_r * norm_cdf(d2)
                 + div_yield * spot * df_q * norm_cdf(d1))
        rho = strike * t * df_r * norm_cdf(d2)
    elif kind == "put":
        price = strike * df_r * norm_cdf(-d2) - spot * df_q * norm_cdf(-d1)
        delta = -df_q * norm_cdf(-d1)
        theta = (-spot * df_q * norm_pdf(d1) * vol / (2 * math.sqrt(t)) + rate * strike * df_r * norm_cdf(-d2)
                 - div_yield * spot * df_q * norm_cdf(-d1))
        rho = -strike * t * df_r * norm_cdf(-d2)
    else:
        raise ValueError("kind must be 'call' or 'put'")
    gamma = df_q * norm_pdf(d1) / (spot * sq)
    vega = spot * df_q * norm_pdf(d1) * math.sqrt(t)
    return BSResult(price, delta, gamma, theta / 365, vega / 100, rho / 100, d1, d2)


def implied_vol(price: float, spot: float, strike: float, days: float, rate: float = 0.065,
                kind: str = "call", div_yield: float = 0.0, lo: float = 1e-4, hi: float = 5.0) -> float:
    """[implied_volatility] bisection solve of BS price = market price."""
    intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    if price < intrinsic * math.exp(-rate * days / 365) - 1e-9:
        return float("nan")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if black_scholes(spot, strike, days, mid, rate, div_yield, kind).price > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-7:
            break
    return 0.5 * (lo + hi)


def put_call_parity_gap(call: float, put: float, spot: float, strike: float, days: float, rate: float = 0.065) -> float:
    """[put_call_parity] C + K*e^{-rT} - (P + S); non-zero (beyond costs) = arbitrage."""
    return call + strike * math.exp(-rate * days / 365) - (put + spot)


def payoff(spot_at_expiry, strike: float, premium: float, kind: str = "call", side: int = 1, qty: int = 1):
    """[options_payoff_summary] P&L at expiry for one leg; side=+1 long, -1 short."""
    s = np.asarray(spot_at_expiry, dtype=float)
    intrinsic = np.maximum(s - strike, 0) if kind == "call" else np.maximum(strike - s, 0)
    return side * (intrinsic - premium) * qty


def strategy_payoff(spot_at_expiry, legs: list[dict]):
    """Sum of legs: each {'strike','premium','kind','side','qty'}; e.g. bull_call_spread, straddle."""
    return sum(payoff(spot_at_expiry, **leg) for leg in legs)


def sd_range(spot: float, daily_vol: float, days: int, k: float = 1.0, daily_mean: float = 0.0) -> tuple[float, float]:
    """[sd_option_writing]/[normal_distribution] k-SD price range over `days` (log-normal)."""
    m = daily_mean * days
    sd = daily_vol * math.sqrt(days)
    return spot * math.exp(m - k * sd), spot * math.exp(m + k * sd)


def sd_writing_strikes(spot: float, annual_vol: float, days: int, strikes: list[float], k: float = 1.0) -> dict:
    """[sd_option_writing] call strikes above the upper k-SD bound (author avoids naked puts).
    Rule: use k=1 within ~4-7 days of expiry, k=2 if writing earlier; never > 15 days out."""
    lo, hi = sd_range(spot, annual_vol / math.sqrt(365), days, k)
    return {"range": (lo, hi), "calls_to_write": [s for s in strikes if s > hi],
            "puts_beyond": [s for s in strikes if s < lo], "ok_timing": days <= 15}


def max_pain(strikes, call_oi, put_oi) -> tuple[float, dict]:
    """[max_pain] strike where total option-writer payout is minimal."""
    strikes = np.asarray(strikes, float)
    call_oi, put_oi = np.asarray(call_oi, float), np.asarray(put_oi, float)
    loss = {float(s): float((call_oi * np.maximum(0, s - strikes)).sum() + (put_oi * np.maximum(0, strikes - s)).sum())
            for s in strikes}
    return min(loss, key=loss.get), loss


def pcr(put_oi, call_oi) -> dict:
    """[put_call_ratio] contrarian reading: >1.3 oversold (bullish), <0.5 overbought (bearish)."""
    r = float(np.sum(put_oi) / np.sum(call_oi))
    view = "contrarian_long" if r > 1.3 else "contrarian_short" if r < 0.5 else "neutral"
    return {"pcr": r, "view": view}


def moneyness(spot: float, strike: float, kind: str = "call", atm_band: float = 0.005) -> str:
    """[moneyness] ITM / ATM / OTM."""
    if abs(spot - strike) <= atm_band * spot:
        return "ATM"
    itm = spot > strike if kind == "call" else spot < strike
    return "ITM" if itm else "OTM"


STRATEGY_SELECTOR = {
    ("moderately_bullish", "low_iv"): ["bull_call_spread"],
    ("moderately_bullish", "high_iv"): ["bull_put_spread"],
    ("outright_bullish", "any"): ["call_ratio_back_spread", "bear_call_ladder"],
    ("moderately_bearish", "low_iv"): ["bear_put_spread"],
    ("moderately_bearish", "high_iv"): ["bear_call_spread"],
    ("outright_bearish", "any"): ["put_ratio_back_spread"],
    ("big_move_unknown", "low_iv"): ["long_straddle", "long_strangle"],
    ("range_bound", "high_iv"): ["short_straddle", "short_strangle"],
}


def select_strategy(view: str, iv_regime: str = "any") -> list[str]:
    """[strategy_selector] map market view + IV regime to Varsity strategies."""
    return STRATEGY_SELECTOR.get((view, iv_regime)) or STRATEGY_SELECTOR.get((view, "any")) or []
