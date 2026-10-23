---
id: debt_schedule
type: Process
modules: [M13]
tags: [process]
---
# Debt Schedule

Split debt into secured and unsecured using the notes, average the opening and closing balances to get the interest base, and project debt from management's capex/financing view; with no guidance, hold debt flat.

## Formula
- **interest**: `avg(opening_debt, closing_debt) * interest_rate`

## Links
- COVERED_IN → [[module_m13]]
- USES → [[base_rule]]
- RELATED_TO → [[debt_to_equity]]
- PART_OF → [[financial_model]]

## Backlinks
- [[financial_model]] USES →
- [[base_rule]] PART_OF →
