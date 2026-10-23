---
id: prior_trend
type: Rule
modules: [M02]
tags: [rule]
---
# Prior Trend Requirement

Reversal patterns are valid only after an opposing trend; a perfect hammer without a prior downtrend is not a pattern.

## Conditions (machine rules)
- **prior_downtrend_proxy**: `close[t-1] < close[t-1-n] (e.g. n=5..10) or close below falling short EMA`
- **prior_uptrend_proxy**: `close[t-1] > close[t-1-n] or close above rising short EMA`

## Caveats
- Source defines prior trend visually; the proxies are implementation choices.

## Links
- COVERED_IN → [[module_m02]]
- PART_OF → [[candlestick_rules]]
- USES → [[trend]]

## Backlinks
- [[candlestick_rules]] USES →
