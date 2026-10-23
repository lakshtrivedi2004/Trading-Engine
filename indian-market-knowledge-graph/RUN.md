# How to run the scalper

The engine trades liquid NIFTY-50 stocks intraday (NSE cash, MIS) through Angel One, with **₹12,000 configured capital**, a **₹35 per-trade stop budget**, and a **₹75 daily kill switch**.
The design is explained in `scalper/README.md`. This file covers only how to run it.

---

## 1. One-time setup

**Python.** You need Python 3.11 or newer with these packages:
```bash
pip install pandas numpy requests websocket-client      # already present on this machine
```

**`.env`** (project root). It must contain:
```
ANGLE_ONE_API=...        # SmartAPI key
ANGLE_ONE_ID=...         # client code
ANGLE_ONE_PIN=...        # login PIN
ANGLE_ONE_TOTP=...       # TOTP secret (the base32 text behind the QR code, not a 6-digit code)
TELEGRAM_BOT_TOKEN=...   # optional - trade alerts
TELEGRAM_CHAT_ID=...     # optional
```
Keep `.env` private: never commit or share it.

**Static IP (needed for live only).** NSE's retail-algo rules require API orders to come from a static IP registered in the Angel One SmartAPI dashboard (smartapi.angelone.in → My Apps → Primary Static IP). Put the same IP in `.env`:
```
STATIC_IP=14.99.35.174
```
- `./run.sh live` refuses to start if your current public IP doesn't match `STATIC_IP`. It prints both addresses. `SKIP_IP_CHECK=1` overrides this.
- During the session, the engine re-checks the IP every 60 seconds. If it changes, new entries are blocked and you get a Telegram alert.
- Airtel home broadband IPs are usually dynamic. Check `curl -4 -s https://api.ipify.org` for a few days, and if it changes, get an Airtel static IP or use a cloud server.

Paper mode and backtests work without a static IP.

**Make the script executable:**
```bash
chmod +x run.sh
```

**Funds.** Keep at least ₹12,000 free in the Angel One account for live trading.

---

## 2. The commands

| Command | What it does | Places orders? |
|---|---|---|
| `./run.sh test` | runs the test suite | no |
| `./run.sh sim` | offline check on synthetic data | no |
| `./run.sh backtest` | downloads 45 days of 1-min history, then runs a walk-forward test | no |
| `./run.sh paper` | live prices, simulated fills, dashboard | no |
| `./run.sh live` | **real orders**, dashboard | **yes** |
| `./run.sh dashboard` | dashboard only | no |

Extra arguments pass straight through:
```bash
./run.sh backtest --days 60
./run.sh paper --symbols SBIN,ICICIBANK,INFY
./run.sh paper --no-gate                 # paper only: trade every setup, even ones without recent edge
./run.sh live --capital 12000 --max-daily-loss 75
```
### Aggressive mode + index options
```bash
./run.sh paper --aggressive                 # every setup that passes sizing, stocks + NIFTY/BANKNIFTY/SENSEX options
./run.sh live  --aggressive                 # same with real money (asks for LIVE)
./run.sh paper --aggressive --no-stocks     # index options only
./run.sh paper --aggressive --indices NIFTY,SENSEX
./run.sh live  --aggressive --max-daily-loss 500   # index options need a bigger loss limit (see below)
```
- In **paper aggressive** mode, the backtest gate is bypassed so setups can be observed. In **live aggressive** mode, index options are enabled, while stock entries still require a positive recent after-cost edge.
- Every entry must fit the stop-out budget and clear the after-cost reward/risk threshold (1.2 by default).
- Index signals come from the index's 1-minute chart. LONG buys a CE and SHORT buys a PE, with **buying only**.
- **Strike choice:** implied volatility from live option prices, then Black-Scholes delta. The strike must meet all of:
  - stop-out loss including option charges fits the remaining daily budget,
  - premium × lot fits the capital,
  - net R:R ≥ 1.2.
- **Option exits:** premium stop or target, the index hitting its stop or target level, a 15-bar time stop, and 15:08 end of day.
- **Reality check (live quotes, 5 Oct 2026):** one NIFTY lot with a normal ~10-point stop loses ₹250–535 including charges. SENSEX loses ₹360–490. A BANKNIFTY lot needs ₹26k+ of premium.
  - With the default `--max-daily-loss 75`, no strike fits, so the engine logs that no option fits and trades only stocks.
  - Index options need roughly `--max-daily-loss 500` or more.

Environment switches:
```bash
PORT=9000 ./run.sh paper     # dashboard on another port
NO_DASH=1 ./run.sh live      # no dashboard
NO_WAIT=1 ./run.sh paper     # start now, even before 09:00 IST
```

---

## 3. The path to real money (do it in this order)

1. **`./run.sh test`**: all tests should pass.
2. **`./run.sh backtest`**: read the **OUT-OF-SAMPLE** block, not the in-sample one.
   - "enabled = NONE" means no setup has had an edge after charges lately, and the engine will stay flat. That is a valid answer.
   - Look at the worst day: it should never be much below -₹75.
