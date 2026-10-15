---
id: loss_set_off_rules
type: Tax
modules: [M07]
tags: [tax]
---
# Loss Set-off and Carry-forward Rules

Must file on time to carry forward. Non-speculative (F&O) loss - same year vs any income except salary; carried forward 8 years vs non-speculative business income only. Speculative (intraday) loss - only vs speculative gains, carried 4 years. STCL - vs capital gains, 8 years.

## Conditions (machine rules)
- **speculative_loss_vs_fno_gain**: `NOT allowed`
- **fno_loss_vs_speculative_gain**: `allowed (same year)`

## Links
- COVERED_IN → [[module_m07]]
- PART_OF → [[business_income_tax]]
- RELATED_TO → [[tax_loss_harvesting]]

## Backlinks
- [[business_income_tax]] RELATED_TO →
- [[tax_loss_harvesting]] USES →
