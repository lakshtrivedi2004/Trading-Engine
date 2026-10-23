---
id: working_capital_days
type: Formula
modules: [M13]
tags: [formula]
---
# Inventory and Receivable Days

Convert inventory (and receivables) from rupees to days, average the days, then convert back for projections; the days figures also reveal operating efficiency (fewer days = faster conversion to sales).

## Formula
- **inventory_days**: `avg(inventory_{t-1}, inventory_t) / materials_consumed_t * 365`
- **receivable_days**: `avg(receivables_{t-1}, receivables_t) / sales_t * 365`
- **back_to_value**: `inventory_t = inventory_days * materials_consumed_t / 365 (adjusting for averaging)`

## Links
- COVERED_IN → [[module_m13]]
- RELATED_TO → [[operating_ratios]]
- PART_OF → [[model_assumptions]]

## Backlinks
- [[model_assumptions]] USES →