3. **`./run.sh paper` for 1–2 weeks.** Check the dashboard and `scalper/logs/trades_paper.csv` every day.
   - Go live only if paper P&L after charges is positive over 10 or more sessions.
4. **`./run.sh live`**: register the static IP first. The engine asks you to type `LIVE`.

---

## 4. Daily routine

| Time (IST) | What happens |
|---|---|
| before 09:00 | run `./run.sh paper` or `./run.sh live`; the script waits until 09:00 |
| 09:00–09:15 | login, download history, re-test the strategy gate on the last 30 days, connect the websocket |
| 09:30 | first entries allowed (after the 15-minute opening range) |
| 09:30–14:45 | trading: one position at a time, at most 4 trades |
| 14:45 | no new entries |
| 15:08 | everything flattened, session logged out, engine exits |

Open **http://127.0.0.1:8050** to watch. The dashboard starts automatically with paper and live.
Use the PAPER / LIVE toggle at the top right to choose which engine you are viewing.

### Market-context rules (data from `india_market_research/`)
| Condition | Engine behaviour |
|---|---|
| NSE holiday | doesn't start |
| Union Budget / RBI policy / election-result day (listed in `india_market_research/config/events.csv`) | doesn't start |
| NIFTY weekly expiry day (Tuesday) | per-trade risk halved |
| India VIX ≥ 20 | per-trade risk halved |
| India VIX ≥ 28 | no new trades |

Add RBI policy dates to `india_market_research/config/events.csv` (type `RBI policy`) so those days are skipped.

The backtest caches history in `scalper/data/hist/`, so running it again on the same day doesn't log in again.

---

## 5. Switching between paper and live
- **Only one engine runs at a time.** Paper and live share one Angel One login, so stopping one would log the other out. Stop the running one with Ctrl+C before starting the other. A second start is refused with "PAPER/LIVE engine is already running".
- **They never share money state.** Each mode has its own day P&L and ₹75 budget, kill-switch file, trade journal and status. Paper losses don't reduce the live budget.
- **The dashboard follows whichever engine is running.** A green dot marks the running mode, and the pill shows `LIVE · trading` or `PAPER · trading`. You can still click the other mode to look at it.
  - It runs in the background and survives switching sessions. Stop it with `./run.sh stop-dashboard`.

## 6. Stopping, the kill switch, and restarts

- **Stop manually:** press `Ctrl+C`. The engine closes any open position, cancels its orders and logs out.
- **Kill switch:** fires when realised plus open loss after charges reaches -₹75. It then:
  1. flattens everything,
  2. writes `scalper/state/KILLED_<date>_live` (or `_paper`),
  3. exits and sends a Telegram alert.

  `run.sh` refuses to trade again that day. Don't delete the KILLED file to get around it.
- **Restart the same day** (crash, reboot): just run it again.
  - It reloads the day's realised P&L from `scalper/state/day_<date>_<mode>.json`, so the ₹75 budget carries over.
  - In live mode it refuses to start if an intraday position is already open at the broker. Close it in the Angel app first.
- **If the PC or internet dies with a live position:** the stop-loss order already sits at the broker and still protects you.
  The target is not at the broker, so check the Angel app. Angel also auto-squares-off MIS positions around 15:15–15:20.

---

## 7. Where things are

| Path | Contents |
|---|---|
| `scalper/logs/trades_paper.csv`, `trades_live.csv` | trade journal (entry, exit, charges, net, day P&L) |
| `scalper/logs/paper_YYYYMMDD.log`, `live_YYYYMMDD.log` | full engine logs |
| `scalper/logs/backtest_oos_trades.csv` | out-of-sample trades from the last backtest |
| `scalper/state/gate.json` | today's enabled setups and their stats |
| `scalper/state/status_*.json` | live snapshot read by the dashboard |
| `scalper/config.py` | every limit and parameter (capital, ₹75 daily stop, ₹35 per trade, times, universe) |

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `loginByPassword ... Invalid totp` | `ANGLE_ONE_TOTP` must be the base32 secret. Also check that the PC clock is synced (`timedatectl`). |
| live orders rejected, IP-related message | register the static IP in the SmartAPI dashboard |
| `market data feed did not connect` | check internet or a firewall blocking `wss://smartapisocket.angelone.in` |
| dashboard says `stale` / `offline` | the engine isn't running or has crashed; check the latest log in `scalper/logs/` |
| `Kill switch already fired today` | working as intended; it trades again on the next session |
| no trades all day | the gate found no setup with edge, or no signal passed the after-charges check; see `state/gate.json` and the log |
| `Open positions already exist ...` | you hold a position the engine didn't open. Close it in the Angel app, or start with `--allow-existing`: the engine then leaves it alone (no trading, closing or P&L counting) |
| `not found in scrip master` | the symbol was renamed or delisted; remove it from `--symbols` or `config.py` |
