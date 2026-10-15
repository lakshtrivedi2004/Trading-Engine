---
id: morning_star
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Morning Star (and Morning Doji Star)

Three-candle bottom reversal - long red P1, gap-down doji/spinning top P2, gap-up blue P3 closing above P1's open.

## Conditions (machine rules)
- **prior_trend**: `downtrend`
- **identify**: `P1 red (long); P2 gaps down and is doji/spinning top; P3 gaps up, blue, P3.close > P1.open`
- **entry**: `close of P3 for both risk types`
- **stop**: `lowest low of P1..P3`

## Facts
- If P2 is a doji it is called a morning doji star.

## Links
- COVERED_IN → [[module_m02]]
- OPPOSITE_OF → [[evening_star]]
- USES → [[doji]]
- USES → [[gaps]]

## Backlinks
- [[gaps]] PART_OF →
