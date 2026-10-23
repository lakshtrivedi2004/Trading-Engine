"""tradekb - executable trading knowledge from the Indian Market Knowledge Graph (Zerodha Varsity M1-M14).

Modules:
  kg          graph access, search, LLM context bundles
  indicators  SMA/EMA/RSI/MACD/Bollinger/ATR/ADX/Aroon/Supertrend/VWAP/Fibonacci/S&R
  candles     Varsity candlestick detectors + trade plans
  options     Black-Scholes, Greeks, IV, SD ranges, max pain, PCR, strategy selector, payoffs
  risk        sizing, Kelly, expectancy, beta hedge, portfolio variance, VaR, Sharpe/Sortino, XIRR
  pairs       ratio & regression pair trading, ADF, pair screening
  strategies  MA/RSI/MACD/BB/ADX/Supertrend signals, calendar spread, momentum portfolio, backtests
  valuation   CAPM, WACC, FCFF, terminal value, DCF, ratios
"""
from .kg import KG

__all__ = ["KG"]
__version__ = "0.1.0"
