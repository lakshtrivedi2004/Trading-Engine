---
id: covariance_correlation_matrix
type: Formula
modules: [M09]
tags: [formula]
---
# Variance, Covariance and Correlation Matrices

Variance = dispersion of an asset's returns; covariance = co-movement sign of two assets; correlation = covariance / (sd_x sd_y). For k assets build the k x k variance-covariance matrix from the n x k excess-return matrix X, then the correlation matrix.

## Formula
- **variance**: `sum((x - mean)^2)/N`
- **covariance**: `sum((r1 - mean1)(r2 - mean2)) / (n - 1)`
- **cov_matrix**: `(1/n) * X^T X, X = returns - column means`
- **corr_matrix**: `cov_matrix / (sd sd^T)`

## Facts
- Diagonal of covariance matrix = variances; correlation matrix diagonal = 1, symmetric.
- Portfolio managers prefer low/negative covariance between holdings so they counterbalance.

## Example
Cipla vs Idea covariance 0.0000523, correlation 0.106.

## Links
- COVERED_IN → [[module_m09]]
- RELATED_TO → [[correlation]]
- PART_OF → [[portfolio_variance]]

## Backlinks
- [[portfolio_variance]] USES →
