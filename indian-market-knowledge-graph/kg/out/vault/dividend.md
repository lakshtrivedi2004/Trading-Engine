---
id: dividend
type: Event
modules: [M01]
tags: [event]
---
# Dividend

Distribution of profits (or reserves) per share, expressed as % of face value; interim or final.

## Facts
- Not mandatory; can be paid from reserves even in a loss year.
- Timeline - declaration date (AGM) -> ex-dividend date -> record date (~30 days after declaration); stock is cum-dividend until ex-date.
- Ex-date is set ~2 business days before record date due to T+2; buy before ex-date to be entitled.
- High payout makes sense when growth opportunities are exhausted.

## Bot use
Price drops roughly by dividend amount on ex-date - adjust backtests; dividend arbitrage awareness.

## Links
- COVERED_IN → [[module_m01]]
- PART_OF → [[corporate_actions]]
- USES → [[face_value]]
- RELATED_TO → [[dividend_yield]]

## Backlinks
- [[face_value]] RELATED_TO →
- [[corporate_actions]] RELATED_TO →
- [[reserves_schedule]] RELATED_TO →
