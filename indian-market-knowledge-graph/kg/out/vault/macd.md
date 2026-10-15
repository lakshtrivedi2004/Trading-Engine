---
id: macd
type: Indicator
modules: [M02]
aliases: ["MACD"]
tags: [indicator]
---
# MACD (Moving Average Convergence Divergence)

Gerald Appel's momentum/trend indicator - MACD line = 12 EMA - 26 EMA; signal line = 9-period EMA of MACD.

## Formula
- **macd_line**: `EMA(close,12) - EMA(close,26)`
- **signal_line**: `EMA(macd_line, 9)`
- **histogram**: `macd_line - signal_line`

## Conditions (machine rules)
- **centerline_bull**: `macd_line crosses above 0`
- **centerline_bear**: `macd_line crosses below 0`
- **signal_bull**: `macd_line crosses above signal_line`
- **signal_bear**: `macd_line crosses below signal_line`

## Parameters
- **fast**: `12`
- **slow**: `26`
- **signal**: `9`

## Facts
- Sign = direction of momentum; magnitude = strength but scales with price level. Works in trends
- poor sideways.

## Links
- COVERED_IN → [[module_m02]]
- COMPUTED_FROM → [[moving_average]]
- PART_OF → [[ta_checklist]]

## Backlinks
- [[moving_average]] RELATED_TO →
- [[ta_checklist]] USES →
