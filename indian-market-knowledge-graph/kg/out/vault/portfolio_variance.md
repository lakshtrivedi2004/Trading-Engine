---
id: portfolio_variance
type: Formula
modules: [M09]
tags: [formula]
---
# Portfolio Variance (Risk)

Portfolio volatility from weights, individual SDs and the correlation matrix; equivalently the SD of the portfolio's own daily returns (equity curve) - both give the same number.

## Formula
- **weighted_sd**: `w_i * sd_i`
- **portfolio_sd**: `sqrt( wsd^T * Corr * wsd )`
- **annualised**: `daily_portfolio_sd * sqrt(252)`
- **range_1sd**: `E[R] +/- sd_annual (68%), 2sd 95%, 3sd 99.7%`

## Caveats
- Short look-back in a bull market biases expected returns upward; use >= 1 year of data.

## Example
5-stock portfolio (Cipla 7%, Idea 16%, Wonderla 25%, PVR 30%, Alkem 22%) daily sd 1.11% -> annual 17.64%; E[R] 55.14% -> 1SD range 37.5%-72.8%.

## Links
- COVERED_IN → [[module_m09]]
- USES → [[covariance_correlation_matrix]]
- RELATED_TO → [[equity_curve]]
- RELATED_TO → [[portfolio_optimization]]

## Backlinks
- [[risk_management]] USES →
- [[expected_return]] RELATED_TO →
- [[covariance_correlation_matrix]] PART_OF →
- [[portfolio_optimization]] USES →
