---
id: aroon
type: Indicator
modules: [M02]
tags: [indicator]
---
# Aroon and Aroon Oscillator

Tushar Chande's time-since-high/low indicator (0-100); default 25 (Kite 14).

## Formula
- **aroon_up**: `100*(n - periods_since_n_high)/n`
- **aroon_down**: `100*(n - periods_since_n_low)/n`
- **oscillator**: `aroon_up - aroon_down`

## Conditions (machine rules)
- **buy**: `aroon_up > 50 and aroon_down < 30`
- **sell**: `aroon_down > 50 and aroon_up < 30`
- **strong_up**: `oscillator > 50`
- **strong_down**: `oscillator < -50`

## Links
- COVERED_IN → [[module_m02]]
- MEASURES → [[trend]]
