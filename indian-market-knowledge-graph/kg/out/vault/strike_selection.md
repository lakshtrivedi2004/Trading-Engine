---
id: strike_selection
type: Rule
modules: [M05]
tags: [rule]
---
# Strike Selection by Series Half and Target Time

Which strike to buy depends on when in the series you trade and how fast the target is expected.

## Conditions (machine rules)
- **first_half_target_5d**: `far OTM (2 strikes from ATM)`
- **first_half_target_15d**: `ATM or slightly OTM (1 strike)`
- **first_half_target_25d**: `slightly ITM`
- **first_half_target_expiry**: `ITM`
- **second_half_same_day**: `far OTM (2-3 strikes)`
- **second_half_target_5d**: `slightly OTM (1 strike)`
- **second_half_target_10d_or_expiry**: `ATM or slightly ITM`
- **general**: `buy options when vol expected to rise; sell when expected to fall; don't buy far OTM just because cheap - needs a fast move`

## Bot use
Map signal horizon & days-to-expiry to strike offset automatically.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[theta]]
- USES → [[delta]]
- USES → [[vega]]
