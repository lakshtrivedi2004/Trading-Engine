# What the literature says, and what this repo does about it

Every paper below was read for one purpose: to find a claim specific enough to
change a line of code. Papers that only produce encouragement are not listed.
Where a finding could not be implemented, the reason is recorded rather than
quietly skipped — an unimplemented finding is information too.

Sections are ordered by how much they changed the system.

---

## 0. The stacking problem — why the engine took no trades (24 Sep 2026 rework)

From 22 to 24 Sep the engine took **zero** trades. The logs showed the
confirmation count was the least of it. An entry had to pass roughly a dozen
gates in series:
- a 15m × 20-bar day trend, which needs five hours of history;
- midday blocked outright, removing about four hours of the session;
- an ATR baseline that refused the first half hour after every restart;
- a close-position filter applied to the still-forming live bar;
- a premium/discount veto that refuses every break of structure by definition;
- a named setup;
- two confirmations;
- then the contract and cost checks.

A historical-data API that returned `AG8004 Invalid API Key` meant every
restart began with no history at all. `tools/funnel.py` replays the decision
layer bar by bar. On identical synthetic data, the old rules produced
**0.2 entries per session with full history and 0 on a cold start**.

The literature does not support AND-stacking. I found no paper that measures
it directly, so the argument is statistical. N roughly independent filters
that each pass with probability p let through p^N of bars. Every tuned
threshold also counts as another trial in the overfitting sense:

- **Bailey, Borwein, López de Prado & Zhu (2015)**, *Probability of Backtest
  Overfitting*, J. Comp. Finance. The more configurations tried, the worse the
  in-sample winner ranks out of sample. SSRN 2326253. (Abstract level.)
- **Harvey, Liu & Zhu (2016)**, *…and the Cross-Section of Expected Returns*,
  RFS 29(1). After this much data-mining, a new predictor needs t > 3.0, not
  2.0. SSRN 2249314. (Abstract level.)
- **Rapach, Strauss & Zhou (2010)**, RFS 23(2). Individual predictors fail out
  of sample; a combination of them does not, mostly because averaging reduces
  forecast variance. SSRN 1257858. (Abstract level.)
- **López de Prado (2018)**, *Advances in Financial Machine Learning*, and
  **Joubert (2022)**, *Meta-Labeling*, JFDS. A simple primary model with high
  recall picks the side. A secondary model, trained on triple-barrier labels
  (target / stop / time), filters and sizes. This is the structure the rework
  moves toward: the playbook is the primary model, and grade plus sizing is a
  hand-built secondary one. A *learned* secondary model needs a journal of
  real trades to train on, and it does not exist yet.

**Implemented.**
- One completed playbook is a trade (`min_confirmations = 1`, counting the
  setup itself).
- Other reads set a grade (A/B/C), and the grade sets the lot count.
- Extras that restate a step of the setup are not counted.
- The HTF trend is a preference, with a lower-timeframe fallback, and reversal
  playbooks may trade against it.
- Midday is allowed when ATR is at or above its own normal.
- An unknown regime is not refused.
- Breakout playbooks are exempt from the range-extreme veto.
- `min_grade` is the single dial for selectivity.

The **cost floor, structural stop, daily loss halt and square-off are
unchanged.**

### The educators the owner follows

