---
id: dcf_model_implementation
type: Process
modules: [M13]
tags: [process]
---
# DCF Implementation in the Model

Inputs are Rf (10Y G-sec, ~7%), Rm (10-12%), beta, kd (~10%), tax (25%), target D/E (50%) and terminal g (~inflation). Steps - project FCFF Y6-Y10, compute TV at Y10, discount everything at WACC, sum to EV, subtract debt and add cash to get equity value, divide by shares for the fair price, then compare with the market price and run sensitivities on key assumptions.

## Example
Year-3 FCFF 294.14 at WACC 10.25% -> PV 294.14/1.1025^3 = 219.49 Cr; Bajaj Auto fair value ~Rs 300 in the base case.

## Bot use
fair_value(inputs) -> upside = fair/price - 1; screen for upside > margin_of_safety; run grid sensitivity on g and WACC.

## Links
- COVERED_IN → [[module_m13]]
- VARIANT_OF → [[dcf]]
- USES → [[wacc]]
- USES → [[terminal_value]]
- USES → [[fcff_fcfe]]
- USES → [[enterprise_value]]
- RELATED_TO → [[margin_of_safety]]
