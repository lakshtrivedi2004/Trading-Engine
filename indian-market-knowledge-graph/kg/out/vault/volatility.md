---
id: volatility
type: Concept
modules: [M05]
tags: [concept]
---
# Volatility (standard deviation of returns)

Volatility = risk = standard deviation of (log) returns, expressed as %; higher SD = wider expected range.

## Formula
- **log_return**: `ln(P_t / P_{t-1})`
- **daily_vol**: `stdev(log_returns)`
- **annual_vol**: `daily_vol * sqrt(252 or 365)`
- **period_vol**: `daily_vol * sqrt(days)`
- **daily_from_annual**: `annual_vol / sqrt(365)`

## Example
Wipro daily vol 1.47% -> annual ~28% (NSE 1.34%/25.5%).

## Links
- COVERED_IN → [[module_m05]]
- USES → [[standard_deviation]]
- RELATED_TO → [[normal_distribution]]
- RELATED_TO → [[implied_volatility]]
- RELATED_TO → [[atr]]

## Backlinks
- [[atr]] MEASURES →
- [[standard_deviation]] RELATED_TO →
- [[normal_distribution]] USES →
- [[volatility_stop_loss]] USES →
