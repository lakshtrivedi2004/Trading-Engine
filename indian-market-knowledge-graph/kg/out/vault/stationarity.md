---
id: stationarity
type: Concept
modules: [M10]
aliases: ["ADF", "Augmented Dickey-Fuller", "Cointegration"]
tags: [concept]
---
# Stationarity, Cointegration and the ADF Test

Two prices are cointegrated if the regression residuals are stationary (constant mean, constant SD, no autocorrelation); test with Augmented Dickey-Fuller - p-value is probability of NON-stationarity; require p <= 0.05. Regression relations on non-stationary residuals are spurious.

## Conditions (machine rules)
- **tradable_pair**: `adf_pvalue(residuals) <= 0.05`
- **manual_checks**: `means of 3 sub-samples similar; SDs similar; lag-1/lag-2 autocorrelation ~0`

## Bot use
Weekly/biweekly re-screen of sector pairs (200-day lookback) with statsmodels.adfuller.

## Links
- COVERED_IN → [[module_m10]]
- PART_OF → [[pair_trading_regression_method]]

## Backlinks
- [[mean_reversion]] RELATED_TO →
- [[pair_trading_regression_method]] USES →
