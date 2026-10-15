---
id: room_rent_cap
type: Concept
modules: [M14]
tags: [concept]
---
# Room Rent Cap / Proportionate Deduction

A cap on room rent (e.g. 1% of sum insured per day). Choosing a costlier room makes the insurer pay proportionately on all room-linked charges (surgeon, consultant, diagnostics), not only the rent difference.

## Formula
- **payable_fraction**: `cap_rent / actual_rent`
- **applied_to**: `room rent + associated charges`

## Conditions (machine rules)
- **prefer**: `no room-rent cap; at least single private room allowed`

## Example
Cap Rs 5,000, room Rs 10,000 -> insurer pays 50%; a Rs 50,000 surgeon fee pays only Rs 25,000.

## Links
- COVERED_IN → [[module_m14]]

## Backlinks
- [[co_payment]] RELATED_TO →
