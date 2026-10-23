---
id: momentum_portfolio
type: Strategy
modules: [M10]
tags: [strategy]
---
# Cross-Sectional Momentum Portfolio

From a tracking universe (e.g. BSE 500, or custom filters like mcap >= 1000 Cr; 150-200 stocks for a 12-15 stock portfolio), rank by trailing 12-month return, buy top 10-15 equally weighted on the first trading day of the month, rebalance monthly (sell drop-outs, buy new entrants).

## Formula
- **score**: `close_t / close_{t-252} - 1`
- **rank**: `descending score`

## Conditions (machine rules)
- **universe**: `liquid, clean, corporate-action adjusted`
- **entry**: `top N by 12m return, equal weight`
- **rebalance**: `monthly after close on last trading day`
- **variations**: `1m/2w/1w lookbacks; skewed weights; fundamental momentum (quarterly sales growth, EPS growth, margins) rebalanced quarterly`

## Caveats
- Works in up-trending markets; poor in choppy markets; bleeds more than the market in downtrends (author profited 2009-10
- hit in 2011) - add a market regime filter; backtest first.

## Bot use
Monthly rebalancing bot with index-trend regime filter (e.g. Nifty above 200-DMA).

## Links
- COVERED_IN → [[module_m10]]
- USES → [[momentum]]
- USES → [[backtesting]]
- RELATED_TO → [[index_valuation]]

## Backlinks
- [[trading_system]] RELATED_TO →
- [[momentum]] RELATED_TO →
- [[smart_beta]] RELATED_TO →
