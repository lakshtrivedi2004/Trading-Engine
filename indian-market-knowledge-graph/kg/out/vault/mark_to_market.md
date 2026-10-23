---
id: mark_to_market
type: Process
modules: [M04]
aliases: ["M2M", "MTM"]
tags: [process]
---
# Mark to Market (M2M)

Daily settlement of futures P&L using previous close as reference; reference price resets to each day's close.

## Formula
- **daily_m2m**: `(close_t - ref_price) * qty * direction, ref = entry on day 1 else close_{t-1}`

## Facts
- Sum of daily M2M = total P&L. M2M plus margins virtually eliminate counterparty default.

## Example
Hindalco long 165, closes 168.3/172.4/171.6, exit 170.1 -> M2M +6600 +8200 -1600 -3000 = +10,200.

## Links
- COVERED_IN → [[module_m04]]
- RELATED_TO → [[clearing_corporation]]

## Backlinks
- [[futures_contract]] USES →
- [[margin]] RELATED_TO →
