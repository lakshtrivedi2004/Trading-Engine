---
id: order_types
type: Concept
modules: [M01]
aliases: ["Limit order", "Market order", "Stop loss order", "SL-M"]
tags: [concept]
---
# Order Types (Limit, Market, SL, SL-M)

Limit = specific price (may not fill); Market = immediate at available prices (price uncertainty); SL/SL-M = passive stop that activates at trigger price.

## Conditions (machine rules)
- **long_stop**: `{"trigger_price": "> stop_price", "activates_when": "ltp <= trigger_price"}`

## Facts
- Market orders walk the book and fill at multiple prices; dangerous for active traders in fast markets.
- Stop loss is passive until trigger price is crossed; for a long, trigger is set slightly above the SL limit price (e.g. trigger 256, SL 255).

## Bot use
Prefer limit orders to control slippage; place SL/SL-M orders immediately after entry.

## Links
- COVERED_IN → [[module_m01]]
- RELATED_TO → [[stop_loss]]
- RELATED_TO → [[bid_ask]]
- RELATED_TO → [[product_types]]

## Backlinks
- [[trading_terminal]] RELATED_TO →
- [[etf]] RELATED_TO →
