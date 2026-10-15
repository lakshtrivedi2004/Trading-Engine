---
id: opportunity_universe
type: Concept
modules: [M02]
tags: [concept]
---
# Opportunity Universe

Fixed list of liquid stocks you scan daily (e.g. Nifty 50).

## Conditions (machine rules)
- **liquidity**: `avg daily volume >= 500000 or tight bid-ask spread`
- **segment**: `EQ`

## Links
- COVERED_IN → [[module_m02]]
- USES → [[liquidity]]

## Backlinks
- [[swing_scan_process]] USES →
