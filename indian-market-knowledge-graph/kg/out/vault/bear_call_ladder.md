---
id: bear_call_ladder
type: Strategy
modules: [M06]
tags: [strategy]
---
# Bear Call Ladder (actually bullish)

Sell 1 ITM call, buy 1 ATM and 1 OTM call (1:1:1) for net credit; profits big above upper breakeven, modest if market falls, loses if market stalls; best on stocks around quarterly results.

## Formula
- **net_credit**: `prem_ITM - prem_ATM - prem_OTM`
- **max_loss**: `(ATM - ITM strike) - net_credit, between ATM and OTM strikes`
- **lower_breakeven**: `ITM_strike + net_credit`
- **upper_breakeven**: `ATM_strike + OTM_strike - ITM_strike - net_credit`

## Example
Nifty 7790 - sell 7600 CE 247, buy 7800 CE 117 and 7900 CE 70 -> credit 60; loss zone 7660-8040.

## Links
- COVERED_IN → [[module_m06]]
- VARIANT_OF → [[call_ratio_back_spread]]

## Backlinks
- [[strategy_selector]] USES →
- [[call_ratio_back_spread]] RELATED_TO →
