---
id: reward_risk_ratio
type: Formula
modules: [M02]
aliases: ["RRR", "Risk reward"]
tags: [formula]
---
# Reward-to-Risk Ratio (RRR)

Expected reward per unit of risk; trades below your RRR threshold must be dropped regardless of setup quality.

## Formula
- **long_rrr**: `(target - entry) / (entry - stop)`
- **short_rrr**: `(entry - target) / (stop - entry)`

## Parameters
- **min_beginner**: `as high as possible`
- **min_active_trader**: `1.5`
- **aggressive**: `1.0`
- **cautious**: `2.0`
- **scalping_ok**: `[0.5, 0.75]`

## Example
Entry 107 SL 102 Target 114 -> RRR 1.4; Entry 55.75 SL 53.55 T 57.2 -> 0.65 reject.

## Links
- COVERED_IN → [[module_m02]]
- PART_OF → [[ta_checklist]]
- USES → [[support_resistance]]
- RELATED_TO → [[expectancy]]

## Backlinks
- [[support_resistance]] USES →
- [[ta_checklist]] USES →
- [[volatility_stop_loss]] USES →
