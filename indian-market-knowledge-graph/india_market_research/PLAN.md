# Plan: India Index Research Platform

Status: **draft v2 (Linux-first), waiting for your approval.** No code has been written yet.

## 1. What I found in the prototype

The prototype's core methods are sound. I'll keep them and wrap them in a proper package:

- `merge_asof` US-to-India mapping
- rolling out-of-sample betas
- Newey-West t-statistics
- strict verdicts
- event de-duplication
- `open_data_usable`

Issues I'll fix. None of them changes the research rules you set.

| # | Where | Issue | Fix |
|---|---|---|---|
| 1 | `patterns.conditional_tests` | **Turn-of-month look-ahead at the data edge.** "Last trading day of the month" is worked out from the data itself. So the newest row always looks like a month-end, even mid-month. | Build the trading calendar from known exchange holidays plus weekdays. The open, incomplete month is excluded from tests. |
| 2 | `patterns.conditional_tests` | **One-day leak across the train/test split.** The last in-sample day's target is tomorrow's return, which is the first out-of-sample day. | Leave a gap of at least one day (configurable) between in-sample and out-of-sample. |
| 3 | `patterns.test_timing` | **Timing rules have no t-statistic.** They are judged on Sharpe and drawdown only. | Add the Newey-West t-statistic of rule-minus-hold daily returns, both in-sample and out-of-sample. Then apply the same strict verdict as every other test. |
| 4 | `events.event_study` | **"Normal day" uses the whole sample, including the future.** | Use the mean of the 250 trading days before each event. This is MacKinlay's (1997) estimation window. |
| 5 | `patterns` / `report` | **Multiple testing is only a sentence.** Changing a parameter in the Pattern lab and re-running is a new test, but nothing counts it. | Keep a test registry in the database that counts every test ever run. Show "tests run (all time)" next to every verdict. Group results into independent families: Broad = Nifty + Sensex, Banks = Bank Nifty + Bankex. |
| 6 | `data.quality_checks` | **Data age counts calendar days.** A long holiday weekend triggers false alarms. | Count missing **trading** sessions instead. |
| 7 | prices | **USD/INR on Yahoo (`INR=X`) has weekend/stale quotes and sometimes spikes.** | Add the same stale and jump checks used for the indices. Problems show in the data-health table. |

## 2. Architecture

Each stage feeds the next:

1. **Data sources:** Yahoo (yfinance), NSE FII/DII, and your CSV files (events, expiry rules, manual flows).
2. **Update step** (`imr update`) loads them into the local database `data/market.db` (SQLite).
3. **Data-health checks** run on what was loaded and write their results to the `data_health` table.
4. **Analysis engine** (pure functions, tested): decompose, regime, events, patterns, walk-forward, stability, flows.
5. **Outputs:** the Streamlit dashboard (`imr dashboard`) and the HTML snapshot (`imr report`).

Principles:
- **The analysis code never downloads anything.** It only reads the database. That's why every number can be traced to stored rows, and why the tests run offline.
- **Demo mode** writes to a separate `data/demo.db`. Every page and report shows a red **SIMULATED DATA** banner when it is used. Real and simulated data can never mix.
- **Settings live in `config/settings.toml`**, a plain text file that is easier to edit than Python. Your editable data lives in CSV files under `config/`.

## 3. Folder structure

```
india_market_research/
  imr/                         the package
    cli.py                     commands: update, dashboard, report, demo, check
    settings.py                loads config/settings.toml (typed, validated)
    paths.py, log.py           pathlib paths; rotating log file in logs/
    data/
      db.py                    SQLite schema, upserts, queries
      yahoo.py                 incremental price download with ticker fallbacks
      nse_flows.py             FII/DII fetch (polite headers, retry, rate limit) + CSV fallback
      calendars.py             trading calendars, holiday alignment, F&O expiry calendar
      events.py                load/validate events.csv
      quality.py               stale / jumps / missing days / open_data_usable
      demo.py                  simulated data (demo mode only)
    analysis/
      stats.py                 Newey-West, Welch, perf, split, verdict (strict rules kept)
      decompose.py             rolling OOS factor decomposition (+ optional FII/DII factor)
      regime.py                trend, vol percentile, VIX, drawdown, correlations
      events.py                event studies incl. RBI policy and F&O expiry
      patterns.py              rule library + in/out-of-sample tests
      walkforward.py           walk-forward validation
      stability.py             rolling out-of-sample performance per rule
      flows.py                 FII/DII trends, relation to Nifty
      registry.py              multiple-testing registry and families
      explain.py               all plain-language sentences in one place
    report/html.py             existing HTML report, adapted
    app/                       Streamlit: Home.py + pages/ (Today, Moves, Regime, Events, Pattern lab, Flows)
  config/
    settings.toml              tickers, costs, splits, thresholds
    events.csv                 your events (budgets, elections, RBI policy...)
    expiry_rules.csv           F&O expiry rule table with date ranges (needs your confirmation)
    holidays.csv               exchange holidays (optional; improves the calendar)
    fii_dii_manual.csv         manual flow import (the fallback)
  data/market.db               created automatically
  logs/  reports/  tests/
  pyproject.toml               installs the `imr` command (works the same on Linux, macOS, Windows)
  requirements.txt (pinned)  README.md
```

