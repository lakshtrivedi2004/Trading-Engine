---
id: earnings_announcement
type: Event
modules: [M01]
tags: [event]
---
# Quarterly Earnings Announcement

Listed companies report quarterly revenue, expenses, taxes, profit and sometimes guidance.

## Conditions (machine rules)
- **signal**: `sign(actual - consensus)`
- **in_line**: `flat_to_negative_bias`

## Facts
- Price reacts to actual vs street expectation - beat = up, miss = down, in-line = flat with negative bias.
- Indian quarters - Q1 Apr-Jun (results ~July), Q2 Jul-Sep (~Oct), Q3 Oct-Dec (~Jan), Q4 Jan-Mar (~Apr); Infosys usually reports first.

## Bot use
Earnings-surprise strategies; avoid holding through results unless intended; widen stops/IV crush awareness.

## Links
- COVERED_IN → [[module_m01]]
- PART_OF → [[market_events]]
- RELATED_TO → [[iv_crush]]

## Backlinks
- [[market_events]] RELATED_TO →
