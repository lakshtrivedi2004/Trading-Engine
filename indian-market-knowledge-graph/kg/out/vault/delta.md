---
id: delta
type: Greek
modules: [M05]
tags: [greek]
---
# Delta

Change in premium per 1-point change in underlying; calls 0..1, puts -1..0; ATM ~0.5, ITM -> 1, OTM -> 0. First derivative of premium.

## Formula
- **premium_change**: `delta * underlying_change`
- **position_delta**: `sum(qty_lots * delta * sign)`

## Facts
- Delta changes with spot (moneyness changes).
- Delta stages - predevelopment (deep OTM ~0, big % but needs huge move), take-off & acceleration (OTM->ATM, max bang for buck), stabilisation (ITM->deep ITM, delta -> 1).
- Deep ITM option (delta 1) behaves like futures - can substitute with lower capital but watch liquidity.
- Deltas are additive across options of same underlying; futures delta = 1; two ATM options ~ one future (but not a true surrogate - other Greeks act).
- Short call = negative delta; short put = positive delta.
- Delta ~ probability of expiring ITM (0.1 delta -> ~10%).
- Delta-neutral positions (net delta 0, e.g. long ATM CE + long ATM PE) are insulated from direction but exposed to vol and time.

## Example
Nifty 8288, 8250 CE at 133, delta 0.55; Nifty to 8310 -> premium ~145.1.

## Bot use
Size option positions by net delta exposure; use delta as ITM probability for strike selection; hedge to delta-neutral.

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[gamma]]
- USES → [[moneyness]]
- RELATED_TO → [[delta_neutral]]

## Backlinks
- [[moneyness]] RELATED_TO →
- [[option_greeks]] PART_OF →
- [[delta_neutral]] USES →
- [[greek_interactions]] RELATED_TO →
- [[strike_selection]] USES →
- [[option_strategy_principles]] USES →
