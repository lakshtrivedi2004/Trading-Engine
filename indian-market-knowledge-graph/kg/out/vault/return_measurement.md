---
id: return_measurement
type: Formula
modules: [M11]
aliases: ["XIRR", "Rolling returns", "Point-to-point return"]
tags: [formula]
---
# Return Measurement (Absolute, CAGR, XIRR, Rolling)

Lump-sum or SIP < 1 yr - absolute return; lump sum > 1 yr - CAGR; SIP > 1 yr - XIRR (cash-flow-dated IRR). Point-to-point returns depend on the two dates; rolling n-year returns (CAGR for every start date) show the distribution - use average, min, max.

## Formula
- **absolute**: `end/start - 1`
- **cagr**: `(end/start) ** (1/years) - 1`
- **xirr**: `rate r s.t. sum(cf_i / (1 + r) ** ((t_i - t_0)/365)) = 0`
- **rolling_n**: `for each date t: (nav_t / nav_{t - n years}) ** (1/n) - 1`

## Bot use
Evaluate strategies with rolling-window returns, not single point-to-point backtests.

## Links
- COVERED_IN → [[module_m11]]
- RELATED_TO → [[cagr]]
- RELATED_TO → [[absolute_return]]

## Backlinks
- [[fund_analysis_process]] USES →
- [[sip]] RELATED_TO →
