---
id: dark_cloud_cover
type: CandlestickPattern
modules: [M02]
tags: [candlestickpattern]
---
# Dark Cloud Cover

Like bearish engulfing but P2's red body covers 50% to <100% of P1's blue body; trade setup same as bearish engulfing.

## Conditions (machine rules)
- **prior_trend**: `uptrend`
- **identify**: `P1 blue, P2 red, 0.5*P1.body <= (P1.close - P2.close) < P1.body`
- **stop**: `pattern high`

## Links
- COVERED_IN → [[module_m02]]
- VARIANT_OF → [[bearish_engulfing]]

## Backlinks
- [[bearish_engulfing]] VARIANT_OF →
- [[piercing_pattern]] OPPOSITE_OF →
