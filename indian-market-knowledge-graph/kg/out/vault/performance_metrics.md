---
id: performance_metrics
type: Formula
modules: [M11]
aliases: ["Sharpe ratio", "Sortino ratio", "Jensen alpha", "Upside capture", "Downside capture"]
tags: [formula]
---
# Risk-Adjusted Performance Metrics (Beta, Alpha, SD, Sharpe, Sortino, Capture Ratios)

Beta = relative risk vs benchmark (not inherent risk); alpha = risk-adjusted excess return; SD = inherent volatility; Sharpe = excess return per unit total volatility; Sortino = per unit downside deviation; capture ratios = share of benchmark up/down moves captured - prefer consistent low downside capture.

## Formula
- **alpha**: `(R_fund - Rf) - beta * (R_bench - Rf)`
- **sharpe**: `(R - Rf) / sd`
- **sortino**: `(R - Rf) / downside_deviation`
- **upside_capture**: `fund_return_in_up_periods / bench_return_in_up_periods * 100`
- **downside_capture**: `fund_return_in_down_periods / bench_return_in_down_periods * 100`

## Caveats
- Sharpe is meaningless for debt funds (ignores credit/rate risk).

## Example
Fund 10%, bench 7%, Rf 4%, beta 0.75 -> alpha 3.75% (beta 1.3 -> 2.1%); Fund A (14%, sd 28%) and B (16%, sd 34%) both Sharpe 0.29 at Rf 6%.

## Bot use
Standard strategy scorecard - Sharpe, Sortino, beta/alpha vs Nifty, capture ratios, max drawdown.

## Links
- COVERED_IN → [[module_m11]]
- USES → [[beta]]
- USES → [[standard_deviation]]

## Backlinks
- [[fund_analysis_process]] USES →
