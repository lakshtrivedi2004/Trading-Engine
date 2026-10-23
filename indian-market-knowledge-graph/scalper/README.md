# scalper: calculation-driven intraday scalping engine (Angel One, NSE)

Risk limits: **₹12,000 configured capital**, at most **₹35 stop-out loss per trade**, and a **₹75 daily kill switch**, counted after charges.
When that limit is reached, the engine closes everything, writes `state/KILLED_<date>_live` and refuses to start again that day.

## Why NSE cash (MIS) and not NIFTY options or futures
* **Futures:** NIFTY futures margin is more than ₹1.5 lakh, so they don't fit ₹12k.
* **Options:** one NIFTY option round trip can cost about ₹70 in brokerage, STT, exchange fees and GST. That does not fit the default ₹75 daily stop with enough room for price risk, so the default system focuses on cash stocks.
* **Cash MIS:** liquid large-cap stocks can be sized to the share, so every trade's worst case fits the budget exactly.

## The pipeline
```
SmartStream ticks ─► 1m bars ─► features (VWAP, ATR, ADX, efficiency ratio, rel-vol, z, OR, PDH/PDL, BB-width pct)
   ─► regime (TREND_UP / TREND_DN / RANGE / MIXED) + NIFTY direction filter
   ─► 5 setups: ORB15+relvol+VWAP | VWAP trend pullback | squeeze momentum breakout | liquidity sweep | VWAP z-score reversion
   ─► strategy gate: walk-forward over the last 30 days; a setup trades only if its shrunk net-R edge after charges is > 0
   ─► sizing: qty so that (stop distance + slippage) * qty + charges <= min(₹35, budget left today)
   ─► EV check: edge_R * max_loss > 0 and net R:R >= 1.2 after charges
   ─► marketable LIMIT entry (3s timeout) ─► broker-side STOPLOSS-LIMIT placed immediately
   ─► tick-level target/stop, cost-covering breakeven at 1R, ATR trail from 1.5R, 15-bar time stop, 15:08 flatten
```
Other risk rules:
* one position at a time, max 4 trades a day
* after 1 loss, a 30-minute pause
* stays under 8 orders/sec, below NSE's 10 OPS retail-algo threshold
* reconciles with broker positions every 5s
* logs out of the API session at the end of the day (an NSE requirement)

## Run it in this order
```bash
python3 -m scalper sim                    # offline check of the code paths (synthetic data has NO real edge)
python3 -m scalper backtest --days 45     # real Angel 1-min history: in-sample gate, then out-of-sample replay
python3 -m scalper paper                  # live data with simulated fills; run it for 1-2 weeks
python3 -m scalper live                   # real orders; asks you to type LIVE
```
Dashboard: in a second terminal run `python3 -m scalper dashboard` and open http://127.0.0.1:8050. It is read-only and local, and never talks to the broker. It shows day P&L, the loss room left on the ₹75 stop, the open position with its SL/entry/TP ladder, today's equity curve, live regime per symbol, the strategy gate, daily history, trades and the engine log. Use the PAPER/LIVE toggle to switch between engines.

Options: `--symbols SBIN,ICICIBANK`, `--capital 12000`, `--max-daily-loss 75`, and `--no-gate` (paper only). Live `--aggressive` enables index options but does not bypass the stock strategy gate.
Start the engine before 09:15 IST. It needs about a minute to load history and build the gate.

## Before going live
* **Static IP:** NSE's retail-algo rules require API orders to come from a static IP registered with Angel One (SmartAPI dashboard). Otherwise the orders will be rejected.
* If `backtest` or the startup gate shows **no strategy enabled**, the engine stays flat. That is the correct result, not a bug.
* Charges are about 0.25% per round trip at this size, so true tick scalping cannot pay for itself. The engine trades few, larger moves on purpose.

Outputs:
* `logs/trades_{paper,live}.csv`: trade journal
* `logs/*.log`: run logs
* `state/gate.json`: today's enabled setups and their stats
* `state/status_{paper,live}.json`: 1-second live snapshot read by the dashboard
* Telegram alerts for every entry, exit, kill and end-of-day summary
