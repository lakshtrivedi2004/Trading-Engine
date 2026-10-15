"""Technical indicators as defined in Varsity M2 (node ids in brackets).

All functions take pandas Series / DataFrames with columns open, high, low, close, volume
(lower-case) and return Series/DataFrames aligned to the input index.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    """[moving_average] simple moving average."""
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """[moving_average] exponential moving average (alpha = 2/(n+1))."""
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing (alpha = 1/n) used by RSI, ATR, ADX."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """[rsi] RSI = 100 - 100/(1+RS), RS = avg_gain/avg_loss (Wilder)."""
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """[macd] macd line, signal line, histogram."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """[bollinger_bands] middle SMA(n), upper/lower = middle +/- k*stdev."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"middle": mid, "upper": mid + k * sd, "lower": mid - k * sd,
                         "width": 2 * k * sd / mid})


def true_range(df: pd.DataFrame) -> pd.Series:
    """[atr] max(high-low, |high-prev_close|, |low-prev_close|)."""
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                     axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """[atr] Average True Range (Wilder)."""
    return wilder(true_range(df), n)


def atr_bands(df: pd.DataFrame, n: int = 14, mult: float = 1.0) -> pd.DataFrame:
    """[atr_bands] close +/- mult*ATR - expected range / volatility stops."""
    a = atr(df, n)
    return pd.DataFrame({"upper": df["close"] + mult * a, "lower": df["close"] - mult * a, "atr": a})


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """[adx] +DI, -DI and ADX (Wilder)."""
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = wilder(true_range(df), n)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": wilder(dx, n)})


def aroon(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    """[aroon] up/down = 100*(n - periods since n-high/low)/n."""
    since_high = df["high"].rolling(n + 1).apply(lambda w: n - int(np.argmax(w)), raw=True)
    since_low = df["low"].rolling(n + 1).apply(lambda w: n - int(np.argmin(w)), raw=True)
    up = 100 * (n - since_high) / n
    down = 100 * (n - since_low) / n
    return pd.DataFrame({"aroon_up": up, "aroon_down": down, "oscillator": up - down})


def alligator(close: pd.Series) -> pd.DataFrame:
    """[alligator] simplified Varsity version: SMA 5/8/13 alignment."""
    a, b, c = sma(close, 5), sma(close, 8), sma(close, 13)
    state = np.where((close > a) & (a > b) & (b > c), 1, np.where((close < a) & (a < b) & (b < c), -1, 0))
    return pd.DataFrame({"lips": a, "teeth": b, "jaw": c, "state": state}, index=close.index)


def supertrend(df: pd.DataFrame, n: int = 7, mult: float = 3.0) -> pd.DataFrame:
    """[supertrend] returns line and direction (+1 green/long, -1 red/short)."""
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    bu, bl = (hl2 + mult * a).to_numpy(), (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    fu, fl = bu.copy(), bl.copy()
    line = np.full(len(df), np.nan)
    direction = np.zeros(len(df), dtype=int)
    for i in range(len(df)):
        if np.isnan(bu[i]):
            continue
        if i > 0 and not np.isnan(fu[i - 1]):
            fu[i] = bu[i] if (bu[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = bl[i] if (bl[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
            prev = direction[i - 1] or 1
            if prev == 1:
                direction[i] = -1 if close[i] < fl[i] else 1
            else:
                direction[i] = 1 if close[i] > fu[i] else -1
        else:
            direction[i] = 1 if close[i] >= hl2.iloc[i] else -1
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=df.index)


def vwap(df: pd.DataFrame, session: pd.Series | None = None) -> pd.Series:
    """[vwap] cumulative typical_price*volume / cumulative volume, reset each session.

    `session` - labels per row (defaults to the calendar date of a DatetimeIndex)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    if session is None:
        session = pd.Series(pd.DatetimeIndex(df.index).date, index=df.index)
    pv = (tp * df["volume"]).groupby(session).cumsum()
    v = df["volume"].groupby(session).cumsum()
    return pv / v


def fibonacci_levels(peak: float, trough: float, uptrend: bool = True,
                     ratios=(0.236, 0.382, 0.5, 0.618)) -> dict[float, float]:
    """[fibonacci_retracement] retracement levels of a move (0.5 included by convention)."""
    rng = peak - trough
    if uptrend:
        return {r: peak - r * rng for r in ratios}
    return {r: trough + r * rng for r in ratios}


def pivot_points(high: float, low: float, close: float) -> dict[str, float]:
    """[pivot_points] classic floor pivots."""
    p = (high + low + close) / 3
    return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + (high - low), "S2": p - (high - low)}


def swing_pivots(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Fractal swing highs/lows (k bars each side) - raw input for support/resistance."""
    h, l = df["high"], df["low"]
    win = 2 * k + 1
    sh = h[(h == h.rolling(win, center=True).max())]
    sl = l[(l == l.rolling(win, center=True).min())]
    return pd.DataFrame({"swing_high": sh, "swing_low": sl}).reindex(df.index)


def support_resistance(df: pd.DataFrame, k: int = 3, tol: float = 0.01, min_touches: int = 3) -> pd.DataFrame:
    """[support_resistance] cluster swing pivots within +/-tol; keep levels with >= min_touches.

    Returns rows: level, touches, kind (support if below last close else resistance)."""
    piv = swing_pivots(df, k)
    pts = pd.concat([piv["swing_high"].dropna(), piv["swing_low"].dropna()]).sort_values().to_numpy()
    levels: list[list[float]] = []
    for p in pts:
        if levels and abs(p - np.mean(levels[-1])) <= tol * p:
            levels[-1].append(p)
        else:
            levels.append([p])
    last = float(df["close"].iloc[-1])
    rows = [{"level": float(np.mean(c)), "touches": len(c),
             "kind": "support" if np.mean(c) < last else "resistance"}
            for c in levels if len(c) >= min_touches]
    return pd.DataFrame(rows, columns=["level", "touches", "kind"])


def crossover(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """True on bars where a crosses above b."""
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift() <= b.shift())


def crossunder(a: pd.Series, b: pd.Series | float) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < b) & (a.shift() >= b.shift())


def log_returns(close: pd.Series) -> pd.Series:
    """[volatility] ln(P_t/P_{t-1})."""
    return np.log(close / close.shift())


def historical_volatility(close: pd.Series, n: int | None = None, periods: int = 252) -> float | pd.Series:
    """[volatility] annualised stdev of log returns (rolling if n given)."""
    r = log_returns(close)
    if n:
        return r.rolling(n).std() * np.sqrt(periods)
    return float(r.std() * np.sqrt(periods))
