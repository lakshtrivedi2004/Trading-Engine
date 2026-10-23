---
id: breakout
type: Strategy
modules: [M02]
tags: [strategy]
---
# Range Breakout / Breakdown

Breakout of a range often starts a new trend; true breakouts have high volume and high momentum; false breakouts have low volume and fall back into range.

## Conditions (machine rules)
- **entry_long**: `close > range_high with volume > avg volume`
- **stop_long**: `range_high (the broken resistance)`
- **entry_short**: `close < range_low with high volume`
- **stop_short**: `range_low`
- **min_target**: `breakout_level +/- range_width`

## Example
Range 125/128-165/168; long 170 SL 165; target 168 + 43 = 211.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[trading_range]]
- USES → [[volume]]

## Backlinks
- [[week52_high_low]] RELATED_TO →
- [[volume]] CONFIRMS →
- [[trading_range]] RELATED_TO →
