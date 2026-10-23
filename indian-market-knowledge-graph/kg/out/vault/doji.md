---
id: doji
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Doji

Open ~= close (no or wafer-thin body), shadows any length; indecision like spinning top; often appear in clusters.

## Conditions (machine rules)
- **identify**: `abs(close - open) <= small_frac * (high - low)`

## Facts
- A doji following a recognisable pattern (e.g. after a bearish engulfing) amplifies the move - 'panic plus uncertainty'.

## Links
- COVERED_IN → [[module_m02]]
- CONFIRMS → [[bearish_engulfing]]

## Backlinks
- [[spinning_top]] RELATED_TO →
- [[morning_star]] USES →
