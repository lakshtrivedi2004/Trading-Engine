---
id: put_option
type: Instrument
modules: [M05]
aliases: ["Long put", "Short put", "PE"]
tags: [instrument]
---
# Put Option (long and short)

Buyer bearish - right to sell at strike; seller bullish/neutral, earns limited premium with large risk.

## Formula
- **intrinsic_value_expiry**: `max(0, strike - spot)`
- **long_put_pnl**: `max(0, strike - spot) - premium_paid`
- **short_put_pnl**: `premium_received - max(0, strike - spot)`
- **breakeven**: `strike - premium`

## Facts
- Whether to buy a call or sell a put when bullish depends on premium attractiveness (pricing/IV).

## Example
Bank Nifty 18400 PE at 315 - breakeven 18085; at 16510 buyer +1575.

## Links
- COVERED_IN → [[module_m05]]
- PART_OF → [[options_payoff_summary]]

## Backlinks
- [[options]] PART_OF →
- [[call_option]] OPPOSITE_OF →
- [[options_payoff_summary]] USES →
