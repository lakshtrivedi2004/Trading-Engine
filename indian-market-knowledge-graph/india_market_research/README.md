# IndexLab — India Index Research Platform

A research platform that runs on your own computer for **Nifty 50, Sensex, Bank Nifty, FinNifty and Bankex** and every BSE stock. It:

- **Explains** why each index moved today: how much came from global markets, oil, rates and the rupee, and how much was domestic.
- **Describes** the market's condition: trend, volatility, drawdown, India VIX.
- **Studies events:** how the indices usually behave around US shocks, VIX spikes, budgets, elections and expiry days.
- **Tests patterns honestly:** on data the rule has never seen, after costs, with a strict standard of evidence.
- **Forecasts** the next session's move size and direction, always shown with the forecast's measured track record.
- **Scans BSE stocks** for the biggest gainers and losers and the most volatile stocks: a watchlist.
- **Sends updates to Telegram** every evening, and answers commands.

> **Not advice.** Everything here is statistics on past data. Forecasts are estimates with a measured accuracy, not certainties. The stock scanner is a watchlist, not a list of trades. Decisions are yours.

---

## 1. Setup (once)

You need Python 3.11 or newer (`python3 --version`). In a terminal:

```bash
cd ~/indian-market-knowledge-graph/india_market_research
python3 -m venv .venv            # a private Python just for this project
source .venv/bin/activate        # switch it on (do this in every new terminal)
pip install -e .                 # installs everything and the 'imr' command
imr update                       # first download: ~10 minutes (16 years of indices + 1 year of BSE stocks)
```

Want to look around first without internet? Run `imr demo`, then `imr dashboard`, and choose **Demo** in the sidebar. Every number in demo mode is simulated and labelled so.

## 2. Daily use

```bash
source .venv/bin/activate
imr update        # after ~6:30 pm IST: new prices, FII/DII flows, BSE stocks, checks, Telegram messages
imr dashboard     # opens http://127.0.0.1:8501 in your browser (Ctrl+C to stop)
```

### Make it automatic

```bash
imr schedule install --bot       # update every weekday at 18:45 India time + keep the Telegram bot running
loginctl enable-linger $USER     # (once) keep them running even when you're logged out
imr schedule status              # check
imr schedule remove              # undo
```

No systemd? `imr schedule install` also prints a `crontab` line you can use instead.

## 3. Telegram bot

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name, and copy the **token** it gives you.
2. Run `imr telegram setup`, paste the token, then send `/start` to your new bot when asked.
3. Run `imr telegram test`. You should receive a message.
4. Keep the command bot running with `imr telegram run`, or automatically with `imr schedule install --bot`.

**Messages it sends you after each update** (turn each on or off in `config/settings.toml` → `[telegram]`):

- **Daily summary:** each index's move, why it moved, the regime, tomorrow's forecasts with their track record, and the top BSE movers.
- **Unusual-move alert:** when an index's unexplained move is more than twice its normal size.
- **Data problems:** a failed download, stale data, or NSE blocking the FII/DII fetch.
- **Weekly pattern check (Fridays):** which pattern verdicts changed.

**Commands:** `/today` `/movers` `/forecast` `/regime` `/health` `/patterns` `/status` `/help`

The bot answers **only your chat**. Anyone else gets "This bot is private". The token is kept in `config/telegram_secret.toml`, which only you can read. Never share that file. Every message also appears on the dashboard's **Updates** page, even when Telegram isn't set up.

## 4. The dashboard pages

