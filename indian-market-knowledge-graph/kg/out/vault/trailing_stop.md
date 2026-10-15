---
id: trailing_stop
type: Rule
modules: [M04]
tags: [rule]
---
# Trailing Stop Loss

Move the stop in the trade's favour as price advances to lock in profit and ride momentum beyond the original target.

## Conditions (machine rules)
- **step_rule**: `for every N points of favourable move, raise stop by N (example N=15)`

## Example
Infosys long 2175, SL 2150, target 2220; trailing every 15 pts -> exited 2230 for +55 vs planned +45.

## Bot use
Implement fixed-step, ATR-based or Supertrend trailing stops.

## Links
- COVERED_IN → [[module_m04]]
- VARIANT_OF → [[stop_loss]]

## Backlinks
- [[supertrend]] RELATED_TO →
- [[product_types_fo]] RELATED_TO →
- [[equity_estimation_models]] USES →
