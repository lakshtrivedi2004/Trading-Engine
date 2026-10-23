---
id: put_ratio_back_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Put Ratio Back Spread

Outright bearish - sell 1 ITM put, buy 2 OTM puts (2:1) for net credit; unlimited downside profit, limited profit if market rises; never execute for a net debit.

## Formula
- **net_credit**: `premium_higher_put - 2*premium_lower_put`
- **max_loss**: `spread - net_credit (at lower strike)`
- **lower_breakeven**: `lower_strike - max_loss`
- **upper_breakeven**: `lower_strike + max_loss`

## Example
Nifty 7506 - sell 7500 PE 134, buy 2x 7200 PE 46 -> credit 42, max loss 258, BEs 6942/7458.

## Links
- COVERED_IN → [[module_m06]]

## Backlinks
- [[strategy_selector]] USES →
- [[call_ratio_back_spread]] OPPOSITE_OF →
