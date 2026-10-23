---
id: position_sizing
type: Rule
modules: [M09]
tags: [rule]
---
# Position Sizing Techniques

Decide how much capital/risk each trade gets. Methods - unit per fixed amount (e.g. 1 lot per 1L - simple, ignores risk, doesn't scale); percentage margin (fixed % of equity as margin per trade); percentage volatility (cap ATR-based volatility per position, e.g. 2% of equity); percentage risk (cap loss-at-stop at 1-3% of equity). Combine one equity model with one or two sizing methods.

## Formula
- **pct_volatility_shares**: `(equity * vol_pct) / ATR`
- **pct_risk_qty**: `(equity * risk_pct) / (abs(entry - stop) * lot_size)  [lots]`
- **classic_5pct_rule**: `max exposure per trade = 5% of capital`

## Parameters
- **risk_per_trade_pct**: `[1, 3]`
- **author_example_pct**: `1.5`
- **vol_pct**: `2`

## Facts
- Professional traders rarely risk > 1-3% per trade.
- Think about total portfolio volatility exposure (e.g. 15% of capital on a bad day - does it churn your stomach?).

## Example
PEL ATR 76, equity 5L, 2% vol -> 10,000/76 = 131 shares; Tata Motors long 393.65 SL 390 lot 1500, 1.5% of 5L = 7,500 / 5,475 per lot -> 1 lot.

## Bot use
Mandatory sizing layer between signal and order - default to % risk with ATR/volatility stops, reduced-total-equity accounting.

## Links
- COVERED_IN → [[module_m09]]
- USES → [[equity_estimation_models]]
- USES → [[atr]]
- RELATED_TO → [[kelly_criterion]]
- USES → [[stop_loss]]

## Backlinks
- [[atr]] RELATED_TO →
- [[leverage]] RELATED_TO →
- [[capital_allocation_author]] RELATED_TO →
- [[risk_management]] USES →
- [[gamblers_fallacy]] RELATED_TO →
- [[recovery_trauma]] RELATED_TO →
- [[equity_estimation_models]] PART_OF →
- [[trading_system]] USES →
