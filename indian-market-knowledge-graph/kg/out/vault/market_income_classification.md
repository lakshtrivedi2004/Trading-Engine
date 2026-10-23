---
id: market_income_classification
type: Tax
modules: [M07]
tags: [tax]
---
# Classifying Market Income (Investor vs Trader)

Heads - LTCG, STCG, speculative business income (intraday equity), non-speculative business income (all F&O - equity, currency, commodity - and frequent delivery trading). You can be both investor and trader; keep portfolios demarcated and classification consistent across years.

## Conditions (machine rules)
- **ltcg**: `equity delivery held > 1 year`
- **stcg**: `equity delivery held 1 day to 1 year, low frequency`
- **speculative_business**: `intraday equity`
- **non_speculative_business**: `F&O (any segment) or high-frequency delivery trading (e.g. several times a week)`

## Facts
- CBDT 2007 circular - buy for dividends = investor; buy and sell to profit = trader (vague, AO discretion).
- F&O trading and intraday equity must be declared as business income (losses too) - non-declaration triggers scrutiny.
- If markets are your only income, classify equity trades as business income; salaried people can more easily treat delivery trades as capital gains.
- Intraday is speculative (no delivery intent); F&O is non-speculative by definition (hedging/delivery possible).

## Links
- COVERED_IN → [[module_m07]]
- RELATED_TO → [[capital_gains_tax]]
- RELATED_TO → [[business_income_tax]]

## Backlinks
- [[income_tax_basics]] RELATED_TO →
