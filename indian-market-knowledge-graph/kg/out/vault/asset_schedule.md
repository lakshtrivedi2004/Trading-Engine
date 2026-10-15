---
id: asset_schedule
type: Process
modules: [M13]
tags: [process]
---
# Asset Schedule (Gross Block, CAPEX, Depreciation, Net Block)

Back out historical capex from changes in gross block; project capex from management guidance (best), historical averages, or a variable method; project depreciation with the proportion method (depreciation/gross block); roll accumulated depreciation forward; the net block flows back to the balance sheet and the depreciation to the P&L.

## Formula
- **capex**: `gross_block_t - gross_block_{t-1} (+ disposals)`
- **depreciation_next**: `(dep_t / gross_block_t) * gross_block_{t+1}`
- **accumulated_dep**: `acc_dep_{t-1} + dep_t`
- **net_block**: `gross_block - accumulated_depreciation`

## Example
Dep 41.71 / GB 538.76 = 7.74%; projected GB 588.77 -> dep 45.58 Cr.

## Links
- COVERED_IN → [[module_m13]]
- USES → [[capex]]
- USES → [[base_rule]]
- PART_OF → [[financial_model]]

## Backlinks
- [[financial_model]] USES →
- [[base_rule]] PART_OF →
