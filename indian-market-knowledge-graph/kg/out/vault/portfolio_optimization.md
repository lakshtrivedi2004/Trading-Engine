---
id: portfolio_optimization
type: Model
modules: [M09]
aliases: ["Efficient frontier", "Minimum variance portfolio", "Markowitz"]
tags: [model]
---
# Portfolio Optimisation and Efficient Frontier

Varying weights changes risk and return. Solve (e.g. Excel Solver) for minimum-variance portfolio (min sd s.t. sum w = 1), and for each fixed risk level the max- and min-return portfolios; the upper envelope from the min-variance point is the efficient frontier - always choose portfolios on it.

## Formula
- **min_variance**: `min_w sqrt(w^T Sigma w) s.t. sum(w) = 1`
- **max_return_at_risk**: `max_w w^T mu s.t. sum(w)=1, sqrt(w^T Sigma w) = target`

## Caveats
- Add no-short (w >= 0) constraints for long-only; optimisation is sensitive to estimated returns.

## Example
Min-variance 15.57% risk / 36.25% return (Cipla raised 7% -> 29.6%); at 17% risk max return 55.87%, min 18.35%.

## Bot use
Allocate across strategies/assets on the efficient frontier given a risk budget.

## Links
- COVERED_IN → [[module_m09]]
- USES → [[portfolio_variance]]
- USES → [[expected_return]]

## Backlinks
- [[portfolio_variance]] RELATED_TO →
