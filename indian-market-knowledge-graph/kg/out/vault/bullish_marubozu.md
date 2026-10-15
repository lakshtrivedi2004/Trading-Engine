---
id: bullish_marubozu
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bullish Marubozu

Long blue candle with no shadows (open = low, close = high) showing buying at every price; bullish regardless of prior trend.

## Conditions (machine rules)
- **identify**: `close > open and abs(open - low) <= tol*high and abs(high - close) <= tol*high`
- **tol_example**: `ACC 0.17% deviation accepted`
- **entry**: `near close of marubozu (risk taker) or next day close if next day is blue (risk averse)`
- **stop**: `low of marubozu`
- **target**: `nearest resistance`

## Example
ACC O 971.8 H 1030.2 L 970.1 C 1028.4 -> buy ~1028.4, SL 970.

## Links
- COVERED_IN → [[module_m02]]
- OPPOSITE_OF → [[bearish_marubozu]]
- USES → [[candlestick_rules]]
