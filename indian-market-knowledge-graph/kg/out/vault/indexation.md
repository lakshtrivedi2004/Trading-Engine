---
id: indexation
type: Formula
modules: [M07]
tags: [formula]
---
# Indexation (Cost Inflation Index)

Inflates purchase cost by CII to tax only real gains on debt funds, property, gold etc.

## Formula
- **indexed_cost**: `purchase_value * CII_sale_year / CII_purchase_year`
- **ltcg**: `sale_value - indexed_cost`

## Caveats
- Indexation benefit removed for many assets in 2023-24 changes - verify.

## Example
Debt fund 1L (2005, CII 497) -> 3L (2015, CII 1024): indexed cost 2,06,036; LTCG 93,964; tax 18,792 vs 40,000 unindexed.

## Links
- COVERED_IN → [[module_m07]]
- PART_OF → [[capital_gains_tax]]
- RELATED_TO → [[inflation]]

## Backlinks
- [[capital_gains_tax]] USES →
