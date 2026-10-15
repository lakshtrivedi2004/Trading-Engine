---
id: base_rule
type: Rule
modules: [M13]
tags: [rule]
---
# Base Rule (Roll-forward)

Every schedule rolls forward - this year's closing balance is next year's opening balance; closing = opening + additions - deletions.

## Formula
- **closing**: `opening + additions - deductions`
- **next_opening**: `closing_t`

## Links
- COVERED_IN → [[module_m13]]
- PART_OF → [[asset_schedule]]
- PART_OF → [[debt_schedule]]
- PART_OF → [[reserves_schedule]]

## Backlinks
- [[asset_schedule]] USES →
- [[debt_schedule]] USES →
- [[reserves_schedule]] USES →
