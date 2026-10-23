---
id: volume
type: Concept
modules: [M02]
aliases: ["Volume trend table", "Smart money"]
tags: [concept]
---
# Volume and the Volume-Price Trend Table

Shares traded (one buy + one sell of 100 = volume 100); used to confirm trends; high volume implies institutional 'smart money'.

## Conditions (machine rules)
- **high_volume**: `volume > SMA(volume, 10)`
- **low_volume**: `volume < SMA(volume, 10)`
- **price_up_vol_up**: `bullish`
- **price_up_vol_down**: `caution - weak hands buying / possible bull trap`
- **price_down_vol_up**: `bearish`
- **price_down_vol_down**: `caution - weak hands selling / possible bear trap`

## Facts
- Volume figure in terminals is cumulative for the day. Avoid trading on low-volume days.

## Bot use
Volume filter for entries - require volume >= 10-day average on the signal bar.

## Links
- COVERED_IN → [[module_m02]]
- PART_OF → [[ta_checklist]]
- CONFIRMS → [[breakout]]

## Backlinks
- [[ohlc]] RELATED_TO →
- [[breakout]] USES →
- [[ta_checklist]] USES →
- [[vwap]] USES →
- [[open_interest]] RELATED_TO →
