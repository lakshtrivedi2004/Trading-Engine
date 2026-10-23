---
id: capital_gains_tax
type: Tax
modules: [M07]
aliases: ["LTCG", "STCG"]
tags: [tax]
---
# Capital Gains Tax on Equity and Mutual Funds (source era)

LTCG on listed equity/equity MFs via exchange (STT paid) 0%; STCG 15%. Off-market - LTCG 10% listed / 20% unlisted, STCG at slab. Debt/non-equity MFs - long-term after 3 years at 20% with indexation, else slab.

## Facts
- Equity-oriented MF = >= 65% in domestic equity.
- Holding period computed FIFO across multiple purchases.
- STT cannot be added to cost for capital gains; brokerage and other charges can.
- Short-term capital losses carry forward 8 years against capital gains; long-term equity losses (when LTCG was exempt) were dead losses.

## Caveats
- Current law differs (verify): LTCG on listed equity taxable above an exemption limit since 2018; STCG and LTCG rates revised in 2024.

## Example
Buy 100 Reliance 10-Apr-14 @800 and 100 on 1-Jun-14 @820; sell 150 on 1-May-15 @920 -> 100 sh LTCG 12,000 (0%), 50 sh STCG 5,000 (15%).

## Bot use
Tax-aware exit timing (holding-period counter); after-tax backtest returns.

## Links
- COVERED_IN → [[module_m07]]
- USES → [[indexation]]
- RELATED_TO → [[stt]]
- RELATED_TO → [[holding_period]]

## Backlinks
- [[market_income_classification]] RELATED_TO →
- [[indexation]] PART_OF →