The old prototype files move to `legacy/`. I'll do that only after the new package reproduces their numbers on the same data. That parity check is a test.

## 4. Database schema (SQLite)

Every table that holds downloaded data has `source` and `downloaded_at` columns.

| table | key | columns |
|---|---|---|
| `prices` | (series, date) | open, high, low, close, volume, **ticker**, **source**, **downloaded_at** |
| `series` | series | kind (index/factor), currency, last_date, ticker_used, notes |
| `fii_dii_flows` | (date) | fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net, **source** (nse_api / manual_csv), **downloaded_at** |
| `events` | (date, type) | description, **source** (file name), **loaded_at** |
| `expiry_rules` | (exchange, contract, valid_from) | valid_to, weekday, week_of_month (last/...), holiday_shift (previous/next), **source**, confirmed (yes/no) |
| `holidays` | (exchange, date) | description, **source** |
| `data_health` | (check_time, series, check) | status (ok/warn/fail), detail, plain-language message |
| `download_log` | (run_id, series) | started, finished, rows_added, ticker, error |
| `test_registry` | (test_id) | run_time, rule, index, family, params (JSON), split, costs, t_in, t_out, verdict |

"One table per dataset" is applied by data type: all price series share `prices`, with a `series` column. That keeps incremental updates and queries simple. If you meant one table per series, tell me and I'll change it.

**Incremental update:** for each series, find the last stored date and download from that date minus 5 days. The 5-day overlap catches Yahoo's late corrections. The newer row replaces the stored one, and any value that changes by more than 0.5% is logged as a data-health warning.

## 5. Analysis additions

- **Walk-forward** (`analysis/walkforward.py`). Expanding window, with yearly test blocks by default. Rules that have parameters get a small parameter grid (for example, 100/150/200-day averages):
  - each fold picks the best parameter on its own past only, then is scored on the next unseen block;
  - the out-of-sample blocks are stitched into one return series, and the Newey-West t-statistic is computed on it.
  - Simple 70/30 split stays the default; walk-forward is an option.
- **Pattern stability** (`analysis/stability.py`): a rolling 2-year window of out-of-sample returns and Newey-West t per rule, charted over time. A fading effect shows as a falling line. (McLean & Pontiff 2016.)
- **FII/DII as a factor:** added to the decomposition only after at least 250 overlapping days exist (setting `min_flow_history`). Flows are published after the close, so a day's flows are treated as "same day" for explanation only. They are never used in trading-rule tests at the same date.
- **Event types:** RBI policy days come from `events.csv`, which you supply. F&O expiry days are generated from the rule table. Events are studied on each index, with clustered events removed. Their windows use only past data for the "normal day" baseline.
- **Duplicates and families:** results are grouped as Broad (Nifty + Sensex) and Banks (Bank Nifty + Bankex). A pattern counts as evidence once per family, and the dashboard says so explicitly.
- **Citations:** every function's docstring cites its method. Examples: Roll 1988; Cutler, Poterba & Summers 1989; MacKinlay 1997; Newey & West 1987; Harvey, Liu & Zhu 2016; Moskowitz, Ooi & Pedersen 2012; Moreira & Muir 2017; Ariel 1987; French 1980.

## 6. Dashboard (Streamlit)

There are six pages, plus a sidebar with a **DEMO** badge when relevant and a permanent disclaimer: "Explains and tests; does not predict prices or give buy/sell advice."

1. **Today:** a plain-language explanation of each index's move, a regime summary, and the data-health table with traffic lights.
2. **What moved the markets:** contribution bars, the last N sessions, rolling betas and out-of-sample R².
3. **Regime:** trend versus the 50/200-day averages, volatility percentile, India VIX, drawdown chart and a correlation matrix.
4. **Events:** average-path charts and tables. Filter by index and event type, and see the number of cases and t-statistics.
5. **Pattern lab:**
   - **Controls:** costs, split %, walk-forward on/off, thresholds.
   - **Results:** in-sample and out-of-sample side by side, verdicts in plain words, the stability chart, and "tests run (all time)".
6. **Flows:** FII/DII daily and cumulative flows, a 20-day trend, and their relationship with Nifty (same-day versus next-day correlation), labelled as association, not causation.

**Export snapshot** writes the existing HTML report (`imr report`).

Charts use Altair, which comes with Streamlit, so no extra dependency is needed.

## 7. Running it (Linux first; this machine is Athena OS with Python 3.14)

