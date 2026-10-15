---
id: product_types_fo
type: Concept
modules: [M04]
aliases: ["Cover order", "Bracket order", "NRML"]
tags: [concept]
---
# F&O Product Types (NRML, MIS, CO, BO)

More risk information to broker RMS -> lower margin. NRML = carry forward (full SPAN+exposure); MIS = intraday (lower); CO = intraday + mandatory stop (lower still); BO = intraday + stop + target + optional trailing (same margin as CO).

## Facts
- MIS/CO/BO auto-squared off ~3:20 PM. RMS cares about risk not reward
- so target doesn't lower margin.

## Caveats
- Bracket orders were later discontinued by many brokers; check availability.

## Links
- COVERED_IN → [[module_m04]]
- VARIANT_OF → [[product_types]]
- RELATED_TO → [[trailing_stop]]
- AFFECTS → [[margin]]

## Backlinks
- [[functional_fixedness]] RELATED_TO →
