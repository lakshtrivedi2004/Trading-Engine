---
id: evening_star
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Evening Star (and Evening Doji Star)

Three-candle top reversal - long blue P1, gap-up doji/spinning top P2, gap-down red P3 closing below P1's open.

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `P1 blue; P2 gaps up, doji/spinning top; P3 gaps down, red, P3.close < P1.open`
- **entry**: `short at close of P3`
- **stop**: `highest high of P1..P3`

## Links
- COVERED_IN → [[module_m02]]

## Backlinks
- [[gaps]] PART_OF →
- [[morning_star]] OPPOSITE_OF →
