---
id: ma_price_crossover_system
type: Strategy
modules: [M02]
tags: [strategy]
---
# Price vs 50-day EMA System

Long when close crosses above 50 EMA; exit when close crosses below.

## Conditions (machine rules)
- **entry_long**: `close crosses above EMA(close, 50)`
- **exit_long**: `close crosses below EMA(close, 50)`

## Parameters
- **ema**: `50`

## Caveats
- Many marginal/losing signals in sideways markets.

## Example
Ambuja - +22, +4, +50 per share across three trades; middle trade in sideways market.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[moving_average]]
- RELATED_TO → [[ma_crossover_system]]

## Backlinks
- [[moving_average]] RELATED_TO →
