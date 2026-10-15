---
id: swing_scan_process
type: Process
modules: [M02]
tags: [process]
---
# Swing Trade Scanning Process

Universe Nifty 50, EOD charts, 6-12 month look-back (2 yr for S&R); Part 1 shortlist charts with a pattern in last 3-4 candles; Part 2 evaluate 4-5 shortlisted charts against checklist; at most 1-2 qualify.

## Facts
- Evaluation order: pattern strength -> prior trend -> volume >= 10-day avg -> S&R within 4% of stop -> Dow patterns & primary/secondary trend -> RRR >= 1.5 (target = S&R) -> MACD/RSI for sizing.
- Positional trades may be squared off intraday if target hits same day.
- Opportunity universe criteria - liquid (tight bid-ask, e.g. >= 5 lakh shares/day), EQ segment, not operator-driven; index stocks satisfy these.

## Bot use
Direct blueprint for an EOD scanner pipeline.

## Links
- COVERED_IN → [[module_m02]]
- USES → [[ta_checklist]]
- USES → [[opportunity_universe]]

## Backlinks
- [[ta_checklist]] RELATED_TO →
