---
id: volatility_stop_loss
type: Rule
modules: [M05]
tags: [rule]
---
# Volatility-Based Stop Loss

Place stop outside expected noise - entry minus (daily vol x sqrt(holding days)) - instead of a fixed %; re-check RRR with the new stop.

## Formula
- **stop_long**: `entry * (1 - daily_vol * sqrt(holding_days))`

## Example
Airtel long 395, daily vol 1.8%, 5 days -> 4.01% -> stop below 379 (use 375); RRR 32/20 = 1.6 still OK.

## Bot use
Default stop model for swing strategies.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[volatility]]
- VARIANT_OF → [[stop_loss]]
- USES → [[reward_risk_ratio]]
- RELATED_TO → [[atr]]
