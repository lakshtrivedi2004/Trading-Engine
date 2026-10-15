---
id: open_interest
type: Concept
modules: [M04]
aliases: ["OI"]
tags: [concept]
---
# Open Interest (OI)

Number of outstanding (open) derivative contracts; rises when new positions are created, falls when positions are closed, unchanged on transfers. Continuous across days unlike volume.

## Conditions (machine rules)
- **long_buildup**: `price up & OI up -> bullish`
- **short_buildup**: `price down & OI up -> bearish`
- **short_covering**: `price up & OI down -> shorts covering (bearish trend may end)`
- **long_unwinding**: `price down & OI down -> longs exiting (bullish trend may end)`
- **caution**: `abnormally high OI with rapid price move = euphoria/leverage; small trigger can cause panic`

## Facts
- Sum of signed positions is always zero (zero-sum).
- Higher OI = more liquid market.
- Volume-price for derivatives - price up & volume up = more longs; price down & volume up = more shorts; price up & volume down = short covering; price down & volume down = long unwinding.

## Bot use
Classify each bar as long buildup/short buildup/short covering/long unwinding as a positioning feature.

## Links
- COVERED_IN → [[module_m04]]
- RELATED_TO → [[volume]]

## Backlinks
- [[liquidity]] RELATED_TO →
- [[max_pain]] USES →
- [[put_call_ratio]] USES →
