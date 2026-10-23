# Indian Market Knowledge Graph + `tradekb`

This is a machine-usable memory of Zerodha Varsity modules 1–14. Module 12's source file is empty.
It has three parts:

1. **Knowledge graph.** 375 nodes and 1,055 typed edges.
   - Every concept, indicator, pattern, strategy, formula, ratio, rule, tax and bias in the modules is a node.
   - Each node carries machine-readable `formula`, `conditions` (entry/exit/stop rules), `params`, `bot_use` and `caveats`.
2. **`tradekb`.** A Python library (numpy and pandas only) that turns those rules into executable code.
3. **Exports.** Formats for LLM agents, RAG, Neo4j and Obsidian.

```
kg/extract/M01..M14.yaml   source of truth: hand-authored per module (edit these)
kg/SCHEMA.md               node/edge schema
kg/build.py                validate + merge + emit  ->  kg/out/
kg/out/graph.json          full graph
kg/out/nodes.jsonl         one node per line with a `text` field (embed these for RAG)
kg/out/edges.jsonl
kg/out/rules.json          only nodes with formulas/conditions (204 bot rules)
kg/out/memory.md           whole graph as one compact markdown file (~35k tokens) - LLM context / system prompt
kg/out/vault/              Obsidian vault: one note per node, [[wikilinks]] + backlinks
kg/out/neo4j/              nodes.csv, edges.csv, import.cypher
kg/out/viewer.html         interactive graph explorer (open in a browser)
tradekb/                   executable library + CLI
examples/example_bot.py    checklist-driven swing bot wired to the graph
tests/                     pytest suite (python3 -m pytest tests)
```

## Rebuild

```bash
python3 kg/build.py            # after editing any kg/extract/*.yaml
python3 -m pytest -q tests
```

## Query (CLI)

```bash
python3 -m tradekb search "iv crush straddle"
python3 -m tradekb show "Supertrend"
python3 -m tradekb context "hedge a portfolio with futures" --depth 1   # paste into an LLM
python3 -m tradekb neighbors kelly_criterion
python3 -m tradekb path rsi kelly_criterion
python3 -m tradekb rules --type Strategy > strategies.json
```

## Use in a bot

```python
from tradekb import KG, indicators as ind, candles, options, risk, pairs, strategies, valuation

kg = KG()
kg["pair_trading_regression_method"]["conditions"]   # {'long_pair': 'z <= -2.5 ...', 'stop_loss': ...}

m = pairs.analyse_pair(prices, "HDFCBANK", "ICICIBANK", lookback=200)   # error ratio + ADF + z-score
m.signal, m.tradable, m.reasons, m.quantities(lots_y=1, lot_y=550, lot_x=700)

candles.trade_plans(ohlc_df)                    # pattern, side, entry, stop, tradable (1-10% range)
options.black_scholes(22000, 22200, 7, 0.14)    # price + delta/gamma/theta/vega
options.sd_writing_strikes(22000, 0.14, 5, strikes)
risk.position_size_risk(500_000, 0.015, entry, stop, lot_size=1)
strategies.momentum_portfolio(closes, top_n=12, regime=nifty_close)
valuation.dcf(fcff_5y, wacc, 0.05, net_debt, shares).fair_price
```

| module | contents (KG node ids) |
|---|---|
| `indicators` | SMA/EMA, RSI, MACD, Bollinger, ATR/ATR bands, ADX/DI, Aroon, Alligator, Supertrend, VWAP, Fibonacci, pivots, swing S&R clustering, volatility |
| `candles` | marubozu, doji, spinning top, hammer, hanging man, shooting star, engulfing, piercing, dark cloud, harami, morning/evening star, with prior-trend filter and trade plans |
| `options` | Black-Scholes + Greeks, implied vol, put-call parity, payoffs/multi-leg, SD ranges, SD option writing, max pain, PCR, moneyness, strategy selector |
| `risk` | RRR, %-risk and %-volatility sizing, Kelly, expectancy, volatility stop, 6-point TA checklist, beta, hedge lots, portfolio variance, Monte-Carlo frontier, VaR/CVaR, CAGR, XIRR, rolling returns, drawdown, Sharpe/Sortino/alpha/capture, trade stats |
| `pairs` | ratio density method, OLS + error ratio, ADF (statsmodels if installed, else numpy + MacKinnon p), full pair pipeline, sector pair screener |
| `strategies` | MA/price and MA crossover, RSI/MACD/BB/ADX/Supertrend signals, statistical calendar spread, futures fair value / cash-and-carry, momentum portfolio, simple backtesters, seasonality, Nifty PE regime |
| `valuation` | CAPM, WACC, FCFF, terminal value, DCF + sensitivity grid, margin of safety, 18 financial ratios + DuPont |

## Use as LLM / agent memory

- **Small agents.** Put `kg/out/memory.md` in the system prompt. Each line is `id [Type] Name: summary | F: formulas | C: conditions | → links`.
- **Large agents or RAG.** Embed `nodes.jsonl[].text`. At query time, retrieve the top-k nodes and expand one hop with `KG().context(query)`.
- **Tool-using agents.** Expose `python3 -m tradekb context|show|rules` as tools. Node ids are stable, so an agent can cite them.

## Caveats

- **Dated rules.** Some content reflects the source's publication dates. Nodes carry `caveats` flagging these; verify against current SEBI, IT Act and exchange circulars before automating.
  - Tax rates and STT (M07 is FY14-16).
  - Settlement (T+2, now T+1).
  - Lot sizes, MCX contract specs and expiry days.
  - Insurance rules.
- **Not in the source.** These were announced in Varsity but never written, so they are not in the graph: the delta-hedging system, interest-rate futures and some extra option strategies.
- **Stubs.** 7 nodes are curated stubs, referenced but not defined in the source: stop_loss, expectancy, pivot_points, dividend_yield, execution, quantitative_analysis, averaging_down.
- **Not validated edges.** The rules encode the book's methods. `examples/example_bot.py` runs on synthetic data and shows wiring, not profitability. Backtest every rule on real data with costs (`strategies.backtest_*`) before risking capital.