- **One-time setup:** create a virtual environment and install, using three commands in the README:
  ```
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .
  ```
  The `pip install -e .` step creates the `imr` command.
- **Daily use:**
  - `imr update` downloads new data and runs the data-health checks;
  - `imr dashboard` opens the dashboard in your browser;
  - `imr report` writes the HTML snapshot;
  - `imr demo` runs on simulated data.
  - `python -m imr ...` works too.
- **Scheduling:**
  - Linux: a **systemd user timer** (`imr-update.service` + `.timer`, created by `imr schedule install`), with a crontab line as the alternative;
  - suggested time 18:30 IST, after NSE publishes FII/DII.
- **Portability:** the code stays portable (pathlib, no shell calls), so macOS and Windows still work. The README has a short section for each, including the Windows Task Scheduler. There are no `.bat` files.
- **Errors:** logs go to `logs/imr.log`, rotated. Every error message ends with "What to do next: …".
- **Python 3.14 check:** in Milestone 1 I'll confirm that Streamlit, pyarrow and yfinance install cleanly on Python 3.14. If they don't, I'll tell you and suggest a 3.12 virtual environment instead of guessing.

## 8. Tests (offline, pytest)

These are synthetic-data tests with known answers:
- the decomposition recovers planted betas (within tolerance);
- a deliberately leaky signal (using day t+1 data) is **caught** by the look-ahead guard, while the honest version passes;
- the event study recovers a planted +1% day-0 effect, and finds nothing on random dates;
- on pure random-walk data, at least 90% of pattern tests return "No reliable evidence" (checked across many seeds);
- holiday mismatches:
  - a US holiday maps to a zero change;
  - two US sessions between Indian dates are summed;
  - an Indian holiday does not shift US data forward or backward;
- walk-forward never lets a fold see its own test block;
- verdict rules are unchanged (|t_in| ≥ 3 for "Strong");
- database:
  - incremental updates add only missing days;
  - `source` and `downloaded_at` are always filled;
- the NSE flow fetcher falls back to CSV on HTTP 403 or a timeout. This uses a mocked network; nothing touches the internet.
- the expiry calendar is generated correctly from a rule table, including the holiday shift;
- a parity test confirms the new package matches the prototype's numbers on demo data.

## 9. Milestones

I'll stop after each milestone, run the tests, show you what works, and wait for your feedback.

1. **Data layer:**
   - SQLite schema, incremental Yahoo download with fallbacks, NSE flows with CSV fallback;
   - events, expiry rules and holidays loading; data-health checks; demo database; logging; the `imr update` and `imr check` commands; tests.
2. **Analysis engine + tests:** refactored modules, fixes 1–7 above, walk-forward, stability, the FII/DII factor, RBI and expiry event studies, the test registry, and the full synthetic-data test suite.
3. **Dashboard:** the six Streamlit pages, demo badge, disclaimer, and HTML export.
4. **Scheduling + docs:** `imr schedule install` (systemd timer), cron and Windows/macOS notes, and a README for non-developers covering setup, daily use, every page, how to read t-statistics and verdicts, and limitations. Finish with a fresh-install check.

## 10. Questions for you (I won't guess these)

1. **F&O expiry rules.** I'll put this draft table in `config/expiry_rules.csv`, but every row is marked **unconfirmed** until you check it against NSE and BSE circulars. I know the rules changed several times and I'm not certain of the exact dates.

   | exchange | contract | rule (draft, UNVERIFIED) |
   |---|---|---|
   | NSE | Nifty / Bank Nifty monthly | last Thursday of the month (moved to last Tuesday around Sept 2025?) |
   | NSE | Bank Nifty monthly | did it move to last Wednesday for a period around 2023–24? |
   | BSE | Sensex / Bankex monthly | last Thursday → last Friday (around 2023?) → Tuesday (around 2025?) → Thursday (around Sept 2025?) |
   | both | holiday rule | if the expiry day is a holiday, the previous trading day |

   Can you confirm or correct these, or point me to the circulars? Until then, expiry event studies will run but carry an "unconfirmed rules" warning.
2. **RBI policy dates.** Please supply them, or confirm that you'll paste them from rbi.org.in into `config/events.csv`. I won't type any from memory.
3. **FII/DII history.** As far as I know, NSE's public endpoint only gives the **latest day**, not history. So the automatic fetch builds history going forward from the day you start. For past years you'd need to import a CSV, for example from NSE's archives or NSDL. Is that acceptable? Do you already have a source for historical flows?
4. **Python version.** I'll require Python 3.11 or newer. Your machine has 3.14, which is fine if the dependencies install (checked in Milestone 1).
5. **Default trading cost.** Keep 0.03% per side, or set it to your real brokerage + STT + slippage?
6. **Old files.** OK to move the prototype into `legacy/` once the parity test passes?
