---
id: bearish_engulfing
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bearish Engulfing

Two-candle top reversal - P1 blue, P2 red whose body engulfs P1's body.

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `P1.close > P1.open and P2.close < P2.open and P2.open >= P1.close and P2.close < P1.open`
- **entry**: `short at close of P2`
- **stop**: `max(P1.high, P2.high)`

## Facts
- A doji after a bearish engulfing is a highly profitable setup per author.

## Example
Ambuja P1 O214 H220 L213.3 C218.75; P2 O220 H221 L207.3 C209.4 -> short 209, SL 221.

## Links
- COVERED_IN → [[module_m02]]
- VARIANT_OF → [[dark_cloud_cover]]

## Backlinks
- [[doji]] CONFIRMS →
- [[bullish_engulfing]] OPPOSITE_OF →
- [[dark_cloud_cover]] VARIANT_OF →
