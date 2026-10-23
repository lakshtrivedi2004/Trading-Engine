---
id: terminal_value
type: Formula
modules: [M13]
tags: [formula]
---
# Terminal Value (Gordon Growth)

Beyond the 5 explicit years, assume cash flows grow forever at roughly long-term inflation; DCF is extremely sensitive to this number, and it requires stable positive cash flows (DCF fails with negative ones).

## Formula
- **tv**: `CF_final * (1 + g) / (r - g)`
- **pv_factor**: `1 / (1 + r)^t`
- **constraint**: `r > g; g ~ long-term inflation`

## Links
- COVERED_IN → [[module_m13]]
- PART_OF → [[dcf]]
- USES → [[inflation]]

## Backlinks
- [[dcf_model_implementation]] USES →
