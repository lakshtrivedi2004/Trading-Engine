---
id: bullish_engulfing
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Bullish Engulfing

Two-candle bottom reversal - P1 red, P2 blue whose real body engulfs P1's real body.

## Conditions (machine rules)
- **prior_trend**: `downtrend`
- **identify**: `P1.close < P1.open and P2.close > P2.open and P2.open <= P1.close and P2.close > P1.open`
- **entry**: `close of P2 (preferred - multi-day pattern) or day after if blue`
- **stop**: `min(P1.low, P2.low)`

## Facts
- Engulfing real bodies is sufficient; shadows need not be engulfed. Preferred over piercing pattern.

## Example
DLF P1 O163 H168 L158.5 C160; P2 O159.5 H170.2 L159 C169 -> buy 169, SL 158.5.

## Links
- COVERED_IN → [[module_m02]]
- OPPOSITE_OF → [[bearish_engulfing]]
- VARIANT_OF → [[piercing_pattern]]

## Backlinks
- [[piercing_pattern]] VARIANT_OF →