- **Umar Punjabi.** One setup is sourced and mechanical: the Raj Shamani
  podcast FO414 transcript
  (https://www.cockatoo.com/content/become-crorepati-with-trading-day-trading-stock-market-secrets-umar-punjabi).
  - Setup: the first 30 minutes are the *initial balance*. Buy a break of its
    high, sell a break of its low.
  - Stop at the "point of no return" on the other side.
  - Risk about 2% per trade; reward-to-risk about 1:2 to 1:3.
  - Validate on 100+ trades before going live.
  - He trades Nasdaq and gold, not NSE options. Ported as
    `arb/playbooks.py:ib_breakout`, with a midpoint stop by default because
    his full-range stop on NIFTY exceeds the per-trade risk cap at ₹10,000.
    His own Telegram links a video titled "Umar Punjabi ADMITS He Lied About
    25% Monthly Returns"; weigh his claimed win rates accordingly.
- **Mr Star Sahil** (Sahil Kumar, @mrstarsahil). **No written, checkable rules
  could be found.** His material is video (live NIFTY scalps, "Zero to
  Operator", expiry-day "zero-hero" trades), and none of it was reachable as
  text. Nothing in this repo is attributed to him. The zero-hero idea, buying
  ₹1–5 far-OTM options on expiry afternoon, is deliberately **not**
  implemented. It is a lottery ticket by construction, and it conflicts with
  `min_delta` and with every sizing rule here.
- **"Psychology" as code** (`IntradayLimits` discipline block):
  - no revenge trade (`cooldown_after_loss_sec`);
  - a losing-streak pause, then B-grade-or-better only until a win;
  - a per-playbook daily cap;
  - a time stop, since a long option pays theta while nothing happens.
  - Kaminski & Lo (2014, J. Fin. Markets 18) is the case for stops: they only
    add value when returns have momentum, which is this strategy's premise.

### First look at real data (24 Sep 2026)

With the API key fixed, the history fetch returned 7 real sessions, 15 to 24
Sep, for NIFTY, BANKNIFTY and SENSEX.

**Funnel:** candidate entries per session per index were NIFTY 1.3 → 16.3,
BANKNIFTY 0.1 → 19.3 and SENSEX 1.4 → 16.1 (old rules → new rules).

**Outcome:** a rough index-point triple barrier. Each trade uses the setup's
stop, a 2R target and a 20-minute time stop, with one position at a time and
the daily caps. Cost was approximated as 3 / 7 / 10 index points round trip.
Theta and spread were not modelled, so treat these as upper bounds.

| Rule set | Trades | Win | Gross | After cost |
|---|---:|---:|---:|---:|
| New rules, no cost floor | 156 | 37% | −0.03R | −0.54R |
| + live `min_net_points` floor | 117 | 39% | +0.05R | −0.13R |
| + `max_cost_frac_of_risk = 0.10` | 51 | 55% | +0.28R | +0.21R |

The tight-stop setups (fib_pullback, psych_reaction, most ob_fvg_retest, with
9 to 23 point stops) were where the after-cost loss came from.

The 0.10 threshold comes from the rule that costs should be under 10–20% of
the trade's edge, and from Mesfin's friction result. It was not searched for.
Even so, it was the first and only value tried *after* seeing the unfiltered
result. At 51 trades, a +0.21R mean has a standard error of roughly ±0.17R.
That is **not evidence of an edge**; it only says the idea is not obviously
dead. Re-run it on each new week of history before trusting it.

### 0b. Trailing the stop to the last opposite swing (owner's rule, 24 Sep 2026)

**The rule (as the owner drew it).** At 1:1, move the stop to the last swing
that formed *after* entry: the latest lower high on a short, the latest higher
low on a long. It is never worse than breakeven plus costs, and it only
tightens. If a reversal pattern appears after 1:1 (an opposing engulfing or
rejection candle, or a CHoCH against the trade), the stop tightens to the
nearer swing.

**Where the stop and target live.** Both are on the index, not on the option
premium. Delta drifts as the index moves (gamma), and an IV drop can move the
premium with no index move at all, so a premium stop is a moving target.

**Target.** The next liquidity level, placed just short of it, because
take-profits cluster at round numbers: 9.3% execute exactly at "00", against
4.4% of stops (Osler 2003). The target is held between 1.5R and 4R. If the
bar at the target is still an impulse, the target extends and the stop locks
near the old target.

**Implementation.** `arb/trailing.py`, wired into
`arb/intraday_engine.py:manage` and updated once per completed bar.

**Real-data comparison.** Same 7 sessions and the same entries for every
variant, with the cost filters on. Numbers are net of the approximate cost:

| Exit rules | No time stop | 20-min time stop | 40-min time stop |
|---|---|---|---|
| Fixed 2R target, no trail | 35 trades, 40% win, **+0.00R** | +0.24R | +0.24R |
| Swing trail + structure target + extension | 40 trades, 58% win, **+0.24R** | +0.18R | **+0.25R** |

- **The trail protects the trade when nothing else does.** Without a time
  stop, the fixed target breaks even while the swing trail keeps +0.24R, and
  19 trades exit on a trailed stop in profit.
- **At 20 minutes the time stop closed about 70% of all trades** before the
  trail or the target could act. The default is now 40 minutes. That value was
  picked from 4 tried (0/20/40/60), and they differ by less than the noise, so
  treat it as a sensible middle, not a tuned optimum.
- Reading swings on 3- or 5-minute folds (`trail_timeframe`) changed nothing
  on this sample; the default stays 1.

**What the literature says.**
- **Kaminski & Lo (2014, J. Fin. Markets 18) [verified]:** under a random walk
  any stop lowers expected return; stops only help when returns trend.
- **Lo & Remorov (2017) [abstract only]:** tight stops lose to costs unless
  autocorrelation is high.
- **Baule, Schlie & Zhou (2025) [abstract, US data]:** intraday returns mostly
  reverse at around 15 minutes.
- **Breakeven at +1R:** no study found either way.
- **Partial profits:** a 1,432-trade trend system lost return on every
  partial variant (practitioner source). Partials are not implemented; at
  ₹10,000 there is usually one lot anyway.
- **Zarattini, Barbon & Aziz (2024):** no target, exit at the close. Supported
  here as `target_mode = "trail"`.

### 0c. The 5%-of-capital stop, and a momentum target (24 Sep 2026)

**The owner's rules.**
- Never risk more than 4–5% of capital on a trade.
- Set the target to what the market is easily making, judged by average
  movement and volume.
- Add each day's profit to the next day's capital.

All three were tested on the same 7 real sessions with the same entries, the
real round-trip costs (₹68–84 per lot) and the swing trail on:

| How the 5% cap is met | Trades | Win | Total | Worst loss |
|---|---:|---:|---:|---:|
| Tighten the stop to fit (`stop_policy="cap"`) | 102 | 36% | **−₹12,720** | −₹584 |
| Skip setups whose stop is too wide (`"skip"`) | 30 | 33% | −₹2,887 | −₹559 |
| **Keep the chart's stop and buy a lower-delta strike so it fits (`"delta"`, default)** | 51 | 51% | **+₹2,502** | **−₹584** |
| For comparison: 10% cap via delta | 54 | 54% | +₹6,092 | −₹1,084 |
| For comparison: 25% cap, ATM strike | 50 | 46% | +₹3,931 | −₹1,067 |

- **A stop pulled inside the structure gets hit by noise.** Rupee risk is
  index stop × delta × lot, so the delta is the thing to reduce, not the
  stop. The owner chose "don't tighten".
- **Momentum target** (`target_mode="momentum"`, in `arb/trailing.py`).
  - Take the median best excursion over the next 30 bars, in both directions,
    across the last ~2 sessions.
  - Scale it by volume now against its session normal (square root, clamped
    to 0.75–1.5×).
  - Set it just short of a major round number.
  - Skip the trade if that move doesn't pay at least 1:1.
  - At a 15-bar horizon or a 1.5R minimum it rarely fired, and when it did it
    lost. Matching the horizon to the hold time (40-minute time stop) is what
    made it work.
- **Caveat.** These settings are the best of roughly 25 variants tried on
  only 7 sessions. Some of the gap between them is luck. Recheck on every new
  week of data.

**Capital.** Every closed trade goes into a day ledger on disk
(`CapitalBook.record`). The day's total becomes capital when the next day
opens, or at the close. Sizing and the risk cap use the day's opening capital.
A mid-day restart resumes the day's P&L and trade count, so it can no longer
reset the daily loss halt.

### 0d. Choosing the risk cap by win rate, with a held-out check

The owner's instruction: "go with what increases the win rate; if the 5% rule
overcomes the win rate, remove it or change it."

**Method.** Sessions 16–21 Sep were in-sample and 22–24 Sep held out. The
entries were the same throughout: delta-sized, momentum target (median, 30
bars, 1:1), swing trail on.

**A broad search overfits.** I searched 63 combinations of cap × target
percentile × minimum R and picked the highest win rate in-sample, which
reached 58–62%. Every one of those fell to 36–41% on the held-out days, and
all of them lost money there. The current target settings (median, 1:1) were
the only ones that held: 53% in-sample and 48% held out, profitable in both
halves.

**The cap on its own, everything else fixed:**

| Cap | Win, 7 days | Win, held out | Total | Worst loss |
|---:|---:|---:|---:|---:|
| 4% | 41% | 38% | −₹122 | −₹468 |
| 5% | 51% | 48% | +₹2,502 | −₹584 |
| 8% | 54% | 48% | +₹5,429 | −₹884 |
| 10–25% | 54% | 48% | ~+₹6,100 | −₹1,084 to −₹1,165 |

**Decision: 8%,** the smallest cap that reaches the best win rate. Be clear
about what supports it:
- The whole 3-point gain (51% → 54%) came from the in-sample days. Held out,
  every cap from 5% to 25% scored the same 48%.
- It rests on 3 more winning trades out of about 54.
- What 8% certainly does is let more of the wide-stop setups through. Those
  setups were the profitable ones in every test in this file.

### 0e. Never trade a sideways market; read the premium chart (25 Sep 2026)

**The owner's rules.**
- "Never ever trade on a sideways market."
- Always judge the trade on the premium chart of the contract being bought.
- The premium can stay flat while the index moves, and decay silently eats
  the capital.

**Why a range kills a long option.** Carr & Madan (1998, eq. 9) give the
P&L of a delta-hedged long option over a short interval:

    P&L ≈ ½ · Γ · S² · (σ²_realised − σ²_implied) · Δt

A buyer only earns back theta when the index moves more than the implied move
for the interval, that is when |ΔS/S| > σ_IV·√Δt. In a range, realised
volatility sits below implied, and every minute held is a loss.

**The literature.** Every source below was checked against the paper or its
abstract, and none tested NSE intraday.

| Source | The finding that matters here |
|---|---|
| Carr & Madan (1998), *Towards a Theory of Volatility Trading*, Risk Books | The formula above. The effect is largest at the money and near expiry, which is the weekly contract we buy |
| Coval & Shumway (2001), *JF* 56(3) | Zero-beta at-the-money S&P straddles lose about 3% a week |
| Bakshi & Kapadia (2003), *RFS* 16(2) | Delta-hedged ATM calls lose about 8% of their value. They lose 21–25% when historical vol is above 14%. A volatile day alone does not help a buyer |
| Goyal & Saretto (2009), *JFE* 94(2) | Straddles earn −12.8% a month where implied vol is far above realised, and +9.9% where realised is far above implied |
| Carr & Wu (2009), *RFS* 22(3) | A long 30-day variance swap returns −66% on average (log excess return). The buyer pays the variance risk premium |
| Muravyev & Ni (2020), *JFE* 136(1) | Delta-hedged options lose −1% overnight and gain +0.3% intraday. Staying flat overnight avoids most decay |
| Jones & Shemesh (2018), *JF* 73(2) | Most option decay is priced over non-trading periods, mostly weekends |
| Bakshi, Cao & Chen (2000), *RFS* 13(3) | Intraday S&P calls often fall while the index rises, even after allowing for decay. This is the academic basis for the premium-chart rule |
| Hull & White (2017), *JBF* 82 | Index IV falls as the index rises, so a call's real delta on a rally is below its model delta |
| Beckmeyer, Branger & Gayda (2023), SSRN 4404704 | Retail traders lose $241k a day on 0DTE options. About 60% of that is costs, and the losses concentrate in single-leg, premium-paying, high-IV trades |
| Agarwal, Ghosh, Prabhala & Zhao (2025), SSRN 5430635 | NSE retail option volume is 90% day trading. Retail lost INR 135bn on BANKNIFTY options in the five years after weeklies launched |
| Lo & MacKinlay (1988), *RFS* 1(1) | The variance ratio test. A random walk has no persistent trend, and trendiness has to be measured against that baseline |

**What the real data says.** Two datasets were used.
- The 17 Sep book recording has 3 hours of real bid/ask for every subscribed
  option on 3 indices. From it we built 1-minute mid-price bars, plus an
  index proxy from put-call parity.
- We also used the cached 1-minute index candles for 21–25 Sep.

1. **Sideways after entry is what loses.** Trend-follow entries every minute
   on the real premiums were split by what the next window did:

   | Next 30 minutes | Samples | Win | Mean, per lot |
   |---|---:|---:|---:|
   | Sideways (efficiency < 0.3) | 540 | 41% | −₹54 |
   | Trending (≥ 0.3) | 130 | 98% | +₹2,784 |

2. **Sideways is not predictable from the index's own past.** This held on
   4 full sessions × 3 indices:
   - The efficiency ratio at 15, 30 or 60 minutes, the choppiness index, and
     recent ATR or range all had rank correlation −0.03 to +0.06 with how the
     next 30 minutes behaved.
   - Only about 22% of half-hour windows trend at all.
   - The mean 15-bar efficiency of real 1-minute bars is 0.261. A simulated
     random walk gives 0.257.

   At this scale the index behaves like a random walk. An index-only
   sideways filter mainly removes trades; it cannot foresee chop.

3. **The premium's own chart does help.** These are non-overlapping trades
   on the real 17 Sep premiums, one at a time, 30-minute hold, net of
   spread and about ₹75 fees:

   | Entry rule | Trades | Win | Total | Worst |
   |---|---:|---:|---:|---:|
   | Baseline, direction of the last 15 min | 25 | 48% | ₹5,730 | −₹1,565 |
   | Premium up over 10 bars | 25 | 60% | ₹15,449 | −₹838 |
   | Premium up over 10 bars by more than the round trip | 25 | 56% | ₹13,766 | −₹1,587 |
   | Index efficiency ≥ random walk, plus the row above (**default**) | 20 | 65% | ₹10,136 | −₹689 |
   | Early exit at 10 min if sideways and premium not up | 26 | 35% | ₹4,122 | −₹1,565 |

   The early "sideways exit" cut trades that would have recovered, so it was
   not built. The swing trail and the 40-minute time stop stay as the exits.

**Implemented** (about 30 lines):
- `arb/regime.py:regime_gate` refuses `index_sideways`. It fires when the
  efficiency ratio over the last `sideways_bars = 15` completed bars is below
  `min_trend_efficiency = 1.0` × 1/√15, the level a random walk reaches. The
  bar is derived from the window, not typed in. It has its own switch, so
  turning `use_regime_gate` off does not turn it off.
- `arb/regime.py:premium_gate` refuses `premium_not_rising` unless the
  contract's mid rose by more than its own round-trip cost over the last
  `premium_rise_bars = 10`. With no premium history yet it refuses
  `premium_chart_warming_up`: no premium chart means no trade.
- `arb/candles.py:CandleStore.premium` stores one mid-price row per minute
  for every subscribed option. It is built from ticks already received, so it
  costs no API calls. After a restart the premium chart needs about 11
  minutes to warm up.
- Funnel on 4 real sessions: candidate entries fall from about 17 to about 10
  per session per index before the premium rule. The premium rule cannot be
  replayed on those days because their premiums were not recorded.

**Not implemented, and why:**
- *An IV-based gate* (realised vol ≥ implied vol) is what Goyal-Saretto and
  Carr-Madan support most directly. It needs an IV per strike, and this
  system deliberately uses no pricing model. The premium-rise rule measures
  the net effect of gamma, theta and vega directly instead.
- *Premium response ratio* (ΔPremium / (δ·ΔIndex)). It is mostly covered by
  the premium-rise rule. It is worth adding once more premium history is
  recorded.
- *IV-rank cap, event IV crush, dealer-gamma grading.* These are candidates
  for grade and size inputs, not gates.

**Caveats, stated plainly.**
- About 20 variants were tried.
- The premium evidence is one afternoon: 20–25 non-overlapping trades on a
  day that trended.
- The index evidence is 4 days.
- This is not proof of an edge. Record the book (`TickRecorder`) on more
  days and re-run the replay before changing thresholds:
  `python tools/premium_replay.py data/book_<stamp>.f4`. On 17 Sep, across
  every in-ladder expiry, it gives:

  | Rules | Trades | Win | Total | Worst |
  |---|---:|---:|---:|---:|
  | Baseline | 22 | 55% | ₹5,616 | −₹1,565 |
  | Sideways + premium rules | 18 | 72% | ₹10,282 | −₹689 |
- The engine will now trade less often. That is the owner's explicit choice.

### 0f. A year of data: best times, Umar Punjabi's process, and 15 strategy families (25 Sep 2026)

**The owner's questions.**
- Find the best time and day to trade, the way Umar Punjabi waits for his A+ window.
- Is the belief right that premium holds in the first half and decays in the second?
- Add or improve strategies until both win rate and reward-to-risk are high.
- Test everything on data first, and drop whatever fails.

**Data.**
- 1-minute NIFTY, BANKNIFTY and SENSEX bars, 22 Sep 2025 – 25 Sep 2026: about 250 sessions each, fetched with `tools/fetch_history.py`.
- In-sample runs up to 25 May 2026; held-out is everything after. A rule counts only if it is positive in both periods.
- Real 1-minute premiums for 11 NIFTY 29 Sep 2026 strikes, 23 sessions.
- The engine's own decision and exit code, replayed by `tools/backtest.py`.
- Fees are ₹75 a lot, plus spread where stated. Delta is sized to the 8% risk cap.

**1. The current strategy has no edge over a year.** Rules as they were before this section:

| | In-sample | Held-out |
|---|---|---|
| All three indices | 912 trades, 41% win, −₹96,600, −0.16R | 451 trades, 44% win, −₹22,599, −0.07R |

- Before costs the entries average −0.02R on NIFTY (t = −0.2) and −0.08R on BANKNIFTY (t = −1.7). That is coin-flip level.
- Costs are about 0.11R a trade, and they make up the whole loss. This is Mesfin's friction ceiling (section 1) again, at 30× the sample of section 0.
- Option decay is not charged, so these figures are the best case.

**2. Time of day, weekday and expiry day.**
- 161 variants were tried, covering every entry window from 09:30 to 14:45 in 15-minute steps and every combination of weekdays to skip.
- The in-sample winners all failed held out. For example, 11:30–13:30 went from ₹+99 in-sample to −₹16,569 held out.
- Only 2 of the 161 were positive in both periods (11:15–12:00 and 11:15–12:15), for ₹640–1,779 total at under 50% win. With 161 tries, that is roughly what luck gives, so no time or day rule was added.
- Index movement does follow the usual U shape: most at the open, least 11:15–13:15, more into the close. Daily range barely differs by weekday.

**3. Umar Punjabi.**
- About 60 of his videos, podcasts and streams were read through their caption tracks.
- What he says about Indian markets: "liquidity 9 to 11, 11 to 1 sideways, 1 to 3 very difficult."
- His other rules:
  - bias from higher-timeframe structure;
  - "no sweep, no setup";
  - one trade a day on a fresh account;
  - a day off after two losses;
  - a weekday filter only if your own backtest shows it.
- His return claims are not audited. The "admits he lied" video is sarcasm: he says he makes 36%.
- Tested on the year:
  - His initial-balance breakout (IB 30 minutes, 5-minute close, stop at the IB midpoint, 2R, out by 12:30) lost on all three indices in both periods. NIFTY: −₹4,513 in-sample and −₹5,599 held out, 42% win.
  - None of 11 variants was positive in both periods.
  - "One trade a day", "stop after the first loss or win", "with the day trend" and "outside the IB" all lost too.
  - Nothing of his was added.

**4. Premium decay by time of day, from real NSE premiums.**
- The at-the-money straddle lost about 0.13% per 15 minutes in the first half and about 0.13% in the second.
- On flat-index minutes it lost 0.17% and 0.16%.
- So on non-expiry days the afternoon does *not* decay faster. This matches the literature:
  - Muravyev & Ni (2020): option buyers lose overnight, not intraday.
  - Bhat, Pandey & Rao (2024, *J. Futures Markets*): the same holds on NIFTY.
- The afternoon collapse is real on **expiry day**, for the expiring contract:
  - SEBI's 2024 consultation paper shows time value falling from ₹29.20 at 12:30 to ₹1.20 at 15:15.
  - Dim, Eraker & Vilkov (2024) find at-the-money 0DTE straddles bought after 14:30 lose 21–41%.
- No expired-contract history was available to test this here.

**5. "Sideways", eleven ways.**
- The index efficiency rule from section 0e lost *more* than no filter in both periods: −₹96,600 against −₹80,855 in-sample.
- Trading *only* in sideways tape was worst of all held out, at −₹49,257. So avoiding chop is directionally right.
- The definition that cut losses in both periods was an index moving at least 1.2× its own normal ATR: −₹27,779 and −₹1,171, with 43% and 49% win. It is still negative.

**6. Fifteen strategy families** (`tools/scan_signals.py`):
- opening-range break and fade;
- gap go and gap fade;
- previous-day high/low break and rejection;
- 60-minute momentum;
- 15-minute burst and reversal;
- NR7 breakout;
- trend day and trend-day pullback;
- TWAP reclaim;
- 5-minute EMA cross.

Only midday 60-minute momentum survived the first screen: at 12:15 or 13:15, follow a 60-minute move of at least 0.2 ADR for an hour. It scored +1.56 ATR in-sample (t = 3.7) and +0.79 held out, positive on all three indices. Once fees are converted at the lower delta that the risk cap forces, it falls to +0.01–0.05R held out with no decay charged. With any decay it turns negative, so it was not added. The literature's intraday-momentum effect exists here, but it is too small for ₹75-a-lot fees on a one-lot account.

**What changed.**
- `min_atr_ratio` 0.7 → 1.2.
- `sideways_bars` 15 → 0: the efficiency test is still available but off.
- `premium_rise_bars` 10 → 0: the premium-must-rise test is still available but off.
  - It conflicts with buying pullbacks, because a retest enters while the premium dips.
  - Approximated as delta × the index's 10-minute move, it halved ob_fvg_retest's trades (219 → 108). It turned the playbook from +₹1,849 / +₹88 into −₹3,564 / −₹1,947, and the win rate fell from 45% to 40%.
  - A "premium lags the index" version fits the owner's intent better. Only 28 entries on 2 days had real premiums to test it, so it was not added.
  - Re-test both once the minute recorder has a few weeks of data.
- `config/intraday.json` now sets `playbooks: ["ob_fvg_retest"]`. It is the only playbook not negative in either period under the new gate, at ₹+1,849 and ₹+88, but its R is about 0. The others lost in at least one period: choch_bos −₹58,727 in-sample, sweep_bos −₹9,234 held out, fib_pullback −₹4,737 held out.
- The intraday engine now records one book snapshot a minute (about 4 MB a day). Premium rules can then be tested on real option history, which Angel does not serve for expired contracts.

**The final configuration, replayed through the engine** (`python tools/backtest.py`, before decay):

| | In-sample | Held-out |
|---|---|---|
| Before these changes | 1,363 trades in total, −₹119,199 | |
| After | 273 trades, 43% win, −₹984, −0.00R | 115 trades, 45% win, −₹3,469, −0.07R |

That is about 96% less loss, almost entirely from taking fewer trades. It is still not an edge, and it does not pass the both-periods test.

**The bottom line, stated plainly.**
- Over a year, three indices, 15 families, 161 timing variants and 11 sideways definitions, no option-*buying* rule showed an edge that survived costs on held-out months.
- The literature explains why. Buyers pay the variance risk premium: Coval & Shumway, Bakshi & Kapadia, and Carr & Wu (section 0e). Retail 0DTE losses are about 60% transaction costs (Beckmeyer et al.). SEBI finds 88–93% of individual F&O traders lose.
- The structural edge sits with option *sellers*. That path (`allow_selling`) needs margin the ₹10,000 purse does not have.
- Until a rule passes `tools/backtest.py` on both periods, keep this engine on paper.

### 0g. Chasing win rate, and the 1.5× pace rule (25 Sep 2026)

**The owner's request.** Raise the win rate. The 72% seen in section 0e was 18 trend-following trades on one afternoon, 17 Sep. On a year of data those rules gave 41–51%.

**Method.** Each variant was one full engine replay on a year of three indices (`python tools/backtest.py --set ...`), about 20 variants in all. Option decay is not charged.

| Variant | Trade win rate | Year net | In-sample | Held-out |
|---|---:|---:|---:|---:|
| Settings of section 0f (pace 1.2) | 44% | −₹4,453 | −₹984 | −₹3,469 |
| Fixed 0.5R target | 61% | −₹28,264 | −₹24,877 | −₹3,387 |
| Fixed 0.5R, 5-point net floor | 65% | −₹32,809 | −₹25,784 | −₹7,025 |
| Fixed 0.75R | 53% | −₹35,401 | −₹28,597 | −₹6,804 |
| Breakeven at 0.5R | 49% | +₹818 | +₹5,253 | −₹4,435 |
| Easier momentum target (p30, 0.5R) | 51% | +₹4,270 | +₹8,900 | −₹4,630 |
| **Pace 1.5 (new default)** | **49%** | **+₹10,485** | **+₹4,848** | **+₹5,637** |
| Pace 1.4 / 1.6 / 1.8 | 45–51% | +₹2,912 / +₹8,786 / +₹2,069 | positive | positive |
| Pace 1.5 + breakeven 0.5R | 48% | +₹8,097 | +₹8,401 | −₹304 |
| Pace 1.5, all playbooks | 48% | −₹1,928 | −₹2,168 | +₹240 |

**Findings.**
- **A small target buys win rate with money.** At 0.5R the win rate reaches 61–66%, but the year loses 6–7× more. Fixed fees make a small win smaller than an ordinary loss. A high win rate on its own is not the goal.
- **Waiting for a genuinely moving index is the only change positive in both periods.** Every pace threshold from 1.4 to 1.8 was positive in-sample and held out, a plateau rather than a single lucky value. 1.5 is its centre.

**Periods at pace 1.5.**

| Period | Profitable | Average trade win rate |
|---|---:|---:|
| Day | 49% | 49% |
| Week | 50% | 47% |
| Month | 46% | 51% |
| Year | +₹10,485 | 49% |

**Caveats.**
- About 110 trades a year across three indices, so about one every two sessions.
- Only 76 in-sample and 33 held-out trades. Per-trade noise is about ±0.1–0.2R, so this is a hint, not proof. NIFTY alone was negative held out, on 4 trades.
- Decay is not charged.
- Re-check it on each new month of data.

### 0h. The premium's own movement, and checking the other side (28 Sep 2026)

**The owner's rules.**
- The lower the premium bought, the less it moves. Even near the money, one index point moves the premium only 0.5–0.6 points. So the stop and target must follow how far *the premium bought* actually moves, not the index.
- Before believing a move, look at the other side. When buying a call on an up move, find the put with the same |delta|. It should fall about as much as the call rises. Check the strikes above and below too.
- If the call rises but the put does not fall in the same way, something is wrong, and the move may be fake or made by institutions. Go with the institutions.

**Data.** Two recorded books, using the nearest non-expiring contract per index. Strikes with spreads above 3 / 6 / 8 points (NIFTY / BANKNIFTY / SENSEX) are blanked.
- 17 Sep 2026: 3 hours at 4 Hz, thinned to one snapshot a minute.
- 28 Sep 2026: 80 minutes from the engine's own one-a-minute recorder.

The SENSEX weekly on 17 Sep had broken quotes and dominated a first unfiltered pass, which was discarded. Replay with `python tools/crossside_replay.py data/book_*.f4`.

**1. The premium moves less than its delta says, and calls on a rally move least.**

| | Calls | Puts |
|---|---:|---:|
| Realised points per index point ÷ chain delta, 1-minute changes (strikes at 0.3–0.7 delta) | **0.74** (IQR 0.71–0.79) | **0.94** (IQR 0.85–0.99) |
| Same, 5-minute changes, per index-session | 0.71–0.86 | 0.83–1.27 |
| Actual 30-minute gain ÷ chain-delta forecast, on a favourable move | **0.43** (88 samples) | 1.19 (72 samples) |
| Actual 30-minute loss ÷ chain-delta forecast, on an adverse move (all expiries) | ~0.98 | ~0.84 |

- A call converted at chain delta **over-promised its 30-minute gain 90% of the time.**
- The losses arrive in full.
- This matches the literature exactly:
  - **Hull & White (2017)**, *Optimal delta hedging for options*, JBF 82 [verified by the research pass]. The minimum-variance delta sits below Black-Scholes delta in every bucket: calls are under-hedged and puts over-hedged. The cause is that index IV falls as the index rises.
  - **Simon (2013)**, JFM 33 [abstract]. Intraday SPY option moves deviate "often substantially" from delta projections, for the same reason.
  - **Derman (1999)**, *Regimes of Volatility* [secondary]. Realised delta depends on the volatility regime.
  - **Bakshi, Cao & Chen (2000)**, RFS 13(3). Calls often fall while the index rises.
- Kumar (2012) confirms that India VIX moves against NIFTY. No study of realised deltas on NIFTY weekly options was found; the recordings above are the evidence.

**2. The other side, measured properly.**
- A call and a put at the same |delta| sit at different strikes, with different skew and vega. An uneven move between them is normal, so "equal and opposite" is the wrong yardstick.
- Instead, each leg is judged against its **own** usual response to the index, regressed on the minutes before the window. The matched pair then splits into:
  - C − P, the index move the options priced;
  - C + P, roughly delta-neutral, which reads a volatility bid or crush.
- Over 5–10 minute windows, the pair's *lean* is the options-implied move minus the actual move. Its correlation with the next move:

  | Window → horizon | Correlation with next index move | t (overlap-adjusted, rough) |
  |---|---:|---:|
  | 5 → 10 min | +0.15 | ~0.9 |
  | 10 → 10 min | +0.24 | ~1.5 |
  | 5 or 10 → 30 min | −0.05 | ~−0.2 |

  The sign is what the owner's idea predicts: when the index runs ahead of what the options priced, it tends to come back. It is short-lived and **not significant** on two sessions.
- On a trade basis (10-minute momentum entries, one at a time, 30-minute hold, ask in, bid out, ₹75 a lot) there were 11 trades. The 4 that the read "confirmed" all lost. That is too few to mean anything either way, but it is not support.
- What the literature says:
  - **Goncalves-Pinto, Grundy, Hameed, van der Heijden & Zhu (2020)**, *Management Science* 66(9) [abstract]. When the stock and the option-implied price disagree, the predictability comes mainly from price pressure on the stock, which reverts toward the option-implied level. This is the mechanism behind "fake_move", and it is daily cross-sectional stock evidence, not intraday index evidence.
  - **Muravyev, Pearson & Broussard (2013)**, JFE 107(2) [abstract]. No economically significant price discovery in option quotes: when parity disagrees, the option quotes move. This is the strongest argument against treating "options_lead" as a signal.
  - **Chakravarty, Gulen & Mayhew (2004)**, JF 59(3) [abstract]. Options carry about 17% of price discovery.
  - **Holowczak, Simaan & Wu (2006)** [abstract]. Options are informative mainly during heavy option trading.
  - **Vipul (2008)**, JFM 28 [abstract]. NIFTY put-call parity gaps "do not persist even for two minutes". A one-minute divergence is mostly already gone.
  - **Chan, Chung & Johnson (1993)**, JF 48, and **Pérignon (2006)**, J. Derivatives 14 [abstract / working paper]. Most wrong-way option moves are stale quotes and bid-ask bounce. That is why this uses mids, a 10-minute window, a disagreement threshold in ATR, and the neighbour-strike check.
  - Cross-sectional IV-spread papers (Cremers & Weinbaum 2010; An, Ang, Bali & Cakici 2014; Xing, Zhang & Zhao 2010) work at weekly-to-monthly horizons on single stocks and say nothing about a 10-minute index read.
- **"Go with the institutions": what SEBI's Jane Street order (3 July 2025) actually shows [read in full by the research pass].**
  - From 09:15 to about 11:46, BANKNIFTY constituents and futures were bought aggressively while the firm was short calls and long puts. The cash and futures longs were then sold into the close.
  - SEBI says the pushed index left "artificially inflated call option premiums and subdued put option premiums". In other words, **the options priced faithfully off the manipulated index**, and a cross-side check would *not* have flagged it.
  - The footprint was in cash and futures aggression and in the time of day on expiry days. The better-supported institutional-flow ideas are expiry-day timing and dealer-gamma or OI concentration: Baltussen, Da, Lammers & Martens (2021, JFE); Barbon & Buraschi; Ni, Pearson & Poteshman (2005, JFE). None is built yet. They need OI by strike, which the feed records but nothing reads.

**Implemented** (`arb/optionflow.py`, wired in `arb/intraday_engine.py:hunt`):
- **Effective delta** (`use_realised_delta = True`).
  - The bought contract's own premium change per index point, regressed over the last `realised_delta_bars = 30` minutes on the minute premium chart.
  - Shrunk toward a prior (`realised_delta_prior_ce = 0.8`, `_pe = 1.0`) by R² × history length, and capped at the chain delta.
  - It sets the momentum target's premium estimate and the `min_net_points` cost check.
  - **Stops and sizing keep the chain delta**, because losses realised in full.
  - On the replay, calls' mean absolute error fell from 11.6 to 8.6 points, and over-promising from 90% to 78%. Puts got slightly worse (7.3 → 8.1), because the cap refuses to credit their larger moves. **Calls are still over-promised**: the one-minute fit misses the IV drift that builds over a 30-minute rally.
- **Cross-side read** (`cross_side_mode = "grade"`).
  - Picks the matched-|delta| opposite contract and the two same-type neighbours.
  - Classifies the last `cross_side_bars = 10` minutes as `confirmed`, `options_lead`, `fake_pullback` (a retrace the options did not price), `fake_move`, `options_against`, `pullback_priced` or `quiet`.
  - A disagreement must exceed `cross_side_lean_atr = 0.5` ATR to count.
  - A confirming read, with the ladder in order and no vol crush, is one more extra for the grade. It is never needed and never refuses.
  - `"gate"` refuses `fake_move` / `options_against`, and `cross_side_veto_crush` adds the vol crush.
  - Every trade journals both reads (`premium_per_point`, `cross_side`), so the next study can use real entries rather than a momentum stand-in.

**Not implemented, and why.**
- *A gate on the cross-side read.* Two sessions, t ≈ 1.5 at best, and 11 trades that lean the other way. By section 0's own lesson, a gate that cannot be shown to help only removes trades.
- *A lower call prior, or a horizon haircut, to close the remaining 30-minute gap.* It would be one number fitted on two sessions. Revisit it after a month of the minute recorder: rerun `tools/crossside_replay.py` and read the journal's `premium_per_point` against realised exits.
- *Using the front future instead of spot as the index series.* Khan et al. (2022) find NIFTY futures lead spot at one minute, which would reduce staleness further. The engine's stops live on spot, so the two are kept consistent for now.

### 0i. Stocks in play (28 Sep 2026)

**The owner's request.** When the three indices are not moving, trade the most volatile stocks instead, in both directions. That includes the NIFTY and BANKNIFTY constituents on their own ("HDFC falls more than the other banks"). Find them with NSE and BSE data and trade them with the same rules.

**Why there were no trades on 28 Sep.**
- The engine was restarted four times between 10:41 and 10:44. The day's move was over by 09:50: the ATR ratio was 1.5–2.0× from 09:15 to about 09:50, then under 1.0× for the rest of the morning.
- Replaying the whole session's bars through `regime_gate` and `evaluate`, only 14–19 bars per index passed the pace gate. None completed an `ob_fvg_retest` setup.
- So nothing was broken. It is the selectivity of section 0g, about one trade every two sessions.
- Separately, the historical API refused calls for rate about every 5 minutes. The candle cache still held every bar from 09:15.

**What can be bought with ₹10–14k** [verified by the research pass from SEBI and NSE circulars].
- Stock F&O contracts are worth ₹3–17 lakh a lot (median ₹6.5 lakh; HDFCBANK ₹4.76 lakh; NSE/FAOP/73549).
- Stock options are monthly only and settle **physically** (SEBI/HO/MRD/DP/CIR/P/2018/67): an in-the-money option held to expiry becomes delivery of the full lot.
- Stock futures cannot be margined at all.
- A near-the-money stock-option lot costs more than the whole purse.
- **The cash market intraday (MIS) is the only way to trade a stock here.** SEBI's peak-margin rules (SEBI/HO/MIRSD/DOP/CIR/P/2020/146) cap leverage at 5×, so ₹10k controls about ₹50k.
- Costs:
  - STT 0.025% on the sell side, NSE 0.00307%, stamp 0.003% on the buy side, SEBI fee and GST.
  - Angel's intraday brokerage since 17 Nov 2025 is the lower of ₹20 and 0.1% per order, with a ₹5 minimum.
  - Round trip: ₹65.5 on ₹50k (0.13%) and ₹27.3 on ₹10k (0.27%).

**Literature.**

| Source | Finding | For this rule |
|---|---|---|
| Zarattini, Barbon & Aziz (2024), SSRN 4729284 [read in full] | US stocks. Take the top 20 by relative volume: first-5-minute volume ÷ its own 14-day average. Trade the 5-minute opening-range breakout, stop at 10% of ATR, exit at the close. Sharpe 2.81 against 0.48 unfiltered. Per-trade R: −0.02 below 1× RVOL, +0.08 above, +0.38 above 30×. Commission only; no spread or slippage; no held-out period | The **relative-volume filter is the edge**, not the entry. Built as `stocks_min_rvol` and a scoring weight |
| Parkinson (1980); Corsi (2009) HAR-RV | Range-based volatility is efficient and persistent at daily horizons | Rank on ATR%, not on close-to-close moves |
| Barber & Odean (2008), RFS [read in full] | Retail buys attention stocks on both big up and big down days | The crowd is in these names. That is flow, not an edge |
| Seasholes & Wu (2007); Lou, Polk & Skouras (2019); Branch & Ma (2012) | Extreme movers continue briefly, then revert. Overnight and intraday returns pull against each other. Stock gaps tend to fade | Do not trade the gap or yesterday's mover by direction |
| Hendershott, Livdan & Rösch (2020), JFE | Intraday, returns are *negatively* related to beta | High beta is no reason to prefer a stock |
| Heston, Korajczyk & Sadka (2010) | Sub-hour reversals are liquidity and bid-ask bounce | No 1-minute fades at retail cost |
| SEBI PR 13/2024 | 71% of individual equity intraday traders lost money in FY23, and 80% of those with 500+ trades a year. Loss-makers paid a further 57% of their losses in costs | The base rate |

No peer-reviewed study of relative volume or opening-range breakouts on NSE single stocks was found.

**Evidence from NSE's own daily bars** (`tools/stock_study.py`, 210 F&O stocks, 23 Sep 2025 – 25 Sep 2026, split at 25 May). Correlations are Spearman, computed per day and averaged, with t over days.

| | In-sample | Held-out |
|---|---:|---:|
| 14-day ATR% → today's range | **+0.43** (t 51) | **+0.40** (t 50) |
| Yesterday's range → today's range | +0.34 | +0.33 |
| \|Opening gap\| → today's range | +0.16 | +0.14 |
| Top-decile ATR stocks, mean daily range | 3.68% (rest 2.62%) | 3.21% (rest 2.36%) |
| Gap ≥ 2%: open-to-close in the gap's direction | −24 bp (t −3.9) | +22 bp (t +1.4) |
| Yesterday's top/bottom 10 movers: continuation next day | −5 bp | −17 bp (t −3.3) |
| Big movers on < 35% delivery: next day | −16 bp | −15 bp |
| Bank betas to BANKNIFTY, rank correlation window to window | +0.40, +0.54, +0.02 | |

- **Volatility persists; direction does not.** The list of stocks to watch can be drawn up before the open and holds up. Which way to trade cannot be read off the gap or yesterday's move.
- The extreme movers' next-day reversal, and the speculative movers' reversal, have the same sign in both halves. At 5–17 bp they are below the 13 bp round trip.
- **"HDFC falls more than the other banks" is not in the data.** Over the latest 60 days HDFCBANK's beta to BANKNIFTY was 1.08, mid-pack (AUBANK 1.28, AXISBANK 1.24, PNB 0.53). Bank betas reshuffle from one quarter to the next. HDFCBANK's beta to NIFTY is 1.40, because it is 17% of BANKNIFTY. Relative strength is therefore computed live every scan and reported, not assumed.

**Implemented.**
- `arb/nse.py` reads NSE's own public data, cached, paced and failure-tolerant:
  - top gainers and losers (F&O), volume gainers, most active, and the pre-open book for all 210 F&O stocks;
  - index members, the daily F&O ban list, and daily bars with delivery %.
  - `equity-stockIndices` refuses non-browser clients, so live ranks come from the engine's own websocket instead.
- `arb/scanner.py` ranks by ATR%, relative volume, move in ATRs and range so far in ATRs, as cross-sectional ranks. It filters:
  - price ≥ ₹100;
  - median turnover ≥ ₹50 cr;
  - ATR ≥ 1.5%;
  - relative volume ≥ 1.0;
  - not in the ban list.

  Relative volume uses the paper's same-minute definition where 1-minute history exists, and a U-shaped volume curve × average daily volume otherwise.
- `arb/stockdesk.py` and `run_bot.py`:
  - subscribes the liquid F&O cash tokens at startup;
  - picks the top `stocks_top_k = 3` at 09:30, and again every 30 minutes;
  - loads four days of 1-minute history for the picks only.
- `arb/intraday_engine.py:_hunt_stocks` runs **the same** regime gate, playbooks, stop, momentum target, trail and discipline rules on the picks, but only when the indices offered nothing.
  - Size is the lesser of (risk cap ÷ stop) and (free capital × leverage ÷ price).
  - The trade must clear its round trip by `stocks_min_net_inr`, and the cost must be at most `max_cost_frac_of_risk` of the risk.
  - Shorts are carried mirrored about their entry, so every existing exit rule reads them correctly.
- **`stocks_enabled = False`.**

**The test that decides it** (`tools/stock_backtest.py`). One-minute bars from Angel for all F&O stocks, 120 days, split at 10 Aug.
- The scanner's top-K picks are traded through the engine's own decision and exit code.
- Against them: K random liquid stocks, and the K *least* in play, on the same sessions.
- Plus the paper's 5-minute opening-range breakout on the same picks.
- The scanner earns its place only if its picks beat random picks after costs in both periods. Results are recorded below once the fetch completes.

### Also added from the literature

- `vwap_pullback`: from **Zarattini & Aziz**, VWAP trend on QQQ, SSRN 4631351.
  Their reported Sharpe of about 2.1 (2018–23) was seen only in secondary
  summaries and was earned before NSE option costs.
- Session VWAP is now today's bars only. It had been averaging several days.

### Base rates, stated plainly

- **SEBI, Sep 2024:** 93% of individual F&O traders lost money FY22–FY24.
- **SEBI, Aug 2026:** 90.9% lost in FY25 and 87.7% in FY26, and 87.7% of
  options traders lost. **Individuals flagged as algo users still lost
  ₹51,728 crore gross.** A bot is not an edge by itself.
- **Barber, Lee, Liu, Odean & Zhang (Taiwan):** under 1% of day traders
  predictably earn profits.

Nothing here is "unbeatable". The system can now trade, which means it can
now be *measured*. The next honest step is a few weeks of paper journal, then
a win-rate and R-multiple study per playbook and per grade. Only after that
should anything be switched off or sized up.

---

## 1. The friction ceiling — why most intraday signals are real and unprofitable

**Mesfin, M. (2026). *Structural Limits of OHLCV-Based Intraday Momentum Signals
in MNQ Futures: A Systematic Falsification Study.*** arXiv:2605.04004

Fourteen intraday signal families, 947 trading days of 5-minute bars on Micro
E-Mini Nasdaq futures (Dec 2021 – Aug 2025), expanding-window walk-forward with
three out-of-sample years. To pass, a signal had to clear five gates at once:
net-return T ≥ 2.0 out-of-sample, ≥ 30 trades per OOS fold, positive net return
after round-trip friction, consistent sign across 2023/2024/2025, and a
permutation p < 0.05.

**None of the fourteen passed.** The failures split three ways:

| Failure mode | Families | Detail |
|---|---|---|
| Gross edge below the friction floor | 11 | Gross 0.07–1.50 pts against a 2.0-pt round trip |
| Real edge, not enough evidence | 2 | ORB long net +2.82 pts but T = 0.88; VVG reversal net +13.49 pts on 35 trades, T = 1.26 |
| Year-unstable / too few trades | 1 | Gap continuation short, net +14.52 pts, T = 1.46 |

Opening-range breakout — the single most-recommended retail intraday pattern —
is in the second group: it makes money gross and cannot prove it after costs.

The two **positive controls** that did pass (RTH Confluence, OOS T = 3.11, mean
net +11.82 pts, N = 196; London Session B, OOS T = 4.30, p = 0.000025) differ
from the failures in exactly two ways:

1. They fire only inside a **classified volatility regime** — a 3-component
   Gaussian mixture over four features: 20-bar ATR ratio, 50-bar volume
   z-score, close position within bar range, and bar return.
2. They **hold 60–75 minutes**, not one bar. The author's own reading is that
   the longer hold is what lets net return accumulate past friction "before the
   edge reverts."

### What this means for us, specifically

Our entry signal is market structure read off 1-minute index candles
([arb/smc.py](../arb/smc.py)) — swing points, BOS, CHoCH, order blocks. That is
a single-bar directional prediction from publicly observable OHLC. It is
squarely inside the family the paper falsifies, and our costs are **worse than
the paper's**: MNQ pays ~2.0 points round-trip on a tight, deep book; we buy
NIFTY/BANKNIFTY options, paying the spread twice plus STT, exchange fees, stamp
duty, SEBI fee, IPFT and GST on a book that is far thinner.

**Implemented — cost-covered breakeven.**
[arb/positions.py:141-158](../arb/positions.py#L141-L158). The 1:1 breakeven
rule moved the stop to the fill price. Getting out costs what getting in cost,
so a stop resting exactly at entry books a full round-trip loss every time it is
hit — the trade was advertised as free and was not. Breakeven is now
`entry + cost_points`, the round trip already charged against that trade and
carried on the `Position`. Guarded so the new stop can never be placed at or
through the live mark, which would close the trade in the name of protecting it.
One flag back: `breakeven_covers_costs = False`.

**Implemented — a volatility regime gate.** [arb/regime.py](../arb/regime.py),
wired at [arb/intraday_engine.py:266](../arb/intraday_engine.py#L266). The
20-bar ATR against its own trailing 200-bar median, capped [0.5, 2.0]. Below
`min_atr_ratio = 0.8` the expected move is too small to clear its own round trip
and no trade is taken, whatever structure says. Rejections show on the dashboard
as `regime_too_quiet`.

One deliberate departure: the paper fixes its ATR baseline at 10.34 points over
the whole sample and flags it as mild look-ahead. We use a trailing median
instead — it costs nothing and removes the objection.

**Implemented — an impulse filter.** `min_close_position = 0.2`: the entry bar
must close in the outer 20% of its own range. This is the paper's third GMM
feature used directly as a gate rather than as a mixture component. A bar that
closes mid-range is indecision wearing the colour of a trend.

**Implemented — the volume z-score.** *(Was the top open item in the first
pass; now closed.)* The fourth GMM feature. An index has no volume of its own —
NIFTY is an average, not an instrument — so Angel's index candles carry none and
our live bars were built with a literal `0.0`. What *does* trade is the front
future on that index, and the feed was already recording its
`volume_trade_for_the_day` into the book ([arb/feed.py:185](../arb/feed.py#L185),
[arb/book.py:25](../arb/book.py#L25)) without anything reading it. Differencing
that cumulative counter per candle bucket produces a genuine per-bar
participation series for the index's most liquid proxy —
[arb/volume.py](../arb/volume.py), wired at
[arb/candles.py](../arb/candles.py). The front month is selected by parsed
expiry, not by list order. Without a usable series every volume-derived check
returns `None` and abstains, rather than reading a column of zeros as "quiet".

**Not implemented — the 60–75 minute hold.** Directly contradicts how this
system is currently built: 1-minute candles, `max_open_positions = 1`,
`trade_cooldown_sec = 60`, up to 15 trades a day. Taking it seriously means
fewer, longer trades — closer to four holds a day than fifteen scalps. It also
interacts with theta, which futures do not pay and we do. Flagged for a
decision, not changed unilaterally.

---

## 2. Time of day — where intraday predictability actually lives

**Gao, L., Han, Y., Li, S. Z., & Zhou, G. (2018). *Market Intraday Momentum.*
Journal of Financial Economics 129(2).** [SSRN 2440866](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)

S&P 500 ETF, minute data, 1993–2013. The **first half-hour return predicts the
last half-hour return**: scaled slope 6.94, significant at 1%, R² = 1.6%.
Replicated across ten other heavily traded domestic and international ETFs. The
effect is **stronger on volatile days, high-volume days, recession days, and
macro-announcement days** — which is the same conditioning variable as Mesfin's
regime gate, arrived at from a completely different direction.

**Heston, S. L., Korajczyk, R. A., & Sadka, R. (2010). *Intraday Patterns in the
Cross-Section of Stock Returns.* Journal of Finance 65(4).**
[arXiv:1005.3535](https://arxiv.org/abs/1005.3535)

Return continuation at half-hour intervals that are exact multiples of a trading
day, persisting at least 40 days. Continuation is **most pronounced in the first
and last half-hour**. Short-horizon reversal in between is driven by temporary
liquidity imbalance and bid-ask bounce — a cost a premium buyer pays and does
not collect. Volume, order imbalance, volatility and spreads show the same
periodicity but do **not** explain the return pattern.

**Implemented — a session-phase gate.** `session_phase()` in
[arb/regime.py](../arb/regime.py) labels the first hour `open`, the last hour
`close`, the rest `midday`. With `skip_midday = True` no new position is opened
in the middle of the session; the rejection reads `midday_chop`.

Both papers are US equity markets. NSE index behaviour is similar in shape —
a volatile open, a thin midday, an active close — but the boundaries here are
set by the clock (first/last 60 minutes of 09:15–15:30), not fitted to Indian
data. **Fit them before trusting them.**

---

## 3. Exit design — how much the stop and target are actually worth

**Optimal Stop-Loss and Take-Profit Parameterization for Autonomous Trading
Agent Swarm (2026).** [arXiv:2604.27150](https://arxiv.org/abs/2604.27150)

A parameter sweep over exit design. Baseline Sharpe 0.419 → 0.525 first pass →
**0.653 with an ATR-scaled overlay, a 56% gain**; profit factor 1.639 → 2.375.
The directional findings survive even if the specific numbers do not: **tighter
loss limits beat permissive ones**, **earlier partial profit-taking beats
holding for the full target**, and **moderate trailing distances beat wide
ones**. The authors explicitly warn this is in-sample without deflated-Sharpe
correction.

The most transferable result is that **exit design is not a rounding error** —
it moved Sharpe by more than half on an unchanged entry signal. That is the
argument for treating the breakeven rule as a first-class parameter rather than
a convention, which is what section 1 did.

**Battle, J. (2025). *A Simple Trading Strategy with a Stop-Loss and Take-Profit
Order.*** [SSRN 5859402](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5859402)

Derives conditions under which a stop-loss **increases** mean return while
reducing variance, and gives a closed-form approximation for the optimal stop
level with Kelly-like structure. Relevant because it shows the optimal stop
depends on the edge's own distribution — our `reward_risk = 2.0` and
`max_risk_frac_of_premium = 0.50` are conventions, not measurements.

**Leung, T., & Zhang, H. (2017). *Optimal Trading with a Trailing Stop.***
[arXiv:1701.03960](https://arxiv.org/abs/1701.03960)

Proves the optimality of pairing a trailing stop with a sell limit order rather
than using the trailing stop alone. Our `trail_step_r` ratchet has no paired
limit; the target is a separate hard level. Worth revisiting.

**Determining Optimal Stop-Loss Thresholds via Bayesian Methods (2016).**
[arXiv:1609.00869](https://arxiv.org/pdf/1609.00869)

Sets the stop from the empirical distribution of maximum drawdowns rather than
by choice. Once `journal/trades_*.jsonl` holds a few hundred trades, our
recorded MAE per position is exactly the input this method needs — the data is
already being collected.

**Not implemented — partial profit-taking.** The strongest single finding in
2604.27150 (75% off at the first threshold appeared in every top configuration).
Blocked by lot granularity: we trade **one lot**, which cannot be partially
closed. It becomes available at two lots, which requires roughly double the
current capital. Note it as the reason to grow the purse.

---

## 4. Method — how to know whether any of this worked

**Bailey, D. H., & López de Prado, M. (2014). *The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality.* Journal
of Portfolio Management 40(5).**
[SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)

The Probabilistic Sharpe Ratio asks whether a track record beats a benchmark
once skew, kurtosis and sample length are accounted for. The Deflated Sharpe
deflates that further by the number of trials run — because the headline Sharpe
of the best of N attempts is mostly a function of N. Their stated point: the
most important number missing from nearly every published backtest is **how many
variants were tried**.

**This applies to the changes in this very file.** Sections 1–3 add four new
parameters (`min_atr_ratio`, `min_close_position`, `skip_midday`,
`breakeven_covers_costs`). Each one is a trial. Tuning them against the same
handful of paper-trading days that motivated them will manufacture a backtest
and not an edge.

**The honest standard, adopted from Mesfin's five gates:**

| Gate | Threshold |
|---|---|
| T-statistic on **net** returns | ≥ 2.0, out-of-sample only |
| Trades per out-of-sample fold | ≥ 30 |
| Net return after full round-trip friction | > 0 |
| Sign consistency across separate periods | required |
| Permutation test | p < 0.05 |

At 15 trades a day that is roughly **two trading days per fold and three folds
minimum** before any claim about these parameters is worth making. Until then
the correct description of the changes above is *theoretically motivated*, not
*validated*.

---

## 5. The base rate nobody should skip

**SEBI (2023). *Analysis of Profit and Loss of Individual Traders dealing in
equity Futures and Options.*** Study dated 25 January 2023, covering FY 2021-22.

**9 out of 10 individual traders in equity F&O lost money.** Average net loss
among loss-makers ≈ ₹50,000. Loss-makers spent a further **28% of their net
losses** on transaction costs. Those who did profit gave back **15–50% of their
profit** in costs.

That last figure is the operative one. It is not a warning label — it is a
measured statement that transaction costs consume between a sixth and a half of
gross profit in exactly the instrument this system trades. It is the same
quantity Mesfin found killed eleven of fourteen signals, measured independently
on Indian retail F&O.

Every cost-related change in this file is aimed at that number.

**Related:** *Animal Spirits on Steroids: Evidence from Retail Options Trading
in India* — [FMA Derivatives 2025](https://www.fma.org/assets/docs/Derivatives2025/Zhao.pdf)

---

## 6. Smart Money Concepts, taken literally

SMC has no peer-reviewed literature behind it. It was systematised by Michael
Huddleston (ICT) in the 2010s and its evidence base is practitioner
demonstration, not published testing — a search for controlled studies of order
blocks, fair value gaps or liquidity sweeps returns tooling and tutorials, not
results. That is worth stating plainly: **the method is implemented here because
it was asked for and because it is internally coherent, not because it has been
shown to work.** Everything in sections 1–4 applies to it unchanged, and the
friction ceiling applies to it hardest.

What made it worth implementing carefully is that the method's *own* rules are
far stricter than the version usually traded, and the strict version is mostly
about refusing trades. Implemented against the canonical definitions (cross
checked against the [`smart-money-concepts`](https://github.com/joshyattridge/smart-money-concepts)
reference library, which is the closest thing to a specification the method has):

| Primitive | Definition implemented | Where |
|---|---|---|
| Swing high/low | Extreme of `strength` bars either side | `_swings` (already existed) |
| BOS / CHoCH | Close beyond prior swing, with/against trend | `analyse` (already existed) |
| Order block | Last opposite-colour candle before the impulse | `_order_block` (already existed) |
| **Fair value gap** | 3-bar imbalance: prior high < next low (bull). Mitigated once price trades back through | `find_fvgs` |
| **Liquidity sweep** | Wick takes out a prior swing extreme, close returns inside. Only counts within 3 bars | `liquidity_sweep` |
| **Dealing range** | Recent 60-bar high/low | `dealing_range` |
| **Premium / discount** | Position in that range; <45% discount, >55% premium | `Structure.pricing` |
| **HTF fold** | 15 × 1m bars → one 15m bar, bucketed by wall clock | `resample` |

Two implementation notes that are correctness, not taste:

- **The higher-timeframe fold is clock-bucketed, not position-bucketed.** The
  engine passes a *rolling* window, so folding by list position re-groups on
  every single bar — 09:15–09:30 one minute, 09:16–09:31 the next — and the day
  trend read off it flickers for reasons the market had nothing to do with.
  Bucketing by timestamp makes the same minute always land in the same 15-minute
  bar, exactly as it does on the chart the rule was written for. Incomplete
  buckets are dropped, so a still-forming bar never moves the day trend.
- **Candlestick patterns may not nominate a trade, only confirm one.** Marshall,
  Young & Rose ([JBF 2006](https://www.sciencedirect.com/science/article/abs/pii/S0378426605002116))
  ran the first properly bootstrapped test of candlestick charting on DJIA
  stocks and found no value; they repeated the null on Japanese equities. Later
  work found some patterns profitable in
  [Taiwan and Thailand](https://journals.sagepub.com/doi/10.1177/2158244017736799) —
  which is the signature of a weak effect that appears and vanishes across
  samples. So [arb/candlesticks.py](../arb/candlesticks.py) is only consulted
  once price is already inside a zone structure identified independently. An
  engulfing bar at an order block says the zone was defended; the same bar in
  open space says nothing.

### The confluence rule

[arb/confluence.py](../arb/confluence.py). Three stages, in the order that can
refuse fastest:

1. **Day trend** from the 15-minute fold. No trend → no trade. Not a coin flip.
2. **Vetoes**, which end the evaluation rather than counting as a negative vote:
   lower timeframe fighting the day trend (`counter_trend`), or price in the
   wrong half of its dealing range (`premium_not_discount`).
3. **Confirmations**, counted and never weighted — a weight is a parameter to
   overfit, a count is not. Available: `structure`, `order_block`,
   `fair_value_gap`, `liquidity_sweep`, `htf_order_block`, `volume`, `vwap`,
   `candlestick`. `min_confirmations = 2`.

**On independence.** Confluence is worth something only when the confirmations
are not restatements of each other. Five momentum indicators agreeing is one
opinion stated five times, carrying the felt confidence of five. The checks here
are drawn from deliberately different evidence — swing geometry, gap structure,
wick behaviour, traded volume, bar shape — so two agreeing is genuinely two
facts.

**On the cost of confirmations.** Every filter both raises the bar and shrinks
the sample. This is the same trap section 4 describes from the other side: at
`min_confirmations = 3` the trade count may fall below the ≥30-per-fold
threshold that makes any claim about the system testable. The default is 2 for
that reason, and 3 should be chosen deliberately rather than because more sounds
safer. **Neither setting has been validated on this market.**

### VWAP

**Berkowitz, Logue & Noser (1988)** introduced VWAP as the benchmark
institutional execution is measured against, which is what makes it a level
large participants defend rather than a line on a chart. Implemented in
[arb/volume.py](../arb/volume.py) as a confirmation only: it answers whether
what we are about to buy is on the side of the day's business that is winning.
It is not used as a mean-reversion signal, because this system is trend
following by instruction and the two uses point opposite ways.

---

## Sequenced setups, and the forex models they came from

Four strategies written for FX/crypto (an Asia-session BOS model on ETH, an
Asia-sweep/London-BOS gold model, a NY multi-pool liquidity sweep, and a NY
fibonacci pullback) were folded into [arb/playbooks.py](../arb/playbooks.py).
The mechanisms were kept; the clocks and the 4H bias were not. What follows is
what the literature says about each mechanism, and what it does not.

### Order matters, and it is not the same claim as confluence

The pre-existing decision layer counted independent confirmations
([arb/confluence.py](../arb/confluence.py)). Counting is order-blind: three
things being true simultaneously scores three whether or not they form a
pattern. The playbooks add the missing axis by requiring events in sequence
(CHoCH *then* BOS; sweep *then* break). No paper is offered here in support of
that being profitable — it is a structural claim about what a setup *is*, and
it is testable from the journal once the sample exists, not before.

### Liquidity sweeps and stop clustering

**Osler, C. (2003). *Currency Orders and Exchange-Rate Dynamics.*** Journal of
Finance 58(5). Stop-loss orders cluster just beyond round numbers and
take-profit orders cluster *at* them, producing two opposite behaviours: price
accelerates through a level once the stops beyond it are taken, and stalls into
one while profit-takers fill. This is the evidence base for both halves of
[arb/levels.py](../arb/levels.py) — and the reason the round-number read is used
as a **veto on headroom** rather than as an entry signal. The same configuration
precedes a breakout about as often as a reversal, so trading it directionally is
a coin flip with extra steps.

Two caveats recorded rather than skipped. Osler is FX, not Indian index options;
and the round-number grid here is **derived** (nearest "nice number" to a
configured ATR multiple) rather than taken from a table, because a fixed
per-index level list would be an unvalidated guess frozen into code.

### Opening range

The opening range is used only as a **liquidity pool to be swept**, never as a
breakout signal. That restraint is deliberate: Mesfin (2026, §1 above) found
opening-range breakout — the most-recommended retail intraday pattern — makes
money gross and cannot clear its own friction (net +2.82 pts, T = 0.88). Using
the range as a level where stops rest is a different claim from using its break
as an entry, and only the former is implemented.

### What is unproven here

The fibonacci pocket (0.5–0.618) has no serious empirical support and is
included because it encodes a real and testable idea — *do not buy an extended
move, wait for a retracement* — not because the ratio is special. It is one
registered playbook among several and can be switched off in config
(`playbooks`). If it does not earn its place in the journal, it should go.

### Where to source the next round of this work

Sources worth using, in rough order of how directly they produce something
implementable:

- **SSRN**, Financial Economics Network — the main venue for this literature.
- **arXiv `q-fin`**, especially `q-fin.TR` (trading and market microstructure)
  and `q-fin.ST` (statistical finance).
- **RePEc** for working papers.
- **Hudson & Thames** — academic finance papers implemented as open-source
  Python; useful for López de Prado's work in particular.
- **Quantocracy** — aggregator that filters for statistical rigour.
- **QuantConnect forums** — papers plus community implementations.
- Searching a paper's exact title on **GitHub** frequently surfaces a
  dissertation-quality implementation.

Search taxonomy that actually returns results: *market microstructure*, *limit
order book dynamics*, *order flow imbalance*, *high-frequency mean reversion*,
*VPIN*, *adverse selection in high-frequency trading*. Authors worth following
directly: **López de Prado** (VPIN, structural breaks, ML on market data),
**Cartea & Jaimungal** (algorithmic trading, LOB modelling, micro-price),
**Rama Cont** (order flow imbalance, stochastic LOB models), **Avellaneda**
(statistical arbitrage, market making).

One honest note on fit. Most of that literature models the **limit order book**
— order flow imbalance, micro-price, adverse selection. This system currently
sees OHLCV candles and top-of-book quotes, not the full book, so those models
are not implementable here as written. The nearest reachable item is order flow
imbalance approximated from the quote stream the feed already carries; that is
a real piece of work, not a configuration change, and it should be scoped before
it is promised.

---

## Open items, in priority order

1. **Calibrate the session windows to NSE data** rather than inheriting US
   half-hour boundaries.
2. **Count how often each confirmation fires, and how each performs alone.**
   The journal now records `confirmations` by name on every position
   ([arb/intraday_engine.py](../arb/intraday_engine.py)). After a few hundred
   trades that answers the question this whole section is currently guessing at:
   which of the eight are carrying the result, and which are decoration.
3. **Decide on hold length.** 1-minute scalping is the falsified architecture;
   60–75 minute holds are the surviving one. This is a strategy decision.
4. **Set the stop from recorded MAE** once the journal has the sample
   (arXiv:1609.00869). The data is already being written.
5. **Partial exits at two lots** — the largest exit-design gain available, and
   currently unreachable at one lot.
6. **Hold out the evaluation.** Do not tune the four new parameters on the days
   that suggested them.
7. **Count how often each PLAYBOOK fires, and how each performs alone.** The
   journal now records `playbooks` and `setup_chain` on every position. The
   five registered setups are hypotheses, not findings; the honest expectation
   is that some of them are decoration and the sample will say which.
8. **Order flow imbalance from the quote stream** — the one item from the
   microstructure literature that this system's data can actually support.
