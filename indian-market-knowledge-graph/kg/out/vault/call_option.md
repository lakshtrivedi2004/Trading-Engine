---
id: call_option
type: Instrument
modules: [M05]
aliases: ["Long call", "Short call", "CE"]
tags: [instrument]
---
# Call Option (long and short)

Buyer bullish with limited risk (premium) and unlimited upside; seller expects price not to rise above strike, earns limited premium with unlimited risk and must post futures-like margin.

## Formula
- **intrinsic_value_expiry**: `max(0, spot - strike)`
- **long_call_pnl**: `max(0, spot - strike) - premium_paid`
- **short_call_pnl**: `premium_received - max(0, spot - strike)`
- **breakeven**: `strike + premium`

## Example
Bajaj Auto 2050 CE at 6.35 - breakeven 2056.35; at 2072 buyer +15.65, seller -15.65.

## Links
- COVERED_IN → [[module_m05]]
- OPPOSITE_OF → [[put_option]]
- PART_OF → [[options_payoff_summary]]
- USES → [[margin]]

## Backlinks
- [[options]] PART_OF →
- [[options_payoff_summary]] USES →
- [[valuation_methods]] RELATED_TO →
