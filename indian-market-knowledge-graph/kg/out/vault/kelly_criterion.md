---
id: kelly_criterion
type: Formula
modules: [M09]
tags: [formula]
---
# Kelly Criterion (and capped Kelly)

Optimal bet fraction from win rate and win/loss ratio; author scales a capped risk budget by Kelly% rather than betting full Kelly.

## Formula
- **kelly_pct**: `W - (1 - W)/R`
- **W**: `winning trades / total trades`
- **R**: `average win / average loss`
- **capped**: `risk_this_trade = kelly_pct * max_risk_pct (e.g. 5%)`

## Caveats
- Full Kelly is too aggressive - a 70% Kelly still leaves a large chance of a huge loss; estimates of W and R are noisy.

## Example
W 0.6, avg win 4532, avg loss 3274 -> R 1.384 -> Kelly ~31%; with 5% cap -> risk ~1.55% of capital.

## Bot use
Scale per-trade risk by rolling backtest/live Kelly, capped.

## Links
- COVERED_IN → [[module_m09]]
- RELATED_TO → [[expectancy]]

## Backlinks
- [[position_sizing]] RELATED_TO →
