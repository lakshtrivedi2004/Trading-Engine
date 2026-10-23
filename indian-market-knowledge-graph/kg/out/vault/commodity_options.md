---
id: commodity_options
type: Contract
modules: [M08]
aliases: ["Black 76", "CTM", "Devolvement"]
tags: [contract]
---
# Commodity Options (options on futures)

Underlying is the commodity futures contract; price with Black-76 (not Black-Scholes); European; on expiry ITM options devolve into futures at the strike. CTM = 2 strikes above/below ATM (by daily settlement price) - need explicit instruction to exercise else worthless; other ITM auto-exercise unless contrary instruction; devolvement margin collected in the days before expiry.

## Formula
- **black76_call**: `e^{-rT} [F N(d1) - K N(d2)], d1 = (ln(F/K) + sigma^2 T/2)/(sigma sqrt T)`

## Links
- COVERED_IN → [[module_m08]]
- VARIANT_OF → [[black_scholes]]
- RELATED_TO → [[mcx_commodity_futures]]
