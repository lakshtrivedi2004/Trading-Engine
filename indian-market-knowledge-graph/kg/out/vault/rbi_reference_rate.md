---
id: rbi_reference_rate
type: Process
modules: [M08]
tags: [process]
---
# RBI Reference Rate and Cross Rates

RBI polls randomly selected contributing banks for USDINR two-way quotes daily (11:30-12:30) and averages them; EURINR/GBPINR/JPYINR are derived by crossing through USD. Currency futures settle on the reference rate.

## Formula
- **eurinr_ask**: `usdinr_ask * eurusd_ask`

## Caveats
- Reference rate is now published by FBIL
- not RBI - verify.

## Example
USDINR ask 67.0740 x EURUSD ask 1.1140 = EURINR 74.72.

## Links
- COVERED_IN → [[module_m08]]
- RELATED_TO → [[currency_futures_india]]

## Backlinks
- [[currency_futures_india]] USES →
