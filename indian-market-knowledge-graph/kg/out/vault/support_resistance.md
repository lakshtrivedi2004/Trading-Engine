---
id: support_resistance
type: Concept
modules: [M02]
aliases: ["Support", "Resistance", "S&R", "Price action zone"]
tags: [concept]
---
# Support and Resistance (S&R)

Price zones expected to attract maximum buying (support, below CMP) or selling (resistance, above CMP); used for targets, entries and stop placement.

## Conditions (machine rules)
- **level_valid**: `count(swing pivots within +/-tol of level) >= 3 and pivots spaced in time`
- **is_support**: `level < cmp`
- **is_resistance**: `level > cmp`

## Facts
- Long trade target = nearest resistance; short trade target = nearest support.
- Construction: (1) load 3-6 months (short-term, for intraday/BTST) or 12-18 months (long-term, for swing) of data; (2) find price action zones - hesitation after up move, hesitation after down move, sharp reversals; (3) find >= 3 zones at the same price, well spaced in time (e.g. >= 2 weeks apart); (4) fit a horizontal line.
- More zones and wider time spacing = stronger level.
- Treat S&R as a zone (e.g. 429 +/- 3), not a point.
- Well-constructed S&R is usually respected but it is probabilistic.
- Accumulation creates support; distribution creates resistance (Dow).

## Example
Ambuja CMP 206, marubozu low 202 -> entry 206, SL 202, target 215 (resistance).

## Bot use
Detect swing pivots, cluster by price with tolerance, keep clusters with >=3 well-spaced touches; use nearest opposite level as target and require stop near a level.

## Links
- COVERED_IN → [[module_m02]]
- PART_OF → [[ta_checklist]]
- USES → [[reward_risk_ratio]]
- RELATED_TO → [[pivot_points]]

## Backlinks
- [[candlestick_rules]] RELATED_TO →
- [[market_phases]] RELATED_TO →
- [[double_top_bottom]] RELATED_TO →
- [[trading_range]] USES →
- [[reward_risk_ratio]] USES →
- [[ta_checklist]] USES →
