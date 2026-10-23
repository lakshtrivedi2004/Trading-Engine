---
id: ma_crossover_system
type: Strategy
modules: [M02]
tags: [strategy]
---
# Moving Average Crossover System

Long when fast EMA crosses above slow EMA; exit when fast crosses below; fewer whipsaws than single MA.

## Conditions (machine rules)
- **entry_long**: `EMA(fast) crosses above EMA(slow)`
- **exit_long**: `EMA(fast) crosses below EMA(slow)`

## Parameters
- **short_term**: `[9, 21]`
- **medium_term**: `[25, 50]`
- **few_months**: `[50, 100]`
- **long_term**: `[100, 200]`
- **intraday**: `["15m x 30m", "5m x 10m"]`

## Facts
- Longer timeframe combos give fewer signals. 'The trend is your friend'.

## Bot use
Baseline trend-following strategy; combine with ADX/regime filter to avoid sideways whipsaw.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[moving_average]]
- MEASURES → [[trend]]

## Backlinks
- [[moving_average]] RELATED_TO →
- [[ma_price_crossover_system]] RELATED_TO →
