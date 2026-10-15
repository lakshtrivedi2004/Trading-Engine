---
id: bullish_harami
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bullish Harami

Bottom reversal - long red P1 then small blue P2 contained within P1's body ('pregnant'); surprise strength unnerves bears.

## Conditions (machine rules)
- **prior_trend**: `downtrend`
- **identify**: `P1 long red; P2 blue; P2.open > P1.close; P2.close < P1.open`
- **entry**: `close of P2 or next day if blue`
- **stop**: `min(P1.low, P2.low)`

## Example
Axis Bank P1 O868 H874 L810 C815; P2 O824 H847 L818 C835 -> buy 835, SL 810.

## Links
- COVERED_IN → [[module_m02]]
- OPPOSITE_OF → [[bearish_harami]]
