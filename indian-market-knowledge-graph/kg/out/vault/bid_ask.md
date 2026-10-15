---
id: bid_ask
type: Concept
modules: [M01]
aliases: ["Bid price", "Ask price", "Offer", "Order book depth", "Spread"]
tags: [concept]
---
# Bid, Ask and Market Depth

Bid = best prices buyers will pay; ask/offer = prices sellers demand; depth shows top 5 levels each side.

## Formula
- **spread**: `best_ask - best_bid`
- **mid**: `(best_ask + best_bid)/2`

## Facts
- Lowest ask has highest priority for sellers; highest bid for buyers (price priority).
- A market buy of 10 shares with 2 @ best ask fills across several levels and moves LTP.
- Understanding order stacking is critical for intraday traders.

## Bot use
Estimate slippage by walking the depth for order size; use bid/ask totals as order-flow imbalance feature.

## Links
- COVERED_IN → [[module_m01]]
- MEASURES → [[liquidity]]
- RELATED_TO → [[impact_cost]]

## Backlinks
- [[price_drivers]] RELATED_TO →
- [[trading_terminal]] RELATED_TO →
- [[order_types]] RELATED_TO →
- [[liquidity]] RELATED_TO →
- [[impact_cost]] COMPUTED_FROM →
