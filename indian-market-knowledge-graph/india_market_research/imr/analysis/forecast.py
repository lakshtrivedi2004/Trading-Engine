"""Forecast lab: next-session forecasts, always reported WITH their measured out-of-sample accuracy.

Two forecasts per index:
1. Direction - probability the next session closes up. Regularised logistic regression on information
   available at the close of day t (own momentum and volatility, distance from the 200-day average,
   India VIX change, and the US session that closed BEFORE day t's Indian close). The US session that
   closes after the Indian close is NOT used: the forecast is made in the evening, before it exists.
2. Move size - expected absolute move of the next session. HAR model (Corsi 2009): next |return| on the
   last day's, last week's and last month's average |return| plus the day's high-low range.
   Volatility clustering makes this genuinely forecastable; direction usually is not.

Honesty rules:
- Walk-forward: refit every `step` sessions on data strictly before the forecast date; every scored
  forecast was made without seeing its outcome.
- Baselines: direction vs "probability = past share of up days"; move size vs "last 20 days' average".
- Diebold & Mariano (1995) test with Newey-West errors on the loss difference decides whether the model
  beats its baseline. Below |t| = 2 the verdict is "no better than baseline".
- Stocks: move size only (pooled HAR across liquid BSE stocks), scored the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .decompose import factor_change_on_calendar
from .stats import newey_west_t

DIR_FEATURES = ["r1", "r5", "r20", "vol20", "dma200", "range", "vix_chg", "us"]
VOL_FEATURES = ["a1", "a5", "a22", "range"]


# ---------------------------------------------------------------- features (known at the close of day t)
def index_features(df: pd.DataFrame, factors: dict | None = None) -> pd.DataFrame:
    c = df["Close"]
    r = c.pct_change()
    a = r.abs()
    f = pd.DataFrame(index=df.index)
    f["r1"], f["r5"], f["r20"] = r, c / c.shift(5) - 1, c / c.shift(20) - 1
    f["vol20"] = r.rolling(20).std()
    f["dma200"] = c / c.rolling(200).mean() - 1
    rng = np.log(df["High"] / df["Low"]) if {"High", "Low"} <= set(df.columns) else pd.Series(np.nan, index=df.index)
    f["range"] = rng.where(rng > 0)
    f["a1"], f["a5"], f["a22"] = a, a.rolling(5).mean(), a.rolling(22).mean()
    factors = factors or {}
    f["vix_chg"] = (factor_change_on_calendar(factors["India VIX"]["Close"], df.index, "ret", "same_day").to_numpy()
                    if "India VIX" in factors else 0.0)
    f["us"] = (factor_change_on_calendar(factors["S&P 500"]["Close"], df.index, "ret", "prev_session").to_numpy()
               if "S&P 500" in factors else 0.0)
    f["range"] = f["range"].fillna(f["a1"] * 1.5)            # if high/low missing, a crude proxy (documented)
    return f


def targets(df: pd.DataFrame) -> pd.DataFrame:
    """Row t holds the NEXT session's outcome (what is being forecast at the close of t)."""
    r = df["Close"].pct_change()
    return pd.DataFrame({"up_next": (r.shift(-1) > 0).astype(float).where(r.shift(-1).notna()),
                         "abs_next": r.shift(-1).abs(), "ret_next": r.shift(-1)}, index=df.index)


# ---------------------------------------------------------------- models (numpy only)
def _standardise(X: np.ndarray, mu=None, sd=None):
    mu = np.nanmean(X, axis=0) if mu is None else mu
    sd = np.nanstd(X, axis=0) if sd is None else sd
    sd = np.where(sd > 0, sd, 1.0)
    return (X - mu) / sd, mu, sd


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 5.0, iters: int = 50):
    Z, mu, sd = _standardise(X)
    Z = np.column_stack([np.ones(len(Z)), Z])
    w = np.zeros(Z.shape[1])
    pen = np.eye(len(w)) * l2
    pen[0, 0] = 0
    for _ in range(iters):                      # Newton-Raphson / IRLS with L2 penalty
        p = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (p - y) + pen @ w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + pen
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return {"w": w, "mu": mu, "sd": sd}


def predict_logistic(m, X):
    Z, _, _ = _standardise(X, m["mu"], m["sd"])
    return 1 / (1 + np.exp(-np.clip(np.column_stack([np.ones(len(Z)), Z]) @ m["w"], -30, 30)))


def fit_ols(X, y, l2: float = 1e-6):
    A = np.column_stack([np.ones(len(X)), X])
    w = np.linalg.solve(A.T @ A + l2 * np.eye(A.shape[1]), A.T @ y)
    return {"w": w}


