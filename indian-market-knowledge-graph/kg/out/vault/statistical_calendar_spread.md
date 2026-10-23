---
id: statistical_calendar_spread
type: Strategy
modules: [M10]
tags: [strategy]
---
# Statistical Futures Calendar Spread

Treat the next-month minus current-month futures price difference as a mean-reverting series (prices already embed carry, dividends etc.); trade +/-1 SD deviations back to the mean.

## Formula
- **spread**: `price_next_month - price_current_month`
- **bands**: `mean +/- 1*sd (200-day continuous futures)`

## Conditions (machine rules)
- **sell_spread**: `spread > mean + 1sd -> sell next month, buy current month`
- **buy_spread**: `spread < mean - 1sd -> buy next month, sell current month`
- **exit**: `spread returns to mean`

## Facts
- Signals cluster near expiry; small P&L so costs must be minimal; directional risk removed so leverage can be higher; backtest per contract and per side.

## Example
SBIN mean 1.227, sd 0.4935 -> bands 0.7335 / 1.7205; 6 short signals all profitable (closed next day), 28 long signals mixed.

## Links
- COVERED_IN → [[module_m10]]
- VARIANT_OF → [[calendar_spread]]
- USES → [[mean_reversion]]

## Backlinks
- [[trading_system]] RELATED_TO →
