---
id: hanging_man
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Hanging Man

Paper umbrella at the top of an uptrend; bears entered intraday; bearish reversal (less reliable than hammer per author because price recovered).

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `lower_shadow >= 2*body`
- **entry**: `short near close (or next day close if red)`
- **stop**: `high of candle`

## Example
BPCL O 592 H 593.75 L 587 C 593 -> short 593, SL above 593.75.

## Links
- COVERED_IN → [[module_m02]]
- VARIANT_OF → [[paper_umbrella]]

## Backlinks
- [[paper_umbrella]] RELATED_TO →
- [[hammer]] OPPOSITE_OF →
