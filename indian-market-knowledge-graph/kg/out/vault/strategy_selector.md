---
id: strategy_selector
type: Rule
modules: [M06]
tags: [rule]
---
# View -> Option Strategy Map

Map outlook and volatility view to a strategy.

## Conditions (machine rules)
- **moderately_bullish_low_iv**: `bull_call_spread`
- **moderately_bullish_after_fall_high_iv**: `bull_put_spread`
- **outright_bullish**: `["call_ratio_back_spread", "bear_call_ladder"]`
- **moderately_bearish_low_iv**: `bear_put_spread`
- **moderately_bearish_after_rally_high_iv**: `bear_call_spread`
- **outright_bearish**: `put_ratio_back_spread`
- **big_move_unknown_direction_low_iv_rising**: `["straddle", "strangle"]`
- **range_bound_high_iv_falling**: `["short_straddle", "short_strangle"]`
- **replicate_futures**: `synthetic_long`

## Bot use
Rule table for an options strategy engine keyed on direction signal strength + IV regime + days to expiry.

## Links
- COVERED_IN → [[module_m06]]
- USES → [[bull_call_spread]]
- USES → [[bull_put_spread]]
- USES → [[call_ratio_back_spread]]
- USES → [[bear_call_ladder]]
- USES → [[bear_put_spread]]
- USES → [[bear_call_spread]]
- USES → [[put_ratio_back_spread]]
- USES → [[straddle]]
- USES → [[strangle]]
- USES → [[synthetic_long]]

## Backlinks
- [[option_strategy_principles]] RELATED_TO →
