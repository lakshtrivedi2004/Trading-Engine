---
id: currency_futures_india
type: Contract
modules: [M08]
tags: [contract]
---
# Indian Currency Derivatives (USDINR, EURINR, GBPINR, JPYINR)

NSE/BSE currency futures quoted to 4 decimals, tick 0.0025; lot $1000 / EUR1000 / GBP1000 / JPY100000 (JPY quoted per 100 yen); margins ~2-4% (JPY highest); expiry 2 working days before month's last business day, trading till 12:30 on expiry; cash settled in INR at reference rate; USDINR options European, premium in INR, strikes at 0.25 intervals, 3 monthly expiries.

## Facts
- Currencies move in tight ranges, hence high leverage.
- Liquidity preference - USDINR futures > USDINR ATM options > GBPINR > EURINR > JPYINR futures.
- USDINR volatility far lower than Nifty (2015 annualised ~4.9% vs ~15.7%); daily SD 0.311% (2015).
- Retail cannot access USDINR spot, so spot-futures arbitrage is unavailable -> trade calendar spreads (exchange spread contracts exist).

## Caveats
- Lot sizes
- cross-currency pairs (EURUSD etc.) and hours have changed since.

## Example
Short 10 lots USDINR 67.69 on 15m bearish marubozu, exit 67.60 -> 0.09 x 1000 x 10 = Rs 900; P&L per pip Rs 2.5/lot.

## Links
- COVERED_IN → [[module_m08]]
- USES → [[currency_pair]]
- RELATED_TO → [[calendar_spread]]
- USES → [[rbi_reference_rate]]

## Backlinks
- [[currency_pair]] RELATED_TO →
- [[rbi_reference_rate]] RELATED_TO →
