---
id: calendar_spread
type: Strategy
modules: [M04]
tags: [strategy]
---
# Calendar Spread (Futures)

Buy one expiry and sell another of the same underlying when their spread deviates from fair value; sell the expensive, buy the cheap.

## Conditions (machine rules)
- **entry**: `near-month trades far above fair value while mid-month near fair -> short near, long mid`
- **assumption**: `mid month stays near fair value; near converges to spot at expiry`

## Facts
- Hedged
- so margins are much lower; not entirely risk-free (liquidity
- execution
- volatility).

## Example
Wipro near 700 (fair 658), mid 665 (fair 663) -> short near/long mid locks ~35.

## Links
- COVERED_IN → [[module_m04]]
- USES → [[futures_pricing]]
- USES → [[expiry]]

## Backlinks
- [[expiry]] RELATED_TO →
- [[futures_pricing]] RELATED_TO →
- [[currency_futures_india]] RELATED_TO →
- [[futures_bull_bear_spread]] VARIANT_OF →
- [[statistical_calendar_spread]] VARIANT_OF →
