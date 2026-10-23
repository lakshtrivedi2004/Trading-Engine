---
id: candlestick
type: Concept
modules: [M02]
aliases: ["Candle", "Real body", "Shadow", "Wick"]
tags: [concept]
---
# Japanese Candlestick Anatomy

Real body connects open and close; upper shadow to high, lower shadow to low; bullish (blue/green) if close > open, bearish (red) if close < open.

## Formula
- **body**: `abs(close - open)`
- **upper_shadow**: `high - max(open, close)`
- **lower_shadow**: `min(open, close) - low`
- **range**: `high - low`
- **bullish**: `close > open`
- **bearish**: `close < open`

## Facts
- Long body = strong buying/selling; short body = little activity.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[ohlc]]
- RELATED_TO → [[candlestick_rules]]

## Backlinks
- [[ohlc]] RELATED_TO →
- [[technical_analysis]] USES →
- [[chart_types]] RELATED_TO →
- [[candlestick_rules]] USES →
