---
id: moving_average
type: Indicator
modules: [M02]
aliases: ["SMA", "EMA", "Simple moving average", "Exponential moving average", "MA"]
tags: [indicator]
---
# Moving Averages (SMA / EMA)

Rolling average of closes; SMA weights equally, EMA weights recent data more and reacts faster (preferred). Lagging trend-following tool.

## Formula
- **sma**: `mean(close[t-n+1..t])`
- **ema**: `ema_t = alpha*close_t + (1-alpha)*ema_{t-1}, alpha = 2/(n+1)`

## Facts
- Bullish when price > EMA; bearish when price < EMA.
- Works brilliantly in trends, whipsaws in sideways markets.
- Take all signals - many small losses, one big winner pays for them; can't pick the winner in advance.

## Links
- COVERED_IN → [[module_m02]]
- RELATED_TO → [[ma_price_crossover_system]]
- RELATED_TO → [[ma_crossover_system]]
- RELATED_TO → [[macd]]

## Backlinks
- [[ma_price_crossover_system]] USES →
- [[ma_crossover_system]] USES →
- [[leading_lagging_indicators]] RELATED_TO →
- [[macd]] COMPUTED_FROM →
- [[alligator]] USES →
