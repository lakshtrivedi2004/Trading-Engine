---
id: fa_checklist
type: Rule
modules: [M03]
tags: [rule]
---
# 10-Point Fundamental Checklist

Screen for investable-grade financials.

## Conditions (machine rules)
- **gross_profit_margin**: `> 20%`
- **revenue_and_pat_growth**: `in line with each other; author prefers revenue & PAT CAGR > 15%`
- **eps**: `EPS growth consistent with net profit growth (no dilution)`
- **debt**: `not highly leveraged; track Debt/EBIT trend and interest coverage`
- **inventory**: `manufacturing - inventory growing with PAT; stable/declining inventory days`
- **sales_vs_receivables**: `sales not driven by receivables (receivables % of sales stable/declining)`
- **cfo**: `positive cash flow from operations`
- **roe**: `> 25% (author elsewhere >20%), not debt-driven`
- **business_diversity**: `1-2 simple business lines`
- **subsidiaries**: `few (many subsidiaries can mean siphoning)`

## Bot use
Direct screener rules for a fundamental-filtered universe.

## Links
- COVERED_IN → [[module_m03]]
- USES → [[gross_profit_margin]]
- USES → [[roe]]
- USES → [[debt_to_equity]]
- USES → [[operating_ratios]]
- USES → [[cash_flow_statement]]

## Backlinks
- [[investable_grade_attributes]] RELATED_TO →
- [[gross_profit_margin]] PART_OF →
- [[equity_research_process]] USES →
