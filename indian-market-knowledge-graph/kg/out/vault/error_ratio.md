---
id: error_ratio
type: Formula
modules: [M10]
tags: [formula]
---
# Error Ratio (choose X vs Y)

Regress both ways; pick the orientation with the lower error ratio (keeps intercept - the unhedged part - small).

## Formula
- **error_ratio**: `SE(intercept) / SE(regression)`

## Example
ICICI(x)->HDFC(y) 0.401; HDFC(x)->ICICI(y) 0.227 -> choose HDFC as X.

## Links
- COVERED_IN → [[module_m10]]
- USES → [[linear_regression]]

## Backlinks
- [[linear_regression]] RELATED_TO →
- [[pair_trading_regression_method]] USES →
