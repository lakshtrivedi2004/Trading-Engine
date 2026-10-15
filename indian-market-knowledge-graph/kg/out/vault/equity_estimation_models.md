---
id: equity_estimation_models
type: Rule
modules: [M09]
tags: [rule]
---
# Equity Estimation Models (Van Tharp)

Core equity - subtract capital allocated to open trades (conservative); total equity - cash + margins blocked + open P&L (risky, counts unrealised); reduced total equity (author's preference) - core equity plus only locked-in profits (via trailing stops).

## Example
5L capital, 20% rule, ACC long blocks 90k -> next trade 20% of 4.1L = 82k; ACC +50 pts, stop trailed to lock 10k -> 20% of 4.2L = 84k.

## Links
- COVERED_IN → [[module_m09]]
- PART_OF → [[position_sizing]]
- USES → [[trailing_stop]]

## Backlinks
- [[position_sizing]] USES →
