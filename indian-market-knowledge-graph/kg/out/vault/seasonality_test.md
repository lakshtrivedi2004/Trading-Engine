---
id: seasonality_test
type: Model
modules: [M08]
tags: [model]
---
# Seasonality Test (Holt-Winters) on USDINR

Comparing Holt-Winters models with vs without seasonal component (chi-square on errors) found no weekly or monthly seasonality in USDINR - it behaves like a random walk; treat 'it always falls in December' claims as noise.

## Bot use
Validate any calendar/seasonal edge statistically before trading it.

## Links
- COVERED_IN → [[module_m08]]
- RELATED_TO → [[backtesting]]
