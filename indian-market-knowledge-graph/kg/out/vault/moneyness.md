---
id: moneyness
type: Concept
modules: [M05]
aliases: ["ITM", "ATM", "OTM", "In the money", "Out of the money", "At the money", "Deep ITM", "Deep OTM"]
tags: [concept]
---
# Moneyness (ITM / ATM / OTM)

ITM has positive intrinsic value; ATM strike is nearest to spot; OTM has zero intrinsic value. Calls - strikes below ATM are ITM; puts - strikes above ATM are ITM. ITM premiums > OTM premiums.

## Conditions (machine rules)
- **call_itm**: `strike < spot`
- **call_otm**: `strike > spot`
- **put_itm**: `strike > spot`
- **put_otm**: `strike < spot`
- **atm**: `argmin |strike - spot|`

## Facts
- Intrinsic value can never be negative (preserves limited-loss property).

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[delta]]
- RELATED_TO → [[option_premium_decomposition]]

## Backlinks
- [[delta]] USES →
- [[option_premium_decomposition]] USES →
