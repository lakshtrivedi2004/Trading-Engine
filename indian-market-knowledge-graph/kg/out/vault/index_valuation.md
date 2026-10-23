---
id: index_valuation
type: Rule
modules: [M03]
tags: [rule]
---
# Nifty Index Valuation (P/E bands)

Nifty P/E historically 16-20x (avg ~18x); peaked 28x (Jan 2008, pre-crash), troughed ~11x (late 2008/09).

## Conditions (machine rules)
- **caution**: `nifty_pe >= 22`
- **attractive**: `nifty_pe <= 16`

## Facts
- NSE publishes daily Nifty P/E
- P/B
- dividend yield (~6 PM).

## Caveats
- Bands are from source era (2014); P/E methodology changed to consolidated earnings in 2021 - recalibrate.

## Bot use
Macro valuation regime filter for long-only equity allocation / leverage.

## Links
- COVERED_IN → [[module_m03]]
- MEASURES → [[market_index]]
- USES → [[pe_ratio]]

## Backlinks
- [[pe_ratio]] RELATED_TO →
- [[momentum_portfolio]] RELATED_TO →
