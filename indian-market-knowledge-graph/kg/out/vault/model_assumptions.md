---
id: model_assumptions
type: Process
modules: [M13]
tags: [process]
---
# Assumption Sheet - Ratio/Growth Drivers with Rolling Averages

Express each line item historically as a ratio to a driver (BS items vs gross block; P&L items vs sales) or as a YoY growth rate; project with the rolling average of the last 4 years (Y6 = avg(Y2..Y5), Y7 = avg(Y3..Y6)) or an informed guess. Prefer drivers with a narrow, stable historical range.

## Formula
- **ratio_driver**: `item_t / driver_t (e.g. liabilities / gross_block)`
- **growth_driver**: `item_t / item_{t-1} - 1`
- **projection**: `item_{t+1} = rolling_mean(ratio, 4) * driver_{t+1}`
- **deferred_tax**: `deferred_tax = avg(deferred_tax / depreciation) * depreciation (more stable than growth)`

## Links
- COVERED_IN → [[module_m13]]
- PART_OF → [[financial_model]]
- USES → [[working_capital_days]]

## Backlinks
- [[financial_model]] USES →
- [[working_capital_days]] PART_OF →
