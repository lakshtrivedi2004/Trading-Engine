---
id: beta
type: Formula
modules: [M04]
tags: [formula]
---
# Beta

Sensitivity of a stock's returns to market returns; market beta = 1; <1 low beta, >1 high beta, <0 inverse.

## Formula
- **beta**: `slope(stock_daily_returns, index_daily_returns) = cov(rs, rm)/var(rm)`
- **daily_return**: `close_t/close_{t-1} - 1`

## Parameters
- **lookback**: `~6 months daily`

## Example
2015 betas - DLF 1.86, SBI 1.58, L&T 1.43, BPCL 1.42, Axis 1.40, Reliance 1.27, ACC 1.22, Maruti 0.95, Cipla 0.59, Infosys 0.43; TCS 0.62.

## Links
- COVERED_IN → [[module_m04]]
- PART_OF → [[portfolio_hedge]]
- MEASURES → [[systematic_risk]]

## Backlinks
- [[hedging]] USES →
- [[portfolio_hedge]] USES →
- [[pair_trading_regression_method]] RELATED_TO →
- [[performance_metrics]] USES →
- [[capm]] USES →
