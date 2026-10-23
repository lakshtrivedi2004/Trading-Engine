---
id: synthetic_long
type: Strategy
modules: [M06]
tags: [strategy]
---
# Synthetic Long (and PCP Arbitrage)

Long ATM call + short ATM put replicates long futures (linear payoff); breakeven = strike + net premium. Arbitrage - synthetic long + short futures should net zero at expiry; a positive locked P&L after costs is an arbitrage.

## Formula
- **synthetic_long**: `+C(K) - P(K)`
- **arbitrage**: `C(K) - P(K) - F + (F - K) ... P&L at expiry = (F - K) - (C - P) ; trade if > costs`

## Facts
- Arbitrage risks (fish-market analogy) - opportunity, liquidity, execution, transaction cost, competition erodes it.

## Example
Nifty 7304, fut 7316; long 7300 CE 79.5, short 7300 PE 73.85, short fut 7316 -> locks ~10.35 pts before costs.

## Links
- COVERED_IN → [[module_m06]]
- USES → [[put_call_parity]]
- IS_A → [[arbitrage]]
- RELATED_TO → [[futures_contract]]

## Backlinks
- [[strategy_selector]] USES →
- [[arbitrage]] RELATED_TO →
