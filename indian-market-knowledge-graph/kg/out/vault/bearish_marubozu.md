---
id: bearish_marubozu
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bearish Marubozu

Long red candle with no shadows (open = high, close = low); extreme selling; bearish regardless of prior trend.

## Conditions (machine rules)
- **identify**: `close < open and abs(high - open) <= tol*high and abs(close - low) <= tol*high`
- **entry**: `short near close (or next day close if red)`
- **stop**: `high of marubozu`

## Example
BPCL O 355.4 H 356 L 341 C 341.7 -> short 341.7, SL 356.

## Links
- COVERED_IN → [[module_m02]]

## Backlinks
- [[bullish_marubozu]] OPPOSITE_OF →
