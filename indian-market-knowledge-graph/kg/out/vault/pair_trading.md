---
id: pair_trading
type: Strategy
modules: [M10]
aliases: ["Relative value trading", "Statistical arbitrage", "RVT"]
tags: [strategy]
---
# Pair Trading (Relative Value / Statistical Arbitrage) - concept

Two similar businesses (e.g. HDFC Bank & ICICI Bank) share drivers so their prices move together; a stock-specific 'local event' causes a temporary anomaly - buy the relatively cheap, sell the relatively expensive and profit when the relationship normalises.

## Facts
- Origin - Gerry Bamberger / Nunzio Tartaglia at Morgan Stanley (1980s); DE Shaw adopted it.
- Not truly market-neutral (two different underlyings); a calendar spread is closer to market-neutral.
- Relationship measures - spread (difference of % changes), differential (price difference), ratio (price1/price2); divergence vs convergence trades.
- Qualify pairs with correlation (Whistler method wants > 0.75; compute on returns).
- Margin-hungry; expect 2-3 signals per pair per year - track many pairs.
- Main risk - the pair keeps diverging (relationship breaks).

## Links
- COVERED_IN → [[module_m10]]
- VARIANT_OF → [[pair_trading_ratio_method]]
- VARIANT_OF → [[pair_trading_regression_method]]
- USES → [[correlation]]
- USES → [[mean_reversion]]

## Backlinks
- [[correlation]] RELATED_TO →
- [[silver]] RELATED_TO →
- [[trading_system]] RELATED_TO →
- [[pair_trading_ratio_method]] IS_A →
- [[pair_trading_regression_method]] IS_A →
