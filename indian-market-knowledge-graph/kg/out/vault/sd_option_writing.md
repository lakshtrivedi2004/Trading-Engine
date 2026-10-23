---
id: sd_option_writing
type: Strategy
modules: [M05]
tags: [strategy]
---
# SD-Based OTM Option Writing (author's method)

Compute 1-SD (or 2-SD) expected range to expiry and sell options outside it; author sells only calls, ~1 SD away, on the Friday before expiry week.

## Conditions (machine rules)
- **range**: `spot*(1 +/- (daily_mean*days +/- daily_sd*sqrt(days)))`
- **sell_calls**: `strikes above upper 1SD bound`
- **sell_puts**: `strikes below lower bound (author avoids puts - panic spreads faster than greed)`
- **timing**: `last Friday before expiry week (~4-7 days to expiry); never > 15 days to expiry; use 2SD if writing earlier`
- **avoid**: `events - monetary policy, results, policy decisions`
- **exit**: `when written option turns ATM, or sentiment turns`
- **instruments**: `Nifty, Bank Nifty, SBI, Infosys, Reliance, Tata Steel, Tata Motors, TCS (liquid only)`

## Parameters
- **max_capital_per_strategy_pct_of_trading_bucket**: `35`

## Caveats
- Small steady gains
- rare catastrophic losses; keep costs minimal; paper-trade several expiries first.

## Example
Nifty 8462, 16 days, daily SD 0.89% -> range 8214-8818; sell 8850/8900 CE for 7.45/4.85 -> ~1.55% on ~12k margin.

## Bot use
Weekly premium-selling bot with SD strike selection, event calendar filter and ATM-touch exit.

## Links
- COVERED_IN → [[module_m05]]
- USES → [[normal_distribution]]
- USES → [[theta]]
- RELATED_TO → [[gamma]]
- RELATED_TO → [[capital_allocation_author]]

## Backlinks
- [[normal_distribution]] RELATED_TO →
- [[option_case_lessons]] RELATED_TO →
- [[max_pain]] RELATED_TO →
