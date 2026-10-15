---
id: tax_audit
type: Tax
modules: [M07]
tags: [tax]
---
# Business Turnover and Tax Audit (44AB / 44AD)

Audit if trading business turnover > Rs 1 Cr, or (44AD) turnover < 1 Cr but profit < 8% of turnover (unless total income below taxable limit). Trading turnover is not contract value.

## Formula
- **delivery_turnover**: `sell value (only if delivery trades declared as business)`
- **intraday_turnover**: `sum of absolute profits and losses`
- **fno_turnover**: `sum of absolute favourable/unfavourable differences + premium received on option sales`

## Facts
- Trade-wise computation is most compliant; scrip-wise uses average prices.
- Presumptive scheme (ITR-4S, 44AD) - declare 8% of turnover as income to avoid audit (only non-speculative income; no expenses, no loss carry-forward).
- Due dates (source) - July 31 non-audit, Sept 30 audit; belated return within 1 yr of AY end with interest/penalty (Rs 5,000 u/s 271F); revised returns allowed if original filed on time.

## Caveats
- Thresholds changed (e.g. higher audit limits for mostly-digital receipts; ICAI guidance on F&O turnover revised in 2022) - verify.

## Example
Futures buy 8000 sell 7900 x25 -> turnover 2,500; options buy 20 sell 30 x100 -> 1,000 + premium 3,000 = 4,000.

## Links
- COVERED_IN → [[module_m07]]
- RELATED_TO → [[itr_forms]]

## Backlinks
- [[business_income_tax]] RELATED_TO →
- [[trader_books]] RELATED_TO →
