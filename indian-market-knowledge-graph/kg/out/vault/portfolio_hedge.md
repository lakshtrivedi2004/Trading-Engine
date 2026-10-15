---
id: portfolio_hedge
type: Strategy
modules: [M04]
tags: [strategy]
---
# Portfolio Hedge with Nifty Futures

Short beta-weighted value of Nifty futures to neutralise systematic risk of a long portfolio.

## Formula
- **portfolio_beta**: `sum(weight_i * beta_i)`
- **hedge_value**: `portfolio_beta * portfolio_value`
- **lots**: `hedge_value / (nifty_futures_price * lot_size)`

## Caveats
- Fractional lots impossible - perfect hedge rarely possible.

## Example
8 lakh portfolio, beta 1.223 -> hedge 9.78 lakh -> 4.33 lots (choose 4 under-hedge or 5 over-hedge); Nifty -500 pts -> futures +54,125 vs portfolio -54,240.

## Bot use
Automated beta-hedging overlay for long equity books.

## Links
- COVERED_IN → [[module_m04]]
- USES → [[beta]]
- USES → [[index_futures]]
- IS_A → [[hedging]]

## Backlinks
- [[hedging]] RELATED_TO →
- [[beta]] PART_OF →
