---
id: value_at_risk
type: Formula
modules: [M09]
aliases: ["VaR", "CVaR", "Expected shortfall"]
tags: [formula]
---
# Value at Risk (VaR) and CVaR (historical)

Worst expected daily loss at a confidence level; historical method - check returns look normal (histogram), sort returns, VaR = worst value within best 95% of observations, CVaR = average of the worst 5%.

## Formula
- **var_95**: `5th percentile of daily portfolio returns`
- **cvar_95**: `mean(returns <= var_95)`

## Facts
- Born after Black Monday (19 Oct 1987
- Dow -22.6%); JP Morgan's 4:15 PM report -> RiskMetrics.

## Example
126 daily returns -> VaR -1.48%, CVaR -2.39%.

## Bot use
Daily risk report and pre-trade risk limit (portfolio VaR <= budget).

## Links
- COVERED_IN → [[module_m09]]
- USES → [[normal_distribution]]
- PART_OF → [[risk_management]]

## Backlinks
- [[risk_management]] USES →
