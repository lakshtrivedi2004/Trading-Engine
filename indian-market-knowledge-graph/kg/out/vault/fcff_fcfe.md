---
id: fcff_fcfe
type: Formula
modules: [M13]
tags: [formula]
---
# Free Cash Flow to Firm (FCFF) and to Equity (FCFE)

The firm is financed by debt and equity holders; FCFF is the cash available to both, and FCFE is what remains for equity after debt service. Discount FCFF at WACC and FCFE at the cost of equity.

## Formula
- **fcff_from_pat**: `PAT + D&A + interest*(1-tax) + deferred_tax - delta_working_capital - capex`
- **fcff_from_ebit**: `EBIT*(1-tax) + D&A + deferred_tax - delta_working_capital - capex`
- **fcfe**: `FCFF - interest*(1-tax) - principal_repayment + net_new_borrowing`
- **working_capital**: `current_assets - current_liabilities`

## Links
- COVERED_IN → [[module_m13]]
- VARIANT_OF → [[free_cash_flow]]
- USES → [[tax_shield]]
- PART_OF → [[dcf]]

## Backlinks
- [[dcf_model_implementation]] USES →
