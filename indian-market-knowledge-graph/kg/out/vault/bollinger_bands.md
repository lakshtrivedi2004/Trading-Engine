---
id: bollinger_bands
type: Indicator
modules: [M02]
aliases: ["BB"]
tags: [indicator]
---
# Bollinger Bands

John Bollinger's 20 SMA with +/-2 standard deviation bands; mean-reversion tool.

## Formula
- **middle**: `SMA(close,20)`
- **upper**: `middle + 2*stdev(close,20)`
- **lower**: `middle - 2*stdev(close,20)`

## Conditions (machine rules)
- **short**: `price touches upper band -> target middle`
- **long**: `price touches lower band -> target middle`
- **failure**: `envelope expansion (bands widening as price rides a band) = trending market, signals fail`

## Facts
- Works in sideways markets
- fails in trends. Expect the trade to work almost immediately; if not
- suspect envelope expansion.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[standard_deviation]]
- RELATED_TO → [[mean_reversion]]
- RELATED_TO → [[atr_bands]]
