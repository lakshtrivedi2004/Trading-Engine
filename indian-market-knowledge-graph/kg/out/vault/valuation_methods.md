---
id: valuation_methods
type: Concept
modules: [M13]
tags: [concept]
---
# Valuation Approaches (Relative, Option-based, Absolute)

Relative - value like identical peers via multiples (fails if the sector is mispriced or the companies differ). Option-based - the firm's value depends on an event (e.g. R&D success), valued like an option (niche, tech). Absolute - intrinsic value from discounted cash flow (enterprise or equity). Decision - market price > fair price means overvalued, < means undervalued.

## Conditions (machine rules)
- **overvalued**: `market_price > fair_price`
- **undervalued**: `market_price < fair_price (buy only with margin_of_safety)`

## Example
Peers at ~10x PAT -> a new peer with PAT 300 should trade ~Rs 3,000.

## Links
- COVERED_IN → [[module_m13]]
- USES → [[pe_ratio]]
- USES → [[dcf]]
- RELATED_TO → [[call_option]]
- RELATED_TO → [[margin_of_safety]]
