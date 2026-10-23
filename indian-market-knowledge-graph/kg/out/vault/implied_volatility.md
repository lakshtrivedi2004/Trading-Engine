---
id: implied_volatility
type: Concept
modules: [M05]
aliases: ["IV", "India VIX", "VIX", "Historical volatility", "Realized volatility", "GARCH"]
tags: [concept]
---
# Volatility Types - Historical, Forecast, Implied, Realised; India VIX

Historical (from past prices), forecast (models such as GARCH(1,1)), implied (market consensus embedded in premium - most valued), realised (actual over the period). India VIX = NSE's 30-day implied vol of Nifty options ('fear index').

## Facts
- Many professionals trade volatility rather than direction.
- VIX rises in steep/panic markets.
- Aug 24 2015 - Nifty -4.9%, VIX +64%, far OTM calls rose 50-80% (low delta + vega).

## Bot use
Regime filter (VIX level/percentile) for buying vs selling options; IV rank for strategy choice.

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[vega]]
- RELATED_TO → [[volatility_smile]]
- RELATED_TO → [[volatility_cone]]

## Backlinks
- [[volatility]] RELATED_TO →
- [[volatility_cone]] USES →
