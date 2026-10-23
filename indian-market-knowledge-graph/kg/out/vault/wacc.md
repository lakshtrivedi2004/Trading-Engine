---
id: wacc
type: Formula
modules: [M13]
tags: [formula]
---
# Weighted Average Cost of Capital (WACC)

A blended discount rate for the whole firm; the cost of equity is always above WACC.

## Formula
- **wacc**: `wd * kd*(1-t) + we * ke`
- **wd**: `D/(D+E)`
- **we**: `E/(D+E)`

## Parameters
- **target_debt_to_equity**: `<= 50% (model default)`
- **tax_rate**: `25% (model default)`

## Example
D 300, E 200, kd 8%, ke 12% -> 9.6% pre-tax; with a 30% tax shield -> 8.16%.

## Links
- COVERED_IN → [[module_m13]]
- USES → [[capm]]
- USES → [[tax_shield]]
- PART_OF → [[dcf]]
- RELATED_TO → [[discount_rate_opportunity_cost]]

## Backlinks
- [[tax_shield]] PART_OF →
- [[capm]] PART_OF →
- [[dcf_model_implementation]] USES →
