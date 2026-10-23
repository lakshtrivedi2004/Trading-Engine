---
id: bear_put_spread
type: Strategy
modules: [M06]
tags: [strategy]
---
# Bear Put Spread (debit)

Moderately bearish (~4-5% fall) - buy ITM put, sell OTM put.

## Formula
- **net_debit**: `premium_higher_put - premium_lower_put`
- **max_loss**: `net_debit`
- **max_profit**: `spread - net_debit`
- **breakeven**: `higher_strike - net_debit`

## Facts
- Volatility matters little early in series; in 2nd half deploy only if IV expected to rise.

## Example
Nifty 7485 - buy 7600 PE 165, sell 7400 PE 73 -> debit 92, max profit 108, BE 7508; position delta -0.276.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[bear_call_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[bull_call_spread]] OPPOSITE_OF →
- [[bear_call_spread]] VARIANT_OF →
