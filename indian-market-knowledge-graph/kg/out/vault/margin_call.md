---
id: margin_call
type: Event
modules: [M04]
tags: [event]
---
# Margin Call

When cash balance after M2M falls below SPAN (maintenance) margin, broker demands funds or force-closes positions.

## Conditions (machine rules)
- **trigger**: `cash_balance + m2m < span_margin`

## Example
HDFC Bank 955->880 (-8%) on 1 lot (250) -> M2M -18,750 leaves 14,659 < SPAN 16,500 -> margin call.

## Bot use
Monitor margin utilisation intraday; reduce positions before forced liquidation.

## Links
- COVERED_IN → [[module_m04]]
- USES → [[margin]]

## Backlinks
- [[margin]] RELATED_TO →
