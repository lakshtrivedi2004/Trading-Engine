---
id: expected_return
type: Formula
modules: [M09]
tags: [formula]
---
# Expected Portfolio Return

Weighted sum of asset expected returns; a probabilistic expectation, not a guarantee. Annualise daily mean x 252.

## Formula
- **portfolio_er**: `sum(w_i * R_i)`
- **annual_from_daily**: `mean_daily_return * 252`

## Example
50% Infy (20%) + 50% RIL (15%) -> 17.5%.

## Links
- COVERED_IN → [[module_m09]]
- RELATED_TO → [[portfolio_variance]]

## Backlinks
- [[portfolio_optimization]] USES →