| Page | What it shows |
|---|---|
| **Today** | Index cards (close, day's move, trend, tomorrow's typical move), why each index moved, regime, top gainers and losers, and data health with "what to do next". |
| **What moved** | Any of the last 250 days, split into each factor's contribution plus the index-specific part. Shows recent sessions, how much the factors explain (R²), and sensitivities over time. |
| **Stock movers** | All BSE stocks that traded, filtered for liquidity. Lists: gainers, losers, **unusual** (big compared with the stock's own normal) and **most volatile**. Columns include ATR, expected next move, volume surge, turnover, and circuit and trade-to-trade flags. |
| **Forecast lab** | For each index, tomorrow's chance of an up close and expected move size. Each comes with how it actually performed on unseen data and a plain verdict, plus charts of forecast vs reality. |
| **Regime** | Price with 50/200-day averages, drawdown, volatility percentile, India VIX, correlation heatmap. |
| **Events** | Average path of the index around each type of event, with number of cases, t-statistics and warnings. |
| **Pattern lab** | Run the pattern tests with your costs, split and walk-forward. In-sample and out-of-sample results side by side, verdicts, near-duplicates merged, a stability chart, and how many tests have been run. |
| **Flows** | FII/DII daily and cumulative flows next to Nifty. |
| **Updates** | Telegram status and the history of every update. |

The **Export HTML snapshot** button on Today, or `imr report`, saves a one-file report in `reports/`.

## 5. How to read the numbers

- **t-statistic:** how far an effect is from zero compared with its noise.
  - |t| ≈ 2 happens by luck about 1 time in 20, and the platform runs dozens of tests, so some will hit 2 by luck.
  - That's why a pattern needs |t| ≥ 3 in older data **and** must keep working on newer data.
  - All t-statistics use Newey-West errors, which allow for calm and wild periods clustering together.
- **Pattern verdicts:**
  - 🟢 **Strong:** strict bar in-sample and it held on unseen data.
  - 🔵 **Moderate:** weaker, but held in the same direction.
  - 🟠 **Faded:** looked good, then failed on newer data.
  - ⚪ **No reliable evidence:** indistinguishable from noise. This is the normal result.
- **Forecast verdicts:** a forecast is judged against a dumb baseline using the Diebold-Mariano test.
  - "Direction" baseline: the usual share of up days.
  - "Move size" baseline: the last 20 days' average move.
  - **"No better than the baseline"** means: treat it as a coin flip. On your data, direction forecasts get this verdict for every index, and FinNifty's is even worse than the baseline. Move-size forecasts beat the baseline for Bank Nifty, FinNifty and BSE stocks.
- **Near-duplicates:** Nifty and Sensex form one family; Bank Nifty, FinNifty and Bankex form another. The same result across a family counts as one finding.
- **σ (sigma) in the scanner:** today's move divided by the stock's normal daily move over the previous 20 days. ±3σ is rare for that stock.

## 6. Your files (all in `config/`)

| File | What to put in it |
|---|---|
| `settings.toml` | Costs (`cost_per_side`: set your brokerage + STT + slippage), scanner filters, Telegram switches, tickers. |
| `events.csv` | Event days, e.g. `2026-10-08,RBI policy,MPC decision`. **RBI dates are not filled in:** copy them from rbi.org.in. |
| `expiry_rules.csv` | F&O expiry rules with date ranges. All rows are **unconfirmed drafts**: check them against NSE/BSE circulars and set `confirmed=yes`. |
| `manual_prices/` | **Bankex history.** BSE blocks automatic downloads, so download it from bseindia.com (Indices → archives) and save the CSV here with a name starting `Bankex` (several files are fine). BSE's export format is read as-is. |
| `fii_dii_manual.csv` | Older FII/DII history if you have it (NSE only publishes the latest day). |
| `holidays.csv` | Older exchange holidays (this year's are downloaded automatically). |

## 7. Where the data comes from

| Data | Source | Notes |
|---|---|---|
| Nifty 50, Sensex, Bank Nifty, global factors, India VIX | Yahoo Finance | Free but imperfect; data health flags gaps and bad prints. |
| FinNifty | NSE website (official) | Yahoo has no history. |
| FII/DII flows, trading holidays | NSE website | Latest day only; history builds up daily. |
| BSE stocks | BSE "bhavcopy" daily file (official) | About 5,000 stocks; one year kept. |
| Bankex | Your CSV from bseindia.com | BSE blocks automatic index downloads. |

Everything is stored in `data/market.db` (SQLite) with its source and download time. Nothing is ever invented or filled in. Clear data errors in factor series (a jump that reverses the next day) are excluded and listed on the Today page.

## 8. Research standards built in

- **No look-ahead:** every rule is automatically re-checked with future data deleted, and refused if its signal changes. US data is matched to the next Indian session.
- **Out-of-sample:** a chronological split with a gap, or walk-forward. Forecast models are refit monthly using only the past.
- **Costs** are deducted on every trade.
- **Multiple testing:** every test ever run is counted, and near-duplicate indices are grouped.
- **Method sources:**
  - Roll (1988); Cutler, Poterba & Summers (1989): decomposition.
  - MacKinlay (1997): event studies.
  - Newey & West (1987): robust t-statistics.
  - Harvey, Liu & Zhu (2016): the t ≥ 3 bar.
  - Corsi (2009): HAR volatility model.
  - Diebold & Mariano (1995): comparing forecasts.
  - McLean & Pontiff (2016): fading anomalies.
  - Trend, momentum and calendar-effect papers are cited in the code.

## 9. Known limitations

- **Bankex** needs your manual CSV. Yahoo's Nifty and Sensex history has about 40 missing days compared with NSE (shown in Data health).
- **Event studies:** RBI dates and confirmed expiry rules are needed for those two studies.
- **Direction forecasts** for liquid indices show no edge, which matches decades of research. Move-size forecasts are the useful part.
- **BSE groups:** the scanner uses only groups A and B by default. Circuit flags are approximate (they assume standard 2/5/10/20% bands).
- **Yahoo and NSE** can change or block their free data at any time. The platform then says so and keeps working with stored data.

## 10. Commands

```
imr update [--offline] [--no-notify]   download new data, check it, send updates
imr dashboard [--port N]               open the dashboard
imr demo                               build the simulated demo database
imr status | imr check                 what is stored | re-run data checks
imr report [--open]                    save the HTML snapshot
imr notify                             prepare/send due updates now
imr telegram setup|test|run|status     Telegram bot
imr schedule install [--bot]|remove|status
```

Problems? Every error ends with **"What to do next"**. Full details are in `logs/imr.log`. Run the tests (offline) with `pip install -e ".[test]" && pytest`.
