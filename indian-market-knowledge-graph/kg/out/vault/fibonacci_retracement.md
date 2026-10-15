---
id: fibonacci_retracement
type: Indicator
modules: [M02]
tags: [indicator]
---
# Fibonacci Retracements

After a sharp move (trough to peak = 100%), price tends to retrace to 23.6%, 38.2% or 61.8% before resuming the trend.

## Formula
- **level_up_move**: `peak - ratio*(peak - trough)`
- **level_down_move**: `trough + ratio*(peak - trough)`
- **ratios**: `[0.236, 0.382, 0.618]`

## Facts
- Use only as a confirmation tool; strong buy when stop coincides with Fib level plus pattern
- S&R and volume.

## Example
380 -> 489 move of 109; 61.8% retracement = 489 - 67.36 = 421.6.

## Links
- COVERED_IN → [[module_m02]]
- CONFIRMS → [[ta_checklist]]
