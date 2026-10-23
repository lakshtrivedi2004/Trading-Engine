---
id: interest_rate_parity
type: Formula
modules: [M08]
tags: [formula]
---
# Interest Rate Parity, Forward Premia and the 'Fairy' Carry Trade

Borrowing in a low-rate currency to invest in a high-rate one (carry trade) is neutralised by the forward rate; the higher-yielding currency trades at a forward discount.

## Formula
- **forward**: `S * (1 + r_quote * N) / (1 + r_base * N)`
- **approx**: `S * (1 + (r_quote - r_base))`

## Example
USDINR 67, INR 7%, USD 0.5% -> 1-yr forward ~71.33.

## Links
- COVERED_IN → [[module_m08]]
- RELATED_TO → [[futures_pricing]]

## Backlinks
- [[currency_drivers]] RELATED_TO →
