---
id: max_pain
type: Indicator
modules: [M06]
tags: [indicator]
---
# Max Pain (Option Pain)

Strike at which total intrinsic payout to option buyers (loss to writers) is minimum; theory says expiry gravitates there.

## Formula
- **writer_loss_at_S**: `sum_over_strikes(call_OI_k * max(0, S - k) + put_OI_k * max(0, k - S))`
- **max_pain**: `argmin_S writer_loss_at_S over listed strikes`

## Conditions (machine rules)
- **author_variant**: `compute at ~15 days to expiry; add +5% buffer; expect expiry between max_pain and max_pain*1.05; write calls beyond the buffer; avoid writing puts; hold to expiry, no averaging`

## Caveats
- Controversial
- no academic backing; the 5% buffer ~1.5-2 SD; backtest before use.

## Links
- COVERED_IN → [[module_m06]]
- USES → [[open_interest]]
- RELATED_TO → [[sd_option_writing]]
