---
id: normal_distribution
type: Concept
modules: [M05]
aliases: ["Bell curve", "Random walk", "Black swan"]
tags: [concept]
---
# Normal Distribution and Price Ranges

Daily returns are a random walk but approximately normally distributed; ~68% within 1 SD, 95% within 2 SD, 99.7% within 3 SD; outside 3 SD = black swan.

## Formula
- **period_mean**: `daily_mean * days`
- **period_sd**: `daily_sd * sqrt(days)`
- **upper**: `spot * exp(period_mean + k*period_sd)`
- **lower**: `spot * exp(period_mean - k*period_sd)`

## Caveats
- Real returns have fat tails - black swans happen more often than normal implies.

## Example
Nifty 8337, daily mean 0.04%, SD 1.046% -> 1-yr 1SD range 7777-10841; 30-day 1SD 7963-8930, 2SD 7520-9457.

## Bot use
Expected-move bands for strike selection, stop placement and position risk.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[volatility]]
- RELATED_TO → [[sd_option_writing]]

## Backlinks
- [[volatility]] RELATED_TO →
- [[sd_option_writing]] USES →
- [[value_at_risk]] USES →
- [[mean_reversion]] USES →
- [[pair_trading_ratio_method]] USES →
