---
id: cagr
type: Formula
modules: [M01]
aliases: ["CAGR"]
tags: [formula]
---
# Compounded Annual Growth Rate (CAGR)

Annualised growth rate over multiple years.

## Formula
- **cagr**: `(end_value / start_value) ** (1 / years) - 1`

## Conditions (machine rules)
- **use_when**: `holding period > 1 year`

## Facts
- Compare returns only on an annualised basis; 17.16% in 6 months ~ 34.32% annualised (simple).

## Example
3030 -> 3550 over 2 years => 8.2% CAGR (below an 8.5% FD).

## Links
- COVERED_IN → [[module_m01]]
- RELATED_TO → [[compounding]]

## Backlinks
- [[absolute_return]] RELATED_TO →
- [[simple_vs_compound_interest]] RELATED_TO →
- [[return_measurement]] RELATED_TO →
