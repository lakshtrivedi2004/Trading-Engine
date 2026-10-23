# Knowledge Graph Schema

Source: Zerodha Varsity modules 1–14 (module12.md is empty in the source folder).
Authoring: one YAML file per module in `kg/extract/`, compiled by `kg/build.py`.

## Node
| field      | meaning |
|------------|---------|
| id         | global snake_case id, unique across all modules (cross-module links use it) |
| type       | see node types |
| name       | display name |
| aliases    | other names / abbreviations |
| module     | M01..M14 (set from file) |
| chapter    | chapter number(s) in the source module |
| summary    | 1–3 sentence definition |
| facts      | list of atomic, source-backed statements |
| formula    | text formula(s) (`dict name -> expression` or string) |
| conditions | machine-readable rule: entry/exit/identify conditions (for bots) |
| params     | default parameters (lookback periods, thresholds) |
| bot_use    | how an automated trading system can use this node |
| caveats    | limitations, failure modes, warnings from the text |
| example    | worked numeric example from the text |
| links      | list of `{to: <id>, rel: <REL>}` |

## Node types
Module, Concept, Instrument, Participant, Regulation, Tax, Process, Indicator, CandlestickPattern,
ChartPattern, Formula, Ratio, Greek, Strategy, Rule, Psychology, Model, Product, Event, Contract

## Relationship types
IS_A, PART_OF, USES, COMPUTED_FROM, CONFIRMS, CONTRADICTS, SIGNALS, VARIANT_OF, OPPOSITE_OF,
PREREQUISITE, HEDGES, AFFECTS, REGULATED_BY, TAXED_AS, APPLIES_TO, RELATED_TO, EXAMPLE_OF, MEASURES
