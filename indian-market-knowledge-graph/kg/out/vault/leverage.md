---
id: leverage
type: Concept
modules: [M04]
tags: [concept]
---
# Leverage

Controlling a large contract value with a small margin; double-edged - magnifies gains and losses.

## Formula
- **leverage**: `contract_value / margin`
- **wipeout_move**: `1 / leverage`

## Parameters
- **author_max_leverage**: `[10, 12]`

## Example
TCS 295,250/41,335 = 7.14x -> 14% adverse move wipes out margin; 42x -> 2.3% move.

## Bot use
Cap effective leverage (e.g. <= 10x); compute margin-wipeout distance before entry.

## Links
- COVERED_IN → [[module_m04]]
- RELATED_TO → [[margin]]
- RELATED_TO → [[position_sizing]]

## Backlinks
- [[futures_contract]] USES →
