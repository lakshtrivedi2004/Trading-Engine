---
id: rsi
type: Indicator
modules: [M02]
aliases: ["RSI"]
tags: [indicator]
---
# Relative Strength Index (RSI)

J. Welles Wilder's 0-100 momentum oscillator of internal strength; strongest in sideways markets.

## Formula
- **rs**: `avg_gain(n) / avg_loss(n)`
- **rsi**: `100 - 100/(1 + RS)`

## Conditions (machine rules)
- **classical_long**: `RSI < 30 (oversold)`
- **classical_short**: `RSI > 70 (overbought)`
- **persistent_overbought**: `RSI stuck > 70 for long -> look to BUY (excess positive momentum)`
- **persistent_oversold**: `RSI stuck < 30 for long -> look to SELL`
- **exit_oversold**: `RSI crosses back above 30 after prolonged oversold -> long`
- **exit_overbought**: `RSI crosses back below 70 after prolonged overbought -> short`

## Parameters
- **period**: `14`
- **oversold**: `30`
- **overbought**: `70`
- **author_pref**: `[20, 80]`

## Facts
- Shorter lookback = more volatile RSI. Parameters are not sacred - develop your own edge. Rarely used standalone.

## Example
14 closes, gains 29, losses 10 -> RS 2.899 -> RSI 74.35.

## Links
- COVERED_IN → [[module_m02]]
- IS_A → [[leading_lagging_indicators]]
- PART_OF → [[ta_checklist]]

## Backlinks
- [[leading_lagging_indicators]] RELATED_TO →
- [[ta_checklist]] USES →
- [[momentum]] RELATED_TO →
