---
id: super_top_up
type: Product
modules: [M14]
tags: [product]
---
# Super Top-up Health Plan

Very cheap large cover (e.g. Rs 50 lakh for ~Rs 1,000s/yr) that pays once the aggregate bills within a policy year cross the deductible. Choose the deductible equal to the existing base or employer cover, and align policy-year dates with that cover, otherwise the deductible must be met again in a new term.

## Formula
- **payout**: `max(0, sum(claims_in_policy_year) - deductible), capped at cover`

## Conditions (machine rules)
- **deductible**: `= base/employer cover`
- **dates**: `super top-up renewal aligned with base/employer policy year`

## Example
Deductible Rs 5L, bills 7L then 3L in the same year -> super top-up pays 2L + 3L; a plain top-up pays 2L + 0.

## Links
- COVERED_IN → [[module_m14]]
- VARIANT_OF → [[top_up]]

## Backlinks
- [[cover_amount_sizing]] RELATED_TO →
- [[group_health_insurance]] RELATED_TO →
- [[insurance_feature_verdicts]] RELATED_TO →
- [[critical_illness_cover]] RELATED_TO →
- [[top_up]] OPPOSITE_OF →
