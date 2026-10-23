---
id: bull_call_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Bull Call Spread (debit)

Moderately bullish - buy ATM (lower) call, sell OTM (higher) call, same expiry/underlying/quantity; caps profit to reduce cost.

## Formula
- **net_debit**: `premium_lower_call - premium_higher_call`
- **max_loss**: `net_debit`
- **max_profit**: `spread - net_debit`
- **breakeven**: `lower_strike + net_debit`

## Conditions (machine rules)
- **strike_first_half**: `target in 5d -> far OTM pair; 15d -> slightly OTM; 25d/expiry -> ATM`
- **strike_second_half**: `1-5d -> far OTM; 10d -> slightly OTM; expiry -> ATM`

## Facts
- Wider spread -> more profit potential but higher breakeven. Higher IV raises cost and lowers profit.

## Example
Nifty 7846 - buy 7800 CE 79, sell 7900 CE 25 -> debit 54, max profit 46, BE 7854.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[bull_put_spread]]
- OPPOSITE_OF → [[bear_put_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[bull_put_spread]] VARIANT_OF →
