---
id: put_call_parity
type: Formula
modules: [M05]
tags: [formula]
---
# Put-Call Parity

For European options of same strike/expiry - put + spot = PV(strike) + call; at expiry put + spot = strike + call.

## Formula
- **pcp**: `P + S = C + K*exp(-r*T)`

## Example
Infy 1200 - at 1100 or 1350 both portfolios (PE + share vs CE + cash) worth the same.

## Bot use
Detect mispricing / synthetic positions (synthetic long = long call + short put).

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[arbitrage]]

## Backlinks
- [[black_scholes]] RELATED_TO →
- [[synthetic_long]] USES →
