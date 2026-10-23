---
id: paper_umbrella
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Paper Umbrella

Small body near top of range with long lower shadow >= 2x body; called Hammer after a downtrend, Hanging Man after an uptrend.

## Conditions (machine rules)
- **identify**: `lower_shadow >= 2*body and upper_shadow small`

## Example
O 100 H 103 L 94 C 102 - body 2, lower shadow 6.

## Links
- COVERED_IN → [[module_m02]]
- RELATED_TO → [[hammer]]
- RELATED_TO → [[hanging_man]]

## Backlinks
- [[hammer]] VARIANT_OF →
- [[hanging_man]] VARIANT_OF →
