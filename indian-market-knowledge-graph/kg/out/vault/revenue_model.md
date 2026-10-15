---
id: revenue_model
type: Model
modules: [M13]
tags: [model]
---
# Revenue Sub-model

Build revenue bottom-up from common-sense questions answered in the annual report - what they make, where they sell (domestic vs export), units x realisation per unit, capacity and utilisation. It also yields capacity utilisation, capex cycles and market share.

## Formula
- **revenue**: `sum_segments(units_sold * avg_realisation)`
- **utilisation**: `units_produced / installed_capacity`

## Example
Bajaj Auto - motorcycles vs commercial vehicles, domestic vs export units; revenue ~Rs 29,111 Cr with exports ~Rs 12,216 Cr.

## Links
- COVERED_IN → [[module_m13]]
- PART_OF → [[financial_model]]

## Backlinks
- [[financial_model]] USES →
