---
id: bull_put_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Bull Put Spread (credit)

Moderately bullish after a fall when put premiums/IV are high and time is ample - buy OTM put, sell ITM put.

## Formula
- **net_credit**: `premium_higher_put - premium_lower_put`
- **max_profit**: `net_credit`
- **max_loss**: `spread - net_credit`
- **breakeven**: `higher_strike - net_credit`

## Example
Nifty 7805 - buy 7700 PE 72, sell 7900 PE 163 -> credit 91, max loss 109, BE 7809.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[bull_call_spread]]
- OPPOSITE_OF → [[bear_call_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[bull_call_spread]] VARIANT_OF →
