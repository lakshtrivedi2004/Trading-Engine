---
id: indirect_cash_flow
type: Process
modules: [M13]
tags: [process]
---
# Indirect Cash Flow and Balance Check

Derive cash flow from P&L and balance-sheet changes - CFO (PAT + depreciation from the asset schedule +/- working-capital changes), CFI (capex, investments), CFF (debt, equity, dividends). Net cash flow plus opening cash gives closing cash, which plugs into the balance sheet; if the model is integrated correctly the balance sheet balances.

## Formula
- **closing_cash**: `opening_cash + CFO + CFI + CFF`
- **check**: `total_assets - total_liabilities_and_equity == 0`

## Links
- COVERED_IN → [[module_m13]]
- RELATED_TO → [[cash_flow_statement]]
- PART_OF → [[financial_model]]
