"""
Settings for the India Market Research System.
Edit this file to change tickers, date range, or research parameters.
"""

# ---------------------------------------------------------------------------
# Indices to research. Each has a list of Yahoo Finance tickers; the first
# one that returns data is used (fallbacks help when Yahoo renames a ticker).
# ---------------------------------------------------------------------------
INDICES = {
    "Nifty 50":   ["^NSEI"],
    "Sensex":     ["^BSESN"],
    "Bank Nifty": ["^NSEBANK"],
    "Bankex":     ["BSE-BANK.BO", "^BSEBANK"],
}

# ---------------------------------------------------------------------------
# Explanatory factors (the forces research shows move Indian indices).
#   kind:   "ret"  -> percentage change of the level (prices, currencies)
#           "diff" -> change in points (yields, volatility indices)
#   timing: "same_day"     -> trades during Indian market hours
#           "prev_session" -> closes AFTER India closes (US markets), so for
#                             an Indian date we use the last session that
#                             finished BEFORE that date. This avoids
#                             look-ahead bias.
# ---------------------------------------------------------------------------
FACTORS = {
    "S&P 500":      {"tickers": ["^GSPC"],     "kind": "ret",  "timing": "prev_session"},
    "US 10Y yield": {"tickers": ["^TNX"],      "kind": "diff", "timing": "prev_session"},
    "Brent crude":  {"tickers": ["BZ=F"],      "kind": "ret",  "timing": "prev_session"},
    "USD/INR":      {"tickers": ["INR=X"],     "kind": "ret",  "timing": "same_day"},
    "Nikkei 225":   {"tickers": ["^N225"],     "kind": "ret",  "timing": "same_day"},
    "Hang Seng":    {"tickers": ["^HSI"],      "kind": "ret",  "timing": "same_day"},
    "India VIX":    {"tickers": ["^INDIAVIX"], "kind": "diff", "timing": "same_day"},
}

# Which factors explain each index in the daily decomposition.
# Bank indices are explained by the broad market first, then rates and the rupee.
# India VIX is deliberately NOT used as an explainer: it moves *with* prices
# (a symptom, not a cause), so it is reported separately in the regime section.
DECOMPOSITION_FACTORS = {
    "Nifty 50":   ["S&P 500", "Nikkei 225", "Hang Seng", "USD/INR", "Brent crude", "US 10Y yield"],
    "Sensex":     ["S&P 500", "Nikkei 225", "Hang Seng", "USD/INR", "Brent crude", "US 10Y yield"],
    "Bank Nifty": ["Nifty 50", "US 10Y yield", "USD/INR"],
    "Bankex":     ["Sensex", "US 10Y yield", "USD/INR"],
}

# ---------------------------------------------------------------------------
# Research settings
# ---------------------------------------------------------------------------
START_DATE = "2010-01-01"       # history to download
ROLLING_WINDOW = 250            # trading days used to estimate factor sensitivities (~1 year)
TRAIN_FRACTION = 0.70           # first 70% of history = in-sample, last 30% = out-of-sample
COST_PER_SIDE = 0.0003          # 0.03% per buy or sell (index futures/ETF ballpark; adjust to yours)
EVENT_WINDOW = (-5, 10)         # days before / after an event to study
REPORT_DAYS = 10                # recent days shown in the "what moved markets" table

# Data-driven event thresholds
BIG_US_MOVE = 0.02              # S&P 500 overnight move of 2%+
VIX_JUMP = 0.15                 # India VIX up 15%+ in a day
BIG_MOVE_SIGMA = 2.0            # index move larger than 2x its recent daily volatility
