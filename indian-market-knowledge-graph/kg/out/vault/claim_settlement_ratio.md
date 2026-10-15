---
id: claim_settlement_ratio
type: Ratio
modules: [M14]
tags: [ratio]
---
# Claim Settlement Ratio (CSR) and Incurred Claims Ratio (ICR)

CSR = % of claims settled; it can be gamed (clearing last year's backlog, settling many small claims while rejecting large ones, combining motor/health for general insurers). Read it alongside ICR and scale (large customer base). Get the numbers from the IRDAI annual report.

## Formula
- **csr**: `claims_settled / (claims_booked + outstanding_start - outstanding_end)`
- **icr**: `claims_paid / net_premiums_collected`

## Conditions (machine rules)
- **icr_high**: `>100% -> insurer losing money (regulator may force merger)`
- **icr_low**: `~50% -> may be stingy on large claims`

## Links
- COVERED_IN → [[module_m14]]
- MEASURES → [[health_insurance]]
