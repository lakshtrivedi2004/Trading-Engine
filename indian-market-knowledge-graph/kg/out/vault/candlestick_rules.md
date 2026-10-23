---
id: candlestick_rules
type: Rule
modules: [M02]
tags: [rule]
---
# Candlestick Trading Rules (3 assumptions)

(1) Buy strength, sell weakness - buy on a blue day, sell on a red day; (2) be flexible but quantify and verify deviations; (3) look for a prior trend - bullish patterns need a prior downtrend, bearish need a prior uptrend.

## Conditions (machine rules)
- **confirmation_long**: `next_day.close > next_day.open`
- **confirmation_short**: `next_day.close < next_day.open`
- **tradable_range**: `0.01 <= (high - low)/close <= 0.10`
- **stop_long**: `min(low over pattern candles)`
- **stop_short**: `max(high over pattern candles)`

## Facts
- Marubozu is the only pattern exempt from the prior-trend rule.
- Each pattern carries built-in entry and stop-loss; targets come from S&R.
- Risk taker enters at ~3:20 PM on the pattern's last day after validating; risk averse enters next day near close only if that day confirms (blue for long, red for short).
- The more days in a pattern, the better to enter on the pattern day itself (stars - enter on P3).
- Long stop = lowest low of the pattern; short stop = highest high of the pattern.
- Avoid trading candles with range < 1% (too subdued) or > 10% (stop too deep).
- Once in a trade, do nothing until target or stop hits (trailing stops allowed).

## Bot use
Implement pattern detectors with tolerance params; require prior trend filter; enter at close of signal bar (aggressive) or next bar close with colour confirmation (conservative).

## Links
- COVERED_IN → [[module_m02]]
- USES → [[candlestick]]
- USES → [[prior_trend]]
- RELATED_TO → [[support_resistance]]

## Backlinks
- [[candlestick]] RELATED_TO →
- [[prior_trend]] PART_OF →
- [[bullish_marubozu]] USES →
- [[ta_checklist]] USES →
