---
id: impact_cost
type: Formula
modules: [M04]
tags: [formula]
---
# Impact Cost

Round-trip loss (buy at best ask, sell at best bid) as % of mid price; proxy for liquidity and market-order slippage.

## Formula
- **impact_cost**: `(best_ask - best_bid) / ((best_ask + best_bid)/2)`

## Conditions (machine rules)
- **avoid_market_orders**: `impact_cost >> 0.0125%`

## Example
MRF 98.7/38315.6 = 0.3% (illiquid); Nifty futures 1.1/8769.35 = 0.0125% (benchmark).

## Bot use
Slippage model in backtests; liquidity filter for universe.

## Links
- COVERED_IN → [[module_m04]]
- MEASURES → [[liquidity]]
- COMPUTED_FROM → [[bid_ask]]

## Backlinks
- [[bid_ask]] RELATED_TO →
- [[index_futures]] RELATED_TO →
- [[liquidity]] MEASURES →
- [[backtesting]] USES →
