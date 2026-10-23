---
id: backtesting
type: Process
modules: [M10]
tags: [process]
---
# Backtesting

Running a system's rules over historical data to measure frequency, win rate and P&L profile before risking capital; use clean, corporate-action-adjusted, date-aligned data; backtest per instrument and per direction (e.g. SBIN calendar spreads - shorts worked, longs mixed).

## Bot use
Every node with 'conditions' should be backtested with realistic costs/slippage before deployment.

## Links
- COVERED_IN → [[module_m10]]
- PART_OF → [[trading_system]]
- USES → [[transaction_charges]]
- USES → [[impact_cost]]

## Backlinks
- [[speculator_trader_investor]] RELATED_TO →
- [[seasonality_test]] RELATED_TO →
- [[equity_curve]] RELATED_TO →
- [[trading_system]] USES →
- [[momentum_portfolio]] USES →
