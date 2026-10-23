---
id: futures_pricing
type: Formula
modules: [M04]
aliases: ["Fair value", "Basis", "Premium", "Discount", "Contango", "Backwardation", "Cost of carry"]
tags: [formula]
---
# Futures Pricing (Spot-Futures Parity)

Futures fair value = spot adjusted for risk-free carry minus dividends; basis (spread) = futures - spot; converges to zero at expiry.

## Formula
- **fair_value**: `spot * (1 + rf * days_to_expiry/365) - dividend`
- **basis**: `futures - spot`

## Parameters
- **rf_proxy**: `RBI 91-day T-bill yield`

## Facts
- Market price differs slightly from fair value due to costs, taxes, margins, expected dividends; gap widens with days to expiry.
- Futures > spot = premium (contango); futures < spot = discount (backwardation, from short-term demand/supply imbalance).

## Example
Infosys spot 2280.5, rf 8.3528%, 7 days -> 2283 (market 2284); 34 days -> 2299; 80 days -> 2322.

## Links
- COVERED_IN → [[module_m04]]
- RELATED_TO → [[cash_and_carry]]
- RELATED_TO → [[calendar_spread]]
- USES → [[interest_rates]]

## Backlinks
- [[futures_contract]] RELATED_TO →
- [[cash_and_carry]] USES →
- [[calendar_spread]] USES →
- [[interest_rate_parity]] RELATED_TO →
