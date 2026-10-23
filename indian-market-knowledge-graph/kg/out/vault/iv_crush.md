---
id: iv_crush
type: Strategy
modules: [M05]
aliases: ["IV crush", "Event trade"]
tags: [strategy]
---
# Event Volatility Play (short straddle before event, exit before announcement)

IV rises into scheduled events (RBI policy, results); short ATM call + put a few days before and buy back as IV drops - ideally minutes before/at the announcement. Long straddles into events often lose because the losing leg's premium collapses faster than the winning leg gains.

## Conditions (machine rules)
- **entry**: `2-4 days before known event, IV elevated, short ATM CE + ATM PE (liquid)`
- **exit**: `time-based stop - just before announcement (or right after, as IV collapses)`
- **stop**: `volatility-based, not premium-based`

## Example
Infosys Q2 - sold 1140 CE 48 + PE 47 (IV 40%/48%) 4 days before; after results CE 55 (IV 28%) + PE 20 (IV 40%) -> +20/lot. RBI trade +10/lot.

## Bot use
Event-calendar driven vol-selling strategy with time exits.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[vega]]
- RELATED_TO → [[monetary_policy]]
- RELATED_TO → [[straddle]]

## Backlinks
- [[earnings_announcement]] RELATED_TO →
- [[speculator_trader_investor]] RELATED_TO →
- [[vega]] RELATED_TO →
- [[systematic_deduction_example]] RELATED_TO →
