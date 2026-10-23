---
id: pair_trading_regression_method
type: Strategy
modules: [M10]
tags: [strategy]
---
# Pair Trading Method 2 - Regression Residual z-score (Stat Arb)

For same-sector pairs over 200 days - regress both ways, keep lower error ratio orientation, require ADF p <= 0.05, then trade the residual's z-score with beta-neutral quantities.

## Formula
- **z**: `today_residual / SE_of_residuals`
- **qty_x**: `qty_y * beta (round to lot sizes; top up in cash market if needed)`
- **unexplained_share**: `intercept / price_y`

## Conditions (machine rules)
- **long_pair**: `z <= -2.5 (book: -2) -> buy Y, sell beta*X`
- **short_pair**: `z >= +2.5 -> sell Y, buy beta*X`
- **stop_loss**: `z beyond -3 (long) / +3 (short)`
- **target**: `z back to -1 (long) / +1 (short), ideally mean`
- **skip**: `negative beta (often untradeable); large intercept relative to price_y (e.g. 1626/2024 = 80% unexplained)`

## Facts
- Price, S&R, RSI are irrelevant - only the residual z-score matters.
- Positions may stay open 7-25 sessions; keep M2M buffer.
- Beyond pairs - multivariate regression (stock A vs basket B+C).

## Example
Tata Motors (Y) vs TM-DVR (X), ADF 0.018, beta 1.59, z -2.54 -> long 1 lot TM (1500) / short 1 lot DVR (2400 ~ 2385); exited ~7 sessions later at z ~ -1 for ~Rs 14k. HDFC(Y)/ICICI(X) beta 0.79 -> 7 lots HDFC vs 1 lot ICICI but rejected for huge intercept.

## Bot use
Complete stat-arb pipeline - nightly screen, z-score monitor intraday, beta-neutral sizing, z-based exits.

## Links
- COVERED_IN → [[module_m10]]
- USES → [[linear_regression]]
- USES → [[error_ratio]]
- USES → [[stationarity]]
- IS_A → [[pair_trading]]
- RELATED_TO → [[beta]]

## Backlinks
- [[pair_trading]] VARIANT_OF →
- [[linear_regression]] PART_OF →
- [[stationarity]] PART_OF →
