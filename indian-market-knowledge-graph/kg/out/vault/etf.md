---
id: etf
type: Instrument
modules: [M11]
aliases: ["iNAV", "Creation unit", "Tracking error"]
tags: [instrument]
---
# Exchange Traded Funds (ETFs)

Exchange-traded index/commodity/debt wrappers with creation/redemption by market makers/APs; liquidity layers - screen depth, market makers, primary creation, underlying stocks (illiquid underlyings -> premiums/discounts).

## Conditions (machine rules)
- **always**: `use limit orders; compare price with iNAV/underlying index; avoid first 30-60 min after open; check average volumes and tracking vs TRI; prefer AMCs serious about ETFs; create units with AMC for large tickets`

## Facts
- ETFs suit tactical/active use of passive exposure; index funds suit set-and-forget.

## Bot use
Limit-order execution guard - never market-order an ETF; reject if price deviates from iNAV beyond threshold.

## Links
- COVERED_IN → [[module_m11]]
- RELATED_TO → [[order_types]]
- RELATED_TO → [[liquidity]]

## Backlinks
- [[index_funds]] RELATED_TO →
