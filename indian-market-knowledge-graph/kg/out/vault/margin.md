---
id: margin
type: Concept
modules: [M04]
aliases: ["SPAN margin", "Exposure margin", "Initial margin", "Maintenance margin"]
tags: [concept]
---
# Margins (Initial, SPAN, Exposure, Maintenance)

Initial margin = SPAN + exposure, blocked while position is open; SPAN (exchange minimum, volatility-driven) acts as maintenance margin; exposure ~4-5% of CV extra cushion.

## Formula
- **initial_margin**: `span + exposure`

## Facts
- Margin varies daily with futures price and volatility (higher expected volatility -> higher SPAN).
- Hedged positions (spreads) get large margin benefit (e.g. Bharat Forge long Jan + short Feb needed ~7.2k vs ~75k).
- Index futures margin ~12-15%; single stocks can be 45-60% (source era).

## Caveats
- Margin rules changed after 2020 SEBI peak-margin regime; intraday leverage limits differ now.

## Links
- COVERED_IN → [[module_m04]]
- RELATED_TO → [[margin_call]]
- RELATED_TO → [[mark_to_market]]

## Backlinks
- [[product_types]] RELATED_TO →
- [[futures_contract]] USES →
- [[leverage]] RELATED_TO →
- [[margin_call]] USES →
- [[product_types_fo]] AFFECTS →
- [[call_option]] USES →
