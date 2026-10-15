---
id: shooting_star
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Shooting Star

Inverted umbrella at top of uptrend - small body near low with upper shadow >= 2x body; bears pushed price back from new high; bearish. Red body slightly more reliable.

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `upper_shadow >= 2*body and lower_shadow small and close ~= low`
- **entry**: `short near close (or next day if red)`
- **stop**: `high of shooting star`

## Example
O 1426 H 1453 L 1410 C 1417 -> short 1417, SL 1453.

## Links
- COVERED_IN → [[module_m02]]
- OPPOSITE_OF → [[hammer]]
