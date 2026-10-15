---
id: week52_high_low
type: Concept
modules: [M01]
tags: [concept]
---
# 52-Week High / Low and All-Time High / Low

Highest/lowest traded price over last 52 weeks (or since listing).

## Conditions (machine rules)
- **bullish_hint**: `close >= max(high, 252 sessions)`
- **bearish_hint**: `close <= min(low, 252 sessions)`

## Facts
- Many believe a 52-week high indicates a bullish trend ahead and a 52-week low a bearish trend.

## Bot use
Breakout/momentum filter.

## Links
- COVERED_IN → [[module_m01]]
- RELATED_TO → [[breakout]]
