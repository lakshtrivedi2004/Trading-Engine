---
id: bear_call_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Bear Call Spread (credit)

Moderately bearish after a rally when call premiums are rich - buy OTM call, sell ITM call.

## Formula
- **net_credit**: `premium_lower_call - premium_higher_call`
- **max_profit**: `net_credit`
- **max_loss**: `spread - net_credit`
- **breakeven**: `lower_strike + net_credit`

## Example
Nifty 7222 - sell 7100 CE 136, buy 7400 CE 38 -> credit 98, max loss 202, BE 7198; delta -0.57.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[bear_put_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[bull_put_spread]] OPPOSITE_OF →
- [[bear_put_spread]] VARIANT_OF →
