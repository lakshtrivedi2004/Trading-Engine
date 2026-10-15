---
id: futures_contract
type: Instrument
modules: [M04]
aliases: ["Futures", "Stock futures", "Index futures"]
tags: [instrument]
---
# Futures Contract

Exchange-traded, standardised, SEBI-regulated, transferable, time-bound, mostly cash-settled agreement whose price mimics the underlying.

## Formula
- **contract_value**: `lot_size * futures_price`
- **pnl**: `(exit_price - entry_price) * lot_size * lots * direction`

## Facts
- Lot size = minimum tradable quantity; multiples of lot only.
- Margin (a % of contract value) is blocked on entry for both buyer and seller.
- Can square off any time before expiry; squaring off transfers risk to a counterparty.
- P&L credited/debited the same day (M2M).
- Zero-sum game - money transfers, not created.
- Linear payoff instrument.
- Indian equity derivatives expired on the last Thursday of the month (per source; expiry days have since changed - verify).
- Unclosed positions are auto-settled at spot on expiry as futures and spot converge.

## Bot use
Express short-term directional views with leverage; trade shorts overnight; hedge portfolios.

## Links
- COVERED_IN → [[module_m04]]
- USES → [[leverage]]
- USES → [[margin]]
- USES → [[mark_to_market]]
- PART_OF → [[expiry]]
- RELATED_TO → [[futures_pricing]]
- REGULATED_BY → [[sebi]]

## Backlinks
- [[short_position]] RELATED_TO →
- [[point_of_view]] RELATED_TO →
- [[derivatives]] RELATED_TO →
- [[forward_contract]] VARIANT_OF →
- [[spot_market]] RELATED_TO →
- [[expiry]] PART_OF →
- [[short_selling_mechanics]] USES →
- [[synthetic_long]] RELATED_TO →
- [[mcx_commodity_futures]] IS_A →
