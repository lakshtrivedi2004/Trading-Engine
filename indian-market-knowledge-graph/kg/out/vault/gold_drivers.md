---
id: gold_drivers
type: Concept
modules: [M08]
aliases: ["London fix", "Bullion"]
tags: [concept]
---
# Gold - Pricing and Drivers

International price set by London AM/PM fix (banks only); Indian price = international + CIF + customs duty + cess + bank costs, so MCX vs CME gold differences are not arbitrage. Gold is a safe haven - rises on uncertainty (e.g. Brexit), inversely related to the US dollar (not always), long-run ~8% CAGR in USD (1970-2016).

## Formula
- **grams_per_troy_oz**: `31.1035`
- **inr_per_10g_naive**: `usd_per_oz / 3.11035 * usdinr (+ duties)`

## Links
- COVERED_IN → [[module_m08]]
- RELATED_TO → [[currency_drivers]]
- RELATED_TO → [[silver]]
