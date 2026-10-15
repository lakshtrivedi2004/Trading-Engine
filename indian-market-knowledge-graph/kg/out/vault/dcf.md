---
id: dcf
type: Model
modules: [M03]
aliases: ["DCF", "Intrinsic value"]
tags: [model]
---
# Discounted Cash Flow (DCF) Valuation

Intrinsic value = PV of projected FCF + PV of terminal value, adjusted for net debt, divided by shares.

## Formula
- **base_fcf**: `average FCF of last 3 years (smooths cyclicality)`
- **projection**: `stage 1 (yrs 1-5) growth g1 (e.g. 18%, 15% for large caps), stage 2 (yrs 6-10) g2 (e.g. 10%)`
- **terminal_value**: `FCF_10 * (1 + g_t) / (r - g_t)`
- **pv_terminal**: `TV / (1 + r) ** 10`
- **equity_value**: `sum PV(FCF) + PV(TV) - net_debt`
- **net_debt**: `total_debt - cash`
- **intrinsic_value_per_share**: `equity_value / shares`
- **value_band**: `IV * [0.9, 1.1]`

## Conditions (machine rules)
- **buy**: `price < lower band`
- **hold**: `lower band <= price <= upper band (no fresh buy)`
- **overvalued**: `price > upper band (book profits or hold, never buy)`

## Parameters
- **discount_rate**: `0.09`
- **terminal_growth**: `[0.03, 0.04]`
- **max_fcf_growth**: `0.2`
- **years**: `10`

## Caveats
- Garbage in, garbage out - requires forecasting.
- Highly sensitive to terminal growth (3.5% -> 4% moves value 368 -> 394).
- Needs constant updates; useless for <1 yr horizon.
- Keep assumptions conservative - FCF growth <= 20%, terminal growth <= 4%, 10-year 2-stage.

## Example
ARBL - avg FCF 140.36 Cr, PV FCF 1968 + PV TV 4111 = 6079 Cr; net debt -218.6; /17.081 Cr shares = Rs 368 (band 331-405) vs CMP 726.

## Links
- COVERED_IN → [[module_m03]]
- USES → [[free_cash_flow]]
- USES → [[time_value_of_money]]
- RELATED_TO → [[margin_of_safety]]

## Backlinks
- [[valuation_ratios]] RELATED_TO →
- [[equity_research_process]] USES →
- [[time_value_of_money]] PREREQUISITE →
- [[free_cash_flow]] PART_OF →
- [[margin_of_safety]] USES →
- [[financial_model]] USES →
- [[valuation_methods]] USES →
- [[enterprise_value]] PART_OF →
- [[fcff_fcfe]] PART_OF →
- [[wacc]] PART_OF →
- [[terminal_value]] PART_OF →
- [[dcf_model_implementation]] VARIANT_OF →
