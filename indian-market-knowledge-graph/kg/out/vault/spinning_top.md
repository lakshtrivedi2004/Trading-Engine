---
id: spinning_top
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Spinning Top

Small real body with roughly equal upper and lower shadows; colour irrelevant; indecision - bulls and bears both failed.

## Conditions (machine rules)
- **identify**: `body small relative to range and upper_shadow ~= lower_shadow`

## Facts
- In a downtrend - either more selling or reversal (~50/50); test with half quantity, add if reversal confirms.
- In an uptrend - bulls pausing or fatigued; book profit on ~50% of holding.
- Calm before the storm - a move is coming, direction uncertain; reduce position size.

## Bot use
Treat as volatility-expansion/indecision flag; reduce size or scale out; not an entry signal alone.

## Links
- COVERED_IN → [[module_m02]]
- RELATED_TO → [[doji]]
