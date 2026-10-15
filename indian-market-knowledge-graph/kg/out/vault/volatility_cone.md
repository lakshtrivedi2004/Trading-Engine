---
id: volatility_cone
type: Model
modules: [M05]
tags: [model]
---
# Volatility Cone

Plot min/-2SD/-1SD/mean/+1SD/+2SD/max of historical realised vol for different windows to expiry (10..90 days); overlay current IVs to spot rich (near +2SD -> short) or cheap (near -2SD -> buy) options.

## Conditions (machine rules)
- **short_vol**: `current IV >= +2SD of realised vol for same days-to-expiry`
- **long_vol**: `current IV <= -2SD`

## Caveats
- Use only for liquid options.

## Bot use
Relative-value IV signal across strikes and underlyings.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[implied_volatility]]

## Backlinks
- [[implied_volatility]] RELATED_TO →