def predict_ols(m, X):
    return np.column_stack([np.ones(len(X)), X]) @ m["w"]


# ---------------------------------------------------------------- walk-forward engine
def walk_forward(feats: pd.DataFrame, tgt: pd.Series, cols: list[str], fit, predict, start: int = 1000,
                 step: int = 21) -> pd.Series:
    """Out-of-sample predictions. A model used at row T was fit only on rows s with s+1 <= T, i.e. whose
    target (the next session's outcome) was already known at the close of T."""
    X = feats[cols].to_numpy(float)
    y = tgt.to_numpy(float)
    ok_x = ~np.isnan(X).any(axis=1)
    preds = np.full(len(X), np.nan)
    first = np.argmax(ok_x)
    T = max(start, first + 250)
    while T < len(X):
        train = np.arange(T) [ok_x[:T] & ~np.isnan(y[:T])]
        train = train[train <= T - 1]                       # target of row T-1 = outcome of day T: known at T
        if len(train) >= 250:
            model = fit(X[train], y[train])
            block = np.arange(T, min(T + step, len(X)))
            block = block[ok_x[block]]
            if len(block):
                preds[block] = predict(model, X[block])
        T += step
    return pd.Series(preds, index=feats.index)


def dm_test(loss_model: pd.Series, loss_base: pd.Series, lags: int = 5) -> float:
    """Diebold-Mariano: t-statistic of mean(loss_base - loss_model); positive = model better."""
    d = (loss_base - loss_model).dropna()
    return newey_west_t(d, lags)


def _verdict(t: float, n: int) -> str:
    if n < 100 or np.isnan(t):
        return "Not enough out-of-sample forecasts yet"
    if t >= 2:
        return "Beats the baseline out-of-sample"
    if t <= -2:
        return "WORSE than the baseline - ignore it"
    return "No better than the baseline"


def forecast_index(df: pd.DataFrame, factors: dict | None = None, start: int = 1000, step: int = 21,
                   cost: float = 0.0003) -> dict:
    """Full honest evaluation + tomorrow's forecast for one index."""
    feats = index_features(df, factors)
    tg = targets(df)
    out: dict = {"asof": df.index[-1]}

    # ---- direction
    p = walk_forward(feats, tg["up_next"], DIR_FEATURES, fit_logistic, predict_logistic, start, step)
    base = tg["up_next"].expanding().mean().shift(1)            # past share of up days, known at t
    ev = pd.DataFrame({"p": p, "base": base, "y": tg["up_next"], "ret": tg["ret_next"]}).dropna()
    brier_m, brier_b = (ev["p"] - ev["y"]) ** 2, (ev["base"] - ev["y"]) ** 2
    hit = ((ev["p"] > 0.5) == (ev["y"] == 1)).mean() if len(ev) else np.nan
    hit_base = ((ev["base"] > 0.5) == (ev["y"] == 1)).mean() if len(ev) else np.nan
    pos = (ev["p"] > 0.5).astype(float)                          # trade it: long if p > 0.5, else flat
    strat = pos * ev["ret"] - pos.diff().abs().fillna(pos.iloc[0] if len(pos) else 0) * cost
    dm = dm_test(brier_m, brier_b)
    out["direction"] = {
        "n": len(ev), "hit_rate": hit, "baseline_hit_rate": hit_base, "brier": brier_m.mean(),
        "baseline_brier": brier_b.mean(), "skill": 1 - brier_m.mean() / brier_b.mean() if len(ev) else np.nan,
        "dm_t": dm, "verdict": _verdict(dm, len(ev)), "trade_t": newey_west_t(strat - ev["ret"]),
        "trade_excess": (strat - ev["ret"]).mean() * 252, "history": ev.assign(brier_m=brier_m, brier_b=brier_b)}
    full = feats[DIR_FEATURES].dropna()
    trainable = full.index[tg.loc[full.index, "up_next"].notna()]
    if len(trainable) >= 250 and len(full):
        m = fit_logistic(full.loc[trainable].to_numpy(float), tg.loc[trainable, "up_next"].to_numpy(float))
        out["direction"]["tomorrow"] = float(predict_logistic(m, full.iloc[[-1]].to_numpy(float))[0])
        out["direction"]["tomorrow_base"] = float(tg["up_next"].mean())

    # ---- move size
    v = walk_forward(feats, tg["abs_next"], VOL_FEATURES, fit_ols, predict_ols, start, step).clip(lower=0)
    evv = pd.DataFrame({"pred": v, "base": feats["a1"].rolling(20).mean(), "y": tg["abs_next"]}).dropna()
    se_m, se_b = (evv["pred"] - evv["y"]) ** 2, (evv["base"] - evv["y"]) ** 2
    dmv = dm_test(se_m, se_b)
    out["size"] = {"n": len(evv), "mae": (evv["pred"] - evv["y"]).abs().mean(), "baseline_mae": (evv["base"] - evv["y"]).abs().mean(),
                   "dm_t": dmv, "verdict": _verdict(dmv, len(evv)),
                   "within_2x": float((evv["y"] <= 2 * evv["pred"]).mean()) if len(evv) else np.nan,
                   "history": evv}
    fv = feats[VOL_FEATURES].dropna()
    tv = fv.index[tg.loc[fv.index, "abs_next"].notna()]
    if len(tv) >= 250 and len(fv):
        mv = fit_ols(fv.loc[tv].to_numpy(float), tg.loc[tv, "abs_next"].to_numpy(float))
        out["size"]["tomorrow"] = float(max(predict_ols(mv, fv.iloc[[-1]].to_numpy(float))[0], 0))
        out["size"]["tomorrow_base"] = float(feats["a1"].tail(20).mean())
    return out


