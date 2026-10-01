"""Black-Scholes premium model used to put theta, gamma and lot cost into the backtest.

Time is TRADING time: 375 minutes a session, 252 sessions a year. Checked against
real NIFTY premiums (29SEP2026 options, 4-23 DTE, 1-minute LTPs) and the 29 Sep 2026
expiry-day book (0 DTE): with time running at f = 0.5-0.6 of trading-time speed
intraday the model's mean error on 20-minute premium changes is ~0 (corr 0.95-0.96).
"""
import math
from datetime import date, timedelta

MIN_PER_DAY, DAYS_PER_YEAR = 375.0, 252.0
R = 0.06

def _N(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs(S, K, T, sig, opt):
    if T <= 1e-9 or sig <= 0:
        return max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    v = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / v
    d2 = d1 - v
    if opt == "CE":
        return S * _N(d1) - K * math.exp(-R * T) * _N(d2)
    return K * math.exp(-R * T) * _N(-d2) - S * _N(-d1)

def delta(S, K, T, sig, opt):
    if T <= 1e-9 or sig <= 0:
        return (1.0 if S > K else 0.0) if opt == "CE" else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return _N(d1) if opt == "CE" else _N(d1) - 1.0

def implied_vol(P, S, K, T, opt, lo=0.01, hi=3.0):
    intrinsic = max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    if P <= intrinsic + 1e-6 or T <= 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs(S, K, T, mid, opt) > P: hi = mid
        else: lo = mid
    return 0.5 * (lo + hi)

def minutes_left_today(t):
    end = t.replace(hour=15, minute=30, second=0, microsecond=0)
    return max(0.0, (end - t).total_seconds() / 60.0)

def T_years(t, expiry_day, trading_days):
    """Trading-time years from datetime t to expiry_day 15:30."""
    today = t.date()
    if expiry_day < today:
        return 0.0
    m = minutes_left_today(t)
    if expiry_day > today:
        full = sum(1 for d in trading_days if today < d <= expiry_day)
        last = trading_days[-1] if trading_days else today
        if expiry_day > last:
            d = max(last, today) + timedelta(days=1)
            while d <= expiry_day:
                full += d.weekday() < 5
                d += timedelta(days=1)
        m += MIN_PER_DAY * full
    return m / (MIN_PER_DAY * DAYS_PER_YEAR)

def expiry_calendar(trading_days, rule):
    """{session: expiry traded that session}. rule: weekly_tue | weekly_thu |
    monthly_tue (last Tuesday). A holiday on expiry moves it to the previous session."""
    tset = set(trading_days)
    def shift(d):
        while d not in tset and d >= trading_days[0]:
            d -= timedelta(days=1)
        return d
    def nominal_after(d):
        if rule in ("weekly_tue", "weekly_thu"):
            wd = 1 if rule == "weekly_tue" else 3
            x = d + timedelta(days=(wd - d.weekday()) % 7)
            return [x, x + timedelta(days=7)]
        out, y, mth = [], d.year, d.month
        for _ in range(2):
            nxt = date(y + (mth == 12), mth % 12 + 1, 1)
            x = nxt - timedelta(days=1)
            while x.weekday() != 1: x -= timedelta(days=1)
            out.append(x); y, mth = nxt.year, nxt.month
        return out
    cal = {}
    for d in trading_days:
        for x in nominal_after(d):
            e = shift(x) if x <= trading_days[-1] else x
            if e >= d:
                cal[d] = e; break
    return cal
