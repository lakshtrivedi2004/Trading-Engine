---
id: futures_bull_bear_spread
type: Strategy
modules: [M08]
tags: [strategy]
---
# Futures Bull / Bear Calendar Spread (currency)

Futures bull spread - long near, short far; bear spread - short near, long far; trade when the inter-month spread deviates from its normal level and exit on convergence; backtest the 'normal' spread first; use exchange spread contracts to reduce leg risk.

## Example
USDINR Jul 67.3075 / Aug 67.6900 spread 0.3825 vs believed normal 0.20 -> long Jul, short Aug, exit at <= 0.20.

## Links
- COVERED_IN → [[module_m08]]
- VARIANT_OF → [[calendar_spread]]
