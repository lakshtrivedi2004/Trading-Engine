---
id: black_scholes
type: Model
modules: [M05]
aliases: ["BS model", "Options calculator"]
tags: [model]
---
# Black-Scholes Option Pricing

Inputs - spot (or futures for futures-based options), strike, risk-free rate (RBI 91-day T-bill), implied volatility, dividend (if ex-date before expiry), calendar days to expiry; outputs theoretical call/put prices and Greeks.

## Formula
- **d1**: `(ln(S/K) + (r - q + sigma^2/2) T) / (sigma sqrt(T))`
- **d2**: `d1 - sigma sqrt(T)`
- **call**: `S e^{-qT} N(d1) - K e^{-rT} N(d2)`
- **put**: `K e^{-rT} N(-d2) - S e^{-qT} N(-d1)`

## Facts
- Black & Scholes (1973)
- Merton; Nobel 1997. Small model errors expected.

## Links
- COVERED_IN → [[module_m05]]
- RELATED_TO → [[option_greeks]]
- RELATED_TO → [[put_call_parity]]

## Backlinks
- [[option_greeks]] COMPUTED_FROM →
- [[commodity_options]] VARIANT_OF →
