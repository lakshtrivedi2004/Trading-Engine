---
id: adx
type: Indicator
modules: [M02]
aliases: ["ADX", "DMI"]
tags: [indicator]
---
# ADX / +DI / -DI (Directional Movement)

Wilder's trend-strength system; ADX measures strength (not direction), +DI/-DI give direction.

## Conditions (machine rules)
- **strong_trend**: `ADX > 25`
- **weak_trend**: `ADX < 20`
- **buy**: `ADX >= 25 and +DI crosses above -DI`
- **sell**: `ADX >= 25 and -DI crosses above +DI`
- **stop**: `low (long) / high (short) of signal candle; trade valid until stop breached`

## Parameters
- **period**: `14`

## Bot use
Regime filter - enable trend systems when ADX > 25, mean-reversion when ADX < 20.

## Links
- COVERED_IN → [[module_m02]]
- MEASURES → [[trend]]
