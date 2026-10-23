---
id: ta_checklist
type: Rule
modules: [M02]
tags: [rule]
---
# Grand 6-Point TA Checklist

(1) recognisable candlestick pattern; (2) stop near S&R (long - pattern low near support; short - pattern high near resistance); (3) volume >= 10-day average; (4) Dow Theory context (primary/secondary trend, double/triple/range/flag); (5) indicators (RSI/MACD) confirm -> scale size up, else proceed at reduced size; (6) RRR >= threshold (1.5 for active traders).

## Conditions (machine rules)
- **must_have**: `["candlestick_pattern", "sr_near_stop", "volume_confirm", "rrr_ok"]`
- **sr_distance_max**: `abs(stop - sr_level)/stop <= 0.04 else skip`
- **indicator_rule**: `if indicators confirm: size = full (e.g. 500); else size = reduced (e.g. 300)`
- **trade_quality**: `satisfy at least 3-4 checklist points; prefer stock with more points over prettier pattern`

## Facts
- Discipline is ~80% of trading success; checklist enforces it.
- Engulfing preferred over piercing/dark cloud when checklist scores tie.
- Choosing not to trade is itself a big trading decision.

## Bot use
Encode as a scoring function; hard filters for 1,2,3,6 and size multiplier for 5.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[candlestick_rules]]
- USES → [[support_resistance]]
- USES → [[volume]]
- USES → [[dow_theory]]
- USES → [[rsi]]
- USES → [[macd]]
- USES → [[reward_risk_ratio]]
- RELATED_TO → [[swing_scan_process]]

## Backlinks
- [[technical_analysis]] USES →
- [[support_resistance]] PART_OF →
- [[volume]] PART_OF →
- [[rsi]] PART_OF →
- [[macd]] PART_OF →
- [[fibonacci_retracement]] CONFIRMS →
- [[dow_theory]] PART_OF →
- [[reward_risk_ratio]] PART_OF →
- [[swing_scan_process]] USES →
