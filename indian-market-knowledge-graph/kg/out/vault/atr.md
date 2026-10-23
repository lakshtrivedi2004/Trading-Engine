---
id: atr
type: Indicator
modules: [M02]
aliases: ["ATR", "True range"]
tags: [indicator]
---
# Average True Range (ATR)

Wilder's volatility measure including gaps; no direction; in price units.

## Formula
- **true_range**: `max(high-low, abs(high-prev_close), abs(low-prev_close))`
- **atr**: `Wilder smoothing / average of TR over n`

## Conditions (machine rules)
- **expected_range**: `price +/- ATR`
- **volatility_stop_long**: `stop <= entry - ATR`
- **volatility_stop_short**: `stop >= entry + ATR`
- **skip**: `if ATR-based stop violates RRR, skip trade`

## Parameters
- **period**: `14`

## Example
ATR 48 at 1320 -> range 1272-1368; long 1325 stop <= 1272.

## Bot use
Volatility-normalised stops and position sizing.

## Links
- COVERED_IN → [[module_m02]]
- RELATED_TO → [[supertrend]]
- RELATED_TO → [[atr_bands]]
- MEASURES → [[volatility]]
- RELATED_TO → [[position_sizing]]

## Backlinks
- [[atr_bands]] USES →
- [[supertrend]] USES →
- [[volatility]] RELATED_TO →
- [[volatility_stop_loss]] RELATED_TO →
- [[position_sizing]] USES →
