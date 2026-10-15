---
id: financial_model
type: Model
modules: [M13]
tags: [model]
---
# Integrated Financial Model

A linked workbook that takes financial statements as input and outputs a valuation (fair share price); fully integrated, so changing any assumption flows through P&L, balance sheet, cash flow and DCF. Modelling is part art (assumptions) and part craft (structure).

## Facts
- Steps - layout -> historical data (5 yrs, consolidated, from the annual report only) -> assumption sheet -> revenue model -> schedules (asset, debt, reserves) -> P&L/BS projections -> cash flow -> valuation.
- Projection horizon - 5 explicit years (Y6-Y10), then terminal value.
- Pick a simple manufacturing company with 1-2 products and consistent disclosure for a first model; avoid banks/NBFC/BFSI (the model doesn't fit them).
- Sensitivity example - materials consumed 65% -> 60% of sales moved Bajaj Auto fair value from Rs 300 to Rs 462.

## Bot use
Generates a fair value per share and lets you run sensitivity scenarios; feeds margin-of-safety screens and long-horizon value bots. Recompute after every annual/quarterly report.

## Links
- COVERED_IN → [[module_m13]]
- USES → [[annual_report]]
- USES → [[model_assumptions]]
- USES → [[revenue_model]]
- USES → [[asset_schedule]]
- USES → [[debt_schedule]]
- USES → [[reserves_schedule]]
- USES → [[cash_flow_statement]]
- USES → [[dcf]]
- PART_OF → [[fundamental_analysis]]

## Backlinks
- [[model_layout_hygiene]] PART_OF →
- [[model_assumptions]] PART_OF →
- [[revenue_model]] PART_OF →
- [[asset_schedule]] PART_OF →
- [[debt_schedule]] PART_OF →
- [[reserves_schedule]] PART_OF →
- [[indirect_cash_flow]] PART_OF →
