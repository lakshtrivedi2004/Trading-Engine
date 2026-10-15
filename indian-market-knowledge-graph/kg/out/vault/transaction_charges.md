---
id: transaction_charges
type: Concept
modules: [M01]
aliases: ["Charges", "STT", "Brokerage"]
tags: [concept]
---
# Transaction Charges (brokerage, STT, exchange fees, GST, SEBI fees, stamp duty)

Costs on every trade - brokerage, Securities Transaction Tax, exchange transaction charges, service tax/GST, SEBI turnover fee.

## Caveats
- Rates in source are dated; use current schedule.

## Example
2014 delivery buy of Rs 1,00,000 - brokerage 20, STT 100 (0.1%), txn 3.25, tax ~2.9, SEBI 0.2 => ~Rs 126.

## Bot use
Always deduct full round-trip costs in backtests; costs dominate high-frequency strategies.

## Links
- COVERED_IN → [[module_m01]]
- RELATED_TO → [[stt]]

## Backlinks
- [[contract_note]] RELATED_TO →
- [[scalping]] RELATED_TO →
- [[stt]] PART_OF →
- [[backtesting]] USES →
- [[expense_ratio]] RELATED_TO →
