---
id: pair_trading_ratio_method
type: Strategy
modules: [M10]
tags: [strategy]
---
# Pair Trading Method 1 - Ratio z-score / Density Curve (Mark Whistler)

Track ratio = price_A / price_B; compute mean, SD (and SD table up to +/-3), and the cumulative normal 'density curve' value NORM.DIST(ratio, mean, sd, TRUE) daily; trade extremes, exit toward mean.

## Formula
- **ratio**: `price_A / price_B`
- **density**: `Phi((ratio - mean)/sd)`

## Conditions (machine rules)
- **long_pair**: `density in ~[0.003, 0.025] (ratio at -2..-3 SD) -> buy A (numerator), sell B`
- **short_pair**: `density in ~[0.975, 0.997] (+2..+3 SD) -> sell A, buy B`
- **exit**: `ratio back inside 2 SD / toward mean (e.g. density ~0.16-0.84)`
- **density_map**: `0.16=-1SD, 0.84=+1SD, 0.025=-2SD, 0.975=+2SD, 0.003=-3SD, 0.997=+3SD`

## Parameters
- **lookback**: `~2 years daily (496 bars)`

## Caveats
- Correlation of Axis-ICICI only ~0.5 in example; author considers this method a foundation - prefers regression method; consider rupee neutrality.

## Example
Axis/ICICI - 25-Oct-2017 density 0.052 (ratio 1.54) -> buy Axis 473, sell ICICI 305.7; exit 31-Oct at density 0.26 (Axis 523, ICICI 300.1).

## Links
- COVERED_IN → [[module_m10]]
- IS_A → [[pair_trading]]
- USES → [[normal_distribution]]

## Backlinks
- [[pair_trading]] VARIANT_OF →
