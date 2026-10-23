---
id: put_call_ratio
type: Indicator
modules: [M06]
aliases: ["PCR"]
tags: [indicator]
---
# Put-Call Ratio (PCR)

Total put OI / total call OI; contrarian sentiment indicator.

## Formula
- **pcr**: `sum(put_OI) / sum(call_OI)`

## Conditions (machine rules)
- **extreme_bearish_contrarian_long**: `pcr > ~1.3 (oversold, expect bounce)`
- **extreme_bullish_contrarian_short**: `pcr < ~0.5 (overbought, expect decline)`
- **neutral**: `0.5 <= pcr <= 1`

## Caveats
- Calibrate extremes per instrument from 1-2 years of history; some use volume or traded value. Source's key-takeaway wording ('>1.3 bearish') refers to the crowd's positioning
- interpretation is contrarian.

## Links
- COVERED_IN → [[module_m06]]
- USES → [[open_interest]]
