---
id: straddle
type: Strategy
modules: [M06]
aliases: ["Long straddle", "Short straddle"]
tags: [strategy]
---
# Long / Short Straddle

Long - buy ATM call + ATM put (delta neutral) to profit from a large move either way; short - sell both to profit from range-bound markets / falling IV.

## Formula
- **long_max_loss**: `net premium paid (at strike)`
- **breakevens**: `strike +/- net premium`
- **short_max_profit**: `net premium received`

## Conditions (machine rules)
- **long_requires**: `low IV at entry, IV rising, large quick move before expiry, event outcome far from expectations`
- **short_requires**: `high IV at entry, IV falling during hold (e.g. sell before events, exit at announcement)`

## Caveats
- Long straddles bought into events often lose due to IV crush; theta hurts longs; deltas drift as market moves - dynamic re-hedging needed to stay neutral.

## Example
Nifty 7579 long 7600 straddle BEs 7435/7765 (2.2% move needed); short straddle 7600 CE 77 + PE 88 = 165 credit.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[strangle]]
- IS_A → [[delta_neutral]]
- USES → [[vega]]

## Backlinks
- [[delta_neutral]] RELATED_TO →
- [[iv_crush]] RELATED_TO →
- [[strategy_selector]] USES →
- [[strangle]] VARIANT_OF →
