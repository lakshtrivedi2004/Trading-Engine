# India Market Research System

A research tool for **Nifty 50, Sensex, Bank Nifty and Bankex**. Run one command and it downloads the data, explains what moved each index, assesses the market regime, studies behaviour around events, tests trading patterns with proper validation, and writes everything into one HTML report.

It is built on published research methods, not indicators picked at random. It explains and tests; it does not predict, and it is not investment advice.

## Setup (one time)

1. Install Python 3.10 or newer from python.org. On Windows, tick "Add Python to PATH" during installation.
2. Open a terminal (Command Prompt on Windows) in this folder and run:

```
pip install -r requirements.txt
```

## Daily use

```
python main.py --open
```

This downloads data (cached for the day), runs every analysis, prints a short summary, and opens the report in your browser. Run it after 4 pm IST so the day's closing data is available; US data for the next morning appears after the US close.

Other options:

| Command | What it does |
|---|---|
| `python main.py --demo --open` | Full report on simulated data, no internet needed. Good for a first look. |
| `python main.py --refresh` | Forces a fresh download even if today's data is cached. |
| `python main.py --start 2015-01-01` | Changes how much history is used. |

Reports are saved in `reports/`, and every table is also exported as CSV to `reports/csv/` so you can explore the numbers in Excel.

## What the report contains

**1. What moved the markets.** Each day's move is split into the part explained by global and macro factors (S&P 500 overnight, Nikkei, Hang Seng, USD/INR, Brent crude, US 10-year yield) and the index-specific part. For Bank Nifty and Bankex the broad index is the first explainer, plus rates and the rupee. Sensitivities are re-estimated daily from the previous year only, so the explanation never uses the day's own data to fit itself. When the index-specific part is unusually large, the report flags it: that's the moment to look for domestic news, FII selling, or positioning.

**2. How much is explainable.** Out-of-sample R² shows what share of daily movement the factors explain. Expect modest numbers for Nifty and Sensex. That matches the research (Roll 1988; Cutler, Poterba & Summers 1989) and is itself useful knowledge.

**3. Market regime.** Trend versus the 50 and 200-day averages, current volatility versus its own history, distance from peak, India VIX, and how closely each index is moving with Nifty.

**4. Event studies.** How the indices behave around:
- big overnight moves in the US market,
- India VIX spikes,
- the index's own extreme days (does it reverse or continue?),
- Union Budgets and Lok Sabha results (from `data/events.csv`).

**5. Pattern tests.** Research-backed patterns, each tested on the first 70% of history and then checked on the last 30% it has never seen, after trading costs:
- trend following (200-day average) and time-series momentum,
- volatility timing,
- reversal or continuation after extreme days,
- the turn-of-the-month effect,
- day-of-the-week effects,
- opening-gap behaviour (only when Yahoo's opening prices are reliable).

Verdicts use a strict standard: in-sample t-statistic of 3 or more (Harvey, Liu & Zhu 2016) and survival out-of-sample. Most patterns will show "No reliable evidence". That is the honest, expected result in a competitive market.

**6. FII/DII flows (optional).** See below.

## Making it better with your own data

**Add RBI policy dates.** Open `data/events.csv` and add rows like:

```
YYYY-MM-DD,RBI policy,MPC decision
```

with dates from rbi.org.in. About six per year, so you quickly get enough cases for meaningful statistics. You can add any event type the same way (US Fed decisions, GDP releases); each new type gets its own event study automatically.

**Add FII/DII flows.** NSE publishes daily FII and DII net buying in the cash market each evening. Add one row per day to `data/fii_dii.csv`:

```
2026-10-01,-1234.5,2345.6
```

(date, FII net, DII net, in Rs crore). With 60+ days the report shows flow trends and how flows relate to Nifty's moves.

**Change settings.** Everything adjustable is in `config.py`: tickers, history length, cost per trade (set it to your real brokerage, STT and slippage), thresholds, and which factors explain which index.

## Method notes and limits

- **No look-ahead bias.** US markets close after India, so a US move is matched to the next Indian session. Trading rules decide at day t's close and are applied to day t+1.
- **Explanation versus prediction.** The decomposition explains a day after it happens; same-day factors like the rupee and Asian markets are only used there, never in the trading-rule tests.
- **Data quality.** Yahoo Finance is free but imperfect. Bankex in particular may be unavailable; if it fails, check the ticker on finance.yahoo.com and update `config.py`. The report lists any data problems at the top. Verify anything important against NSE or BSE.
- **Near-duplicates.** Nifty and Sensex, and Bank Nifty and Bankex, move almost identically. A pattern appearing in both is one finding, not two.
- **Multiple testing.** About 50 tests run each time; by chance alone, a few will look significant. Trust only what holds out-of-sample and keeps holding when you re-run it months later.
- **Patterns fade.** Published anomalies weaken after they become known (McLean & Pontiff 2016).

## Files

| File | Purpose |
|---|---|
| `main.py` | Runs everything |
| `config.py` | All settings |
| `data.py` | Download, cache, data-quality checks |
| `decompose.py` | Daily move decomposition |
| `regime.py` | Trend, volatility, drawdown |
| `events.py` | Event studies |
| `patterns.py` | Pattern tests with in/out-of-sample validation |
| `flows.py` | FII/DII analysis |
| `report.py` | HTML report |
| `demo_data.py` | Simulated data for `--demo` |
