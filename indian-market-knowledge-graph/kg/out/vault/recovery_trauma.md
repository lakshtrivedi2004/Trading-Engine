---
id: recovery_trauma
type: Formula
modules: [M09]
tags: [formula]
---
# Recovery Trauma (drawdown recovery math)

Gains needed to recover a loss grow non-linearly - small accounts that take big bets suffer most. Stay in the game ('if you lose all your chips you can't bet' - Larry Hite).

## Formula
- **required_gain**: `1/(1 - loss) - 1`

## Example
-5% needs +5.3%; -10% needs +11.1%; -50% needs +100%; -60% needs +150%.

## Bot use
Hard max-drawdown circuit breakers; reduce size during drawdowns.

## Links
- COVERED_IN → [[module_m09]]
- RELATED_TO → [[position_sizing]]
- RELATED_TO → [[hedging]]
