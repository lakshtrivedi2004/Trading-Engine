---
id: option_premium_decomposition
type: Formula
modules: [M05]
tags: [formula]
---
# Premium = Intrinsic Value + Time Value

Premium splits into intrinsic value (exercise value now) and time value (compensation to writer for time risk).

## Formula
- **time_value**: `premium - intrinsic_value`

## Example
Nifty 8531, 8600 CE at 99.4 -> all time value; 8514.5, 8450 CE at 160 -> IV 64.5, TV 95.5.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[moneyness]]

## Backlinks
- [[moneyness]] RELATED_TO →
- [[theta]] RELATED_TO →