# ---------------------------------------------------------------- stocks: pooled move-size model
def stock_size_forecasts(stocks: pd.DataFrame, symbols: list[str] | None = None, train_share: float = 0.6) -> dict:
    """Pooled HAR on |daily move| across stocks (moves from BSE's own previous close).
    Fit on the first `train_share` of dates, scored on the rest vs each stock's 20-day average |move|;
    then refit on everything for tomorrow's expected move per stock."""
    d = stocks.sort_values(["symbol", "date"]).copy()
    if symbols is not None:
        d = d[d["symbol"].isin(symbols)]
    if d.empty:
        return {"n": 0}
    d["a1"] = (d["close"] / d["prev_close"] - 1).abs()
    d["range"] = np.log(d["high"] / d["low"]).where(d["high"] > d["low"])
    g = d.groupby("symbol")["a1"]
    d["a5"] = g.transform(lambda s: s.rolling(5, min_periods=5).mean())
    d["a22"] = g.transform(lambda s: s.rolling(20, min_periods=15).mean())
    d["y"] = g.shift(-1)
    d["range"] = d["range"].fillna(d["a1"] * 1.5)
    ok = d.dropna(subset=VOL_FEATURES)
    dates = np.sort(ok["date"].unique())
    if len(dates) < 30:
        return {"n": 0, "note": "Need at least 30 days of stock data to score the model."}
    cut = dates[int(len(dates) * train_share)]
    tr = ok[(ok["date"] < cut) & ok["y"].notna()]
    te = ok[(ok["date"] > cut) & ok["y"].notna()]          # strictly after: no overlap with training targets
    m = fit_ols(tr[VOL_FEATURES].to_numpy(float), tr["y"].to_numpy(float))
    pred = np.clip(predict_ols(m, te[VOL_FEATURES].to_numpy(float)), 0, None)
    se_m = pd.Series((pred - te["y"].to_numpy()) ** 2, index=te.index)
    se_b = (te["a22"] - te["y"]) ** 2
    daily = pd.DataFrame({"d": te["date"], "m": se_m, "b": se_b}).groupby("d")[["m", "b"]].mean()
    dm = dm_test(daily["m"], daily["b"], lags=3)
    final = fit_ols(ok[ok["y"].notna()][VOL_FEATURES].to_numpy(float), ok[ok["y"].notna()]["y"].to_numpy(float))
    last = ok.groupby("symbol").tail(1)
    last = last[last["date"] == last["date"].max()]
    tomorrow = pd.Series(np.clip(predict_ols(final, last[VOL_FEATURES].to_numpy(float)), 0, None), index=last["symbol"])
    return {"n": len(te), "days_scored": len(daily), "dm_t": dm,
            "verdict": (_verdict(dm, 1000) if len(daily) >= 30 else
                        f"Not enough out-of-sample days yet ({len(daily)} of 30 needed)"),
            "mae": float(np.mean(np.abs(pred - te["y"]))), "baseline_mae": float((te["a22"] - te["y"]).abs().mean()),
            "tomorrow": tomorrow}


FORECAST_NOTE = ("Forecasts are statistical estimates, not advice. Each one is shown with how it has actually performed "
                 "on unseen data. Direction forecasts for liquid indices are usually no better than a coin flip; "
                 "move-size (volatility) forecasts are typically more reliable and useful for stop-loss and position sizing.")
