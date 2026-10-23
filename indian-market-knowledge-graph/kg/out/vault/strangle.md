---
id: strangle
type: Strategy
modules: [M06]
tags: [strategy]
---
# Long / Short Strangle

Like a straddle with OTM call and put (equidistant, equal lots) - cheaper but wider breakevens; short strangle sells OTM wings to collect premium in ranges.

## Formula
- **long_max_loss**: `net premium paid (between strikes)`
- **upper_breakeven**: `call_strike + net_premium`
- **lower_breakeven**: `put_strike - net_premium`

## Conditions (machine rules)
- **short_strangle_setup**: `stock in trading range (double/triple tops-bottoms) - write strikes outside range, watch for breakouts`

## Example
Nifty 7921 - 7700 PE 28 + 8100 CE 32 = 60; BEs 7640/8160.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[straddle]]
- USES → [[trading_range]]

## Backlinks
- [[strategy_selector]] USES →
- [[straddle]] VARIANT_OF →
- [[systematic_deduction_example]] USES →
