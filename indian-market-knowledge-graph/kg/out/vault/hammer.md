---
id: hammer
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Hammer

Paper umbrella at the bottom of a downtrend; bulls rejected new lows; bullish reversal. Longer lower shadow = more bullish; blue body slightly preferred.

## Conditions (machine rules)
- **prior_trend**: `downtrend`
- **identify**: `lower_shadow >= 2*body and abs(close-open)/open within ~1-2% and close near high`
- **entry**: `near close of hammer day (risk taker) or next day close if blue (risk averse)`
- **stop**: `low of hammer`

## Facts
- Author trusts hammer more than hanging man.

## Example
Cipla 15m - risk taker buy 444, risk averse 445.4, SL 441.5.

## Links
- COVERED_IN → [[module_m02]]
- VARIANT_OF → [[paper_umbrella]]
- OPPOSITE_OF → [[hanging_man]]

## Backlinks
- [[paper_umbrella]] RELATED_TO →
- [[shooting_star]] OPPOSITE_OF →
