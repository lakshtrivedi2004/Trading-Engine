---
id: bearish_harami
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bearish Harami

Top reversal - long blue P1 then small red P2 inside P1's body.

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `P1 long blue; P2 red; P2.open < P1.close; P2.close > P1.open`
- **entry**: `short at close of P2 or next day if red`
- **stop**: `max(P1.high, P2.high)`

## Example
IDFC P1 O124 H129 L122 C127; P2 O126.9 H129.7 L125 C124.8 -> short ~125, SL 129.7.

## Links
- COVERED_IN → [[module_m02]]

## Backlinks
- [[bullish_harami]] OPPOSITE_OF →
