---
id: gaps
type: Concept
modules: [M02]
tags: [concept]
---
# Gap Up / Gap Down

Gap up = open above prior close (buyer enthusiasm, bullish); gap down = open below prior close (bearish); no trades occur in the gap.

## Conditions (machine rules)
- **gap_up**: `open > prev_close (strict: open > prev_high)`
- **gap_down**: `open < prev_close`

## Links
- COVERED_IN → [[module_m02]]
- PART_OF → [[morning_star]]
- PART_OF → [[evening_star]]

## Backlinks
- [[morning_star]] USES →
- [[overnight_risk]] RELATED_TO →
