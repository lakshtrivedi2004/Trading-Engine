---
id: mcx_commodity_futures
type: Contract
modules: [M08]
aliases: ["MCX", "NCDEX"]
tags: [contract]
---
# MCX Commodity Futures (general rules)

MCX (metals, energy, some agri) and NCDEX (agri); prices include import duties/taxes; most contracts physically deliverable - intent to deliver must be given before tender period (brokers often force exit before); each contract maps to a designated spot delivery centre; trade the current/near month for liquidity; agri traded till 5 PM, no MIS for agri.

## Formula
- **contract_value**: `price * lot_size / quotation_unit`
- **pnl_per_tick**: `(lot_size / quotation_unit) * tick_size`

## Facts
- Gold (1 kg, quote per 10 g, tick Rs1 -> Rs100/tick, expiry 5th, bi-monthly), Gold Mini (100 g, Rs10/tick), Guinea, Petal (illiquid) - margins ~4-5%.
- Silver (30 kg, quote per kg, Rs30/tick, compulsory delivery), Silver Mini/Micro (cash-settle option) - margin ~5-6%.
- Crude oil (100 bbl, quote per bbl, Rs100/tick, expiry ~19th, 6 months listed, margin ~9% NRML / 4.5% MIS); Crude Mini (10 bbl, Rs10/tick); MCX crude tracks Brent (per source).
- Copper (1 MT, quote per kg, tick 0.05 -> Rs50, ~7.8%); Aluminium (5 MT, Rs250/tick, ~5.6%) and Mini (1 MT); Lead (5 MT, Rs250/tick, ~11.7%) and Mini; Nickel (250 kg, tick 0.10 -> Rs25, ~10%) and Mini (100 kg); base metals expire last day of month.
- Cardamom (100 kg, Rs10/tick, expiry 15th, ~10.5%); Mentha oil (360 kg, Rs36/tick, ~8.5%) - most liquid agri on MCX.
- Natural gas (1250 mmBtu, tick 0.10 -> Rs125, expiry 25th, ~15% NRML) - mirrors NYMEX.

## Caveats
- Specs as of 2016-17; MCX crude now references WTI-linked pricing and many specs changed - verify.

## Links
- COVERED_IN → [[module_m08]]
- IS_A → [[futures_contract]]
- RELATED_TO → [[ta_on_currencies_commodities]]

## Backlinks
- [[base_metals]] RELATED_TO →
- [[agri_commodities]] RELATED_TO →
- [[commodity_options]] RELATED_TO →
