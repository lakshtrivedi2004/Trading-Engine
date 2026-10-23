---
id: alligator
type: Indicator
modules: [M02]
tags: [indicator]
---
# Alligator (Bill Williams)

Three displaced SMAs - jaw 13, teeth 8, lips 5.

## Conditions (machine rules)
- **buy**: `close > SMA5 > SMA8 > SMA13 and lines separated`
- **sell**: `close < SMA5 < SMA8 < SMA13 and lines separated`
- **no_trade**: `lines intertwined/flat`

## Links
- COVERED_IN → [[module_m02]]
- USES → [[moving_average]]
