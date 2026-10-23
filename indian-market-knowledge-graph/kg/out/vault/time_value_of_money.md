---
id: time_value_of_money
type: Formula
modules: [M03]
tags: [formula]
---
# Time Value of Money (FV, PV, NPV)

Money's value changes over time due to opportunity cost; compounding moves money forward, discounting moves it back.

## Formula
- **fv**: `amount * (1 + r) ** n`
- **pv**: `amount / (1 + r) ** n`
- **npv**: `sum(cf_t / (1 + r) ** t)`

## Example
5000 at 8.5% for 5 yrs = 7518.3; 10,000 in 6 yrs at 8.5% = 6129.5 today; pizza machine 5 lakh/yr x 10 yrs at 8.5% NPV = 32.8 lakh.

## Links
- COVERED_IN → [[module_m03]]
- PREREQUISITE → [[dcf]]

## Backlinks
- [[compounding]] RELATED_TO →
- [[dcf]] USES →
- [[discount_rate_opportunity_cost]] USES →
