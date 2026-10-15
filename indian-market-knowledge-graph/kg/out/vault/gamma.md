---
id: gamma
type: Greek
modules: [M05]
tags: [greek]
---
# Gamma

Rate of change of delta per point of underlying (2nd derivative of premium); always positive for long options. Long options = long gamma; short options = short gamma.

## Formula
- **new_delta**: `delta + gamma * underlying_change`

## Facts
- Gamma peaks at ATM; low for deep ITM/OTM.
- Near expiry ATM gamma shoots up; ITM/OTM gamma -> 0.
- Short gamma makes positions grow against you - a 10-lot short ATM (delta 5) can become delta 8.5 after a 70-pt adverse move.
- Avoid shorting ATM/high-gamma options, especially near expiry; never short ATM/ITM hoping they expire worthless; OTM options are better short candidates if held to expiry.

## Example
8400 CE delta 0.3 gamma 0.0025; +70 pts -> delta 0.475, premium 26 -> 47.

## Bot use
Risk limits should use gamma-adjusted (scenario) delta, not lot counts.

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[theta]]

## Backlinks
- [[option_greeks]] PART_OF →
- [[delta]] RELATED_TO →
- [[sd_option_writing]] RELATED_TO →
- [[greek_interactions]] RELATED_TO →
