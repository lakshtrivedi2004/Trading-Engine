---
id: piercing_pattern
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Piercing Pattern

Like bullish engulfing but P2's blue body covers 50% to <100% of P1's red body.

## Conditions (machine rules)
- **prior_trend**: `downtrend`
- **identify**: `P1 red, P2 blue, 0.5*P1.body <= (P2.close - P1.close) < P1.body`
- **stop**: `pattern low`

## Links
- COVERED_IN → [[module_m02]]
- VARIANT_OF → [[bullish_engulfing]]
- OPPOSITE_OF → [[dark_cloud_cover]]

## Backlinks
- [[bullish_engulfing]] VARIANT_OF →
