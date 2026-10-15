"""
Data layer: downloads daily data from Yahoo Finance, caches it locally,
and runs basic quality checks.
"""
import datetime as dt
import os

import numpy as np
import pandas as pd

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
FLOWS_FILE = os.path.join(BASE_DIR, "data", "fii_dii.csv")
EVENTS_FILE = os.path.join(BASE_DIR, "data", "events.csv")


def _safe_name(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def _download(ticker, start):
    """Download one ticker. Returns a clean OHLCV DataFrame or None."""
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).history(start=start, auto_adjust=False, actions=False)
    except Exception as exc:  # network errors, renamed tickers, etc.
        print(f"    ! {ticker}: download failed ({exc})")
        return None
    if df is None or df.empty or "Close" not in df:
        return None
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)       # keep the exchange's local date
    df.index = idx.normalize()
    df = df[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df]]
    df = df[df["Close"] > 0].dropna(subset=["Close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _load_series(name, tickers, start, refresh):
    """Load from today's cache if present, else download (falling back to old cache)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{_safe_name(name)}.csv")
    fresh = (
        os.path.exists(path)
        and dt.date.fromtimestamp(os.path.getmtime(path)) == dt.date.today()
    )
    if fresh and not refresh:
        return pd.read_csv(path, index_col=0, parse_dates=True), "cache"

    for ticker in tickers:
        df = _download(ticker, start)
        if df is not None and len(df) > 50:
            df.to_csv(path)
            return df, ticker

    if os.path.exists(path):
        print(f"    ! {name}: using older cached data (download failed)")
        return pd.read_csv(path, index_col=0, parse_dates=True), "old cache"
    return None, None


def load_market_data(start=config.START_DATE, refresh=False):
    """Returns (indices, factors, notes). indices/factors are dicts of DataFrames."""
    indices, factors, notes = {}, {}, []
    print("Downloading index data...")
    for name, tickers in config.INDICES.items():
        df, src = _load_series(name, tickers, start, refresh)
        if df is None:
            notes.append(f"{name}: no data found for tickers {tickers}. "
                         f"Check the ticker on finance.yahoo.com and update config.py.")
        else:
            indices[name] = df
            print(f"  {name:<12} {len(df):>5} days  ({src})")
    print("Downloading factor data...")
    for name, spec in config.FACTORS.items():
        df, src = _load_series(name, spec["tickers"], start, refresh)
        if df is None:
            notes.append(f"{name}: no data; it will be left out of the analysis.")
        else:
            factors[name] = df
            print(f"  {name:<12} {len(df):>5} days  ({src})")
    notes += quality_checks(indices)
    return indices, factors, notes


def quality_checks(indices):
    """Flag common Yahoo data problems so results aren't trusted blindly."""
    notes = []
    for name, df in indices.items():
        r = df["Close"].pct_change()
        spikes = r[r.abs() > 0.15]
        if len(spikes):
            days = ", ".join(d.strftime("%Y-%m-%d") for d in spikes.index[:5])
            notes.append(f"{name}: {len(spikes)} daily move(s) above 15% ({days}). "
                         f"Real crashes happen (e.g. March 2020), but check these are not data errors.")
        last = df.index.max()
        age = (pd.Timestamp.today().normalize() - last).days
        if age > 5:
            notes.append(f"{name}: latest data is from {last.date()} ({age} days old).")
        if "Open" in df:
            usable = open_data_usable(df)
            if not usable:
                notes.append(f"{name}: Opening prices look unreliable (often equal to the previous "
                             f"close), so gap tests are skipped for this index.")
    return notes


def open_data_usable(df):
    """Yahoo sometimes stores the previous close as the open for Indian indices."""
    if "Open" not in df:
        return False
    recent = df.tail(500)
    prev_close = recent["Close"].shift(1)
    same = (np.isclose(recent["Open"], prev_close, rtol=1e-5)
            | np.isclose(recent["Open"], recent["Close"], rtol=1e-5))
    return same.mean() < 0.2


def load_events():
    """User-maintained calendar events (budget days, RBI policy days, elections...)."""
    if not os.path.exists(EVENTS_FILE):
        return pd.DataFrame(columns=["date", "type", "description"])
    ev = pd.read_csv(EVENTS_FILE, comment="#", skip_blank_lines=True)
    ev.columns = [c.strip().lower() for c in ev.columns]
    ev["date"] = pd.to_datetime(ev["date"], errors="coerce")
    return ev.dropna(subset=["date"])


def load_flows():
    """Optional FII/DII net flows (Rs crore), maintained by the user from NSE data."""
    if not os.path.exists(FLOWS_FILE):
        return None
    fl = pd.read_csv(FLOWS_FILE, comment="#", skip_blank_lines=True)
    fl.columns = [c.strip().lower() for c in fl.columns]
    if fl.empty or "date" not in fl:
        return None
    fl["date"] = pd.to_datetime(fl["date"], errors="coerce", dayfirst=False)
    fl = fl.dropna(subset=["date"]).set_index("date").sort_index()
    for c in ["fii_net", "dii_net"]:
        if c in fl:
            fl[c] = pd.to_numeric(fl[c], errors="coerce")
    return fl if len(fl) else None
