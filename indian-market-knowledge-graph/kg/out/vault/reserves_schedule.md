---
id: reserves_schedule
type: Process
modules: [M13]
tags: [process]
---
# Equity / Reserves Schedule

Share capital (authorised >= issued >= subscribed/paid-up; shares = share capital / face value); reserves - securities premium (rises with fresh equity), general reserve, retained earnings (PAT flows in, dividends and DDT flow out). This is the main P&L-to-BS link.

## Formula
- **shares_outstanding**: `paid_up_share_capital / face_value`
- **retained_earnings**: `opening + PAT - dividends - transfers_to_general_reserve`

## Links
- COVERED_IN → [[module_m13]]
- USES → [[base_rule]]
- USES → [[face_value]]
- RELATED_TO → [[dividend]]
- PART_OF → [[financial_model]]

## Backlinks
- [[financial_model]] USES →
- [[base_rule]] PART_OF →
