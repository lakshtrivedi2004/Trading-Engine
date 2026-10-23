---
id: call_ratio_back_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Call Ratio Back Spread

Outright bullish - sell 1 ITM call, buy 2 OTM calls (strict 2:1), usually for net credit; unlimited upside, small profit if market falls, max loss at higher strike.

## Formula
- **net_credit**: `premium_lower - 2*premium_higher`
- **max_loss**: `spread - net_credit (at higher strike)`
- **downside_payoff**: `net_credit`
- **lower_breakeven**: `lower_strike + net_credit`
- **upper_breakeven**: `higher_strike + max_loss`

## Conditions (machine rules)
- **strikes**: `slightly ITM short + slightly OTM long when time is ample; in 2nd half use deep ITM short + slightly ITM long`
- **volatility**: `rising IV helps with ample time; hurts with few days left; avoid starting when IV > ~2x normal`

## Example
Nifty 7743 - sell 7600 CE 201, buy 2x 7800 CE 78 -> credit 45, max loss 155 at 7800, BEs 7645/7955.

## Links
- COVERED_IN → [[module_m06]]
- RELATED_TO → [[bear_call_ladder]]
- OPPOSITE_OF → [[put_ratio_back_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[bear_call_ladder]] VARIANT_OF →
