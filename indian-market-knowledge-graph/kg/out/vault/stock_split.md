---
id: stock_split
type: Event
modules: [M01]
tags: [event]
---
# Stock Split

Face value split (e.g. FV 10 -> 5); shares multiply, price divides, market cap unchanged.

## Formula
- **split_factor**: `old_fv / new_fv`
- **post_price**: `pre_price / split_factor`

## Example
FV 10 -> 2 (1:5) - 100 sh @900 -> 500 sh @180.

## Links
- COVERED_IN → [[module_m01]]
- PART_OF → [[corporate_actions]]
- USES → [[face_value]]

## Backlinks
- [[face_value]] RELATED_TO →
- [[corporate_actions]] RELATED_TO →
- [[bonus_issue]] RELATED_TO →
