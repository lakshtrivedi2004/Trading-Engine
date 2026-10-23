---
id: supertrend
type: Indicator
modules: [M02]
tags: [indicator]
---
# Supertrend

ATR-based trailing trend line (Kite default period 7, multiplier 3); green below price in uptrends, red above in downtrends.

## Formula
- **basic_upper**: `(high+low)/2 + mult*ATR`
- **basic_lower**: `(high+low)/2 - mult*ATR`

## Conditions (machine rules)
- **buy**: `close crosses above supertrend (turns green)`
- **sell**: `close crosses below supertrend (turns red)`
- **trail**: `hold long until close below green line`

## Parameters
- **period**: `7`
- **multiplier**: `3`
- **suggested_multiplier**: `[3, 4]`

## Facts
- Fewer false signals than MA systems; works best in trending markets. Higher multiplier = fewer signals.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[atr]]
- RELATED_TO → [[trailing_stop]]

## Backlinks
- [[atr]] RELATED_TO →
