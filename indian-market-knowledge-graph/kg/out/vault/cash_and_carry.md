---
id: cash_and_carry
type: Strategy
modules: [M04]
tags: [strategy]
---
# Cash & Carry Arbitrage

When futures trade far above fair value, buy spot and sell futures to lock the spread; unwind near expiry as prices converge.

## Conditions (machine rules)
- **entry**: `futures_market - fair_value >> costs`
- **legs**: `long spot, short futures (equal quantity)`
- **exit**: `just before expiry (sell spot, buy futures)`

## Example
Wipro spot 653, fair futures 658, market 700 -> locked +47 regardless of expiry price.

## Links
- COVERED_IN → [[module_m04]]
- USES → [[futures_pricing]]
- IS_A → [[arbitrage]]

## Backlinks
- [[futures_pricing]] RELATED_TO →
- [[arbitrage]] RELATED_TO →
- [[arbitrage_fund]] USES →
