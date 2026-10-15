---
id: pmi
type: Event
modules: [M01]
aliases: ["PMI"]
tags: [event]
---
# Purchasing Managers Index (PMI)

Survey-based manufacturing/services activity index.

## Conditions (machine rules)
- **expansion**: `pmi > 50`
- **contraction**: `pmi < 50`
- **no_change**: `pmi == 50`

## Links
- COVERED_IN → [[module_m01]]
- PART_OF → [[market_events]]

## Backlinks
- [[market_events]] RELATED_TO →
