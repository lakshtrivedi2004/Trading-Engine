---
id: vwap
type: Indicator
modules: [M02]
aliases: ["VWAP"]
tags: [indicator]
---
# VWAP (Volume Weighted Average Price)

Intraday cumulative volume-weighted typical price; gauges intraday direction and execution quality.

## Formula
- **typical_price**: `(high + low + close)/3`
- **vwap**: `cumsum(typical_price*volume)/cumsum(volume) (reset daily)`

## Conditions (machine rules)
- **intraday_bull**: `price > VWAP`
- **intraday_bear**: `price < VWAP`
- **good_long_fill**: `buy price < VWAP`
- **good_short_fill**: `short price > VWAP`

## Caveats
- Intraday only; ignore jump at 9:15 open; lags price.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[volume]]
- RELATED_TO → [[execution]]
