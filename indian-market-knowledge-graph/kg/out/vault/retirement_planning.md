---
id: retirement_planning
type: Model
modules: [M11]
tags: [model]
---
# Retirement Corpus Planning

Inflate today's annual expense for each retirement year (FV at inflation ~5%) and sum to get the corpus; build it via monthly SIPs into equity funds with annual step-ups, each instalment compounding for its remaining months.

## Formula
- **expense_in_year_k**: `annual_expense * (1 + inflation) ** years_from_now`
- **corpus**: `sum over retirement years`
- **sip_fv**: `sum_m installment_m * (1 + r) ** (months_remaining_m / 12)`
- **multi_asset_return**: `sum(weight_i * expected_return_i)`

## Parameters
- **conservative_equity_cagr**: `[0.1, 0.11]`
- **inflation**: `[0.04, 0.05]`

## Example
50k/month today, retire in 25 yrs for 20 yrs at 5% inflation -> ~7.2 Cr; needs ~Rs 20k/month SIP stepped up 10% yearly at 11%.

## Links
- COVERED_IN → [[module_m11]]
- USES → [[inflation]]
- USES → [[sip]]
- RELATED_TO → [[asset_allocation]]

## Backlinks
- [[mf_portfolio_construction]] RELATED_TO →
- [[sip]] RELATED_TO →
