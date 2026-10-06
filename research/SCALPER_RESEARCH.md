# Star Sahil, other scalpers, and the base rates

**Date:** 6 Oct 2026.

**Scope.** What "Mr Star Sahil" teaches, whether his profitability can be checked, which of his rules a bot could backtest, what is known about profitable scalpers, and the base-rate evidence from regulators and academics.

**What this is not.** Research only. No repository code was changed and nothing was run against the engine.

**Update to PAPERS.md.** [PAPERS.md](PAPERS.md) §"The educators the owner follows" says *"No written, checkable rules could be found"* for Mr Star Sahil. That is no longer true. Section 2 below extracts his rules from his own videos, with timestamps.

---

## 0. Method, and how far to trust it

### Sources

- His YouTube channel [youtube.com/@MrStarSahil](https://www.youtube.com/@MrStarSahil) (channel id `UC06LfOG6RjSlbP5m1mbuc0Q`). Metadata comes from the public channel and video pages.
- The Hindi auto-generated (ASR) captions of 24 of his videos, downloaded with `yt-dlp --skip-download --write-auto-subs --sub-langs hi`.
  - Each was read in full and translated.
  - Timestamps below are the start of a 30-second caption block, so `[12:30]` means somewhere between 12:30 and 13:00.
- Two critic videos about him, a third-party roast channel.
- His public Telegram channel, Instagram bio and Facebook page.
- SEBI's own studies (primary PDFs where reachable) and peer-reviewed or working papers.

### Limits

- ASR captions garble numbers. Where a number is garbled, it is flagged.
- He often says "this candle" or "this level" while pointing at the chart. Anything defined only on screen is marked **visual only**. I did not watch the video frames.
- Nothing he says is quoted verbatim. "Quotes" are my translations of auto-captions and are marked *(tr.)*.
- One video, "SMC (ORDER BLOCK)" (`De1uwfURAns`, Jun 2024), is **members-only (paid)** and could not be read.
- The transcripts sit in the session scratchpad and will be wiped on reboot. To reproduce, re-run the `yt-dlp` command above on the video IDs listed in each section.

---

## 1. Who he is: facts that could be checked

| Item | Finding | Source |
|---|---|---|
| Name / handle | Sahil Kumar, "Mr Star Sahil", @MrStarSahil. Instagram @mrstarsahil: "Psychological Trader, 4 Year+ Trading Experience, I came here to rule". | Channel JSON-LD; [Instagram](https://www.instagram.com/mrstarsahil/) |
| Channel size | 601K subscribers, 460 videos, 110.7M views. Joined 14 Jul 2022. Country: India. | Channel About page (fetched 6 Oct 2026) |
| Biggest videos | "Advance Liquidity Masterclass" 394K views; "DAY-1 Zero to Operator" 373K; "My Trading Journey Age-18" 362K; "Free Real Price-Action Course" 306K. | Video pages |
| Age | About 18 in Jun 2025 (title of `HsQKYnl67lo`). Says he was in 9th class "five years ago" [0:30]. | [My Trading Journey, Age-18](https://www.youtube.com/watch?v=HsQKYnl67lo) |
| Own trading history (his account) | Bought Nifty dips with Rs 20k his father borrowed; 5 winning days took it to Rs 25k, then averaging down and theta wiped it out [2:00–3:00]. Quit after about Rs 1.7 lakh of losses "in these 3 years" [4:30]. Came back, lost half his capital on 24 Aug 2023 [5:00], blew up again on a "zero-hero" trade [5:30]. The family sold his mother's gold for about Rs 60k [6:00]. Says he then recovered more than the 3 years of losses in 6 months [6:00–6:30]. | same video |
| Proof offered for the turnaround | Only Instagram clips of selling and later re-buying the gold [6:00]. No broker statement, no tax filing, no verified P&L. | same video |
| Timeline problem | The channel started Jul 2022, and the "Zero to Operator" teaching series in Feb 2024. By his own account he was still losing until at least late Aug 2023. **Much of his teaching history overlaps his own unprofitable period.** | derived |
| SEBI registration | He never claims RA or RIA registration in any of the 24 videos. His disclaimer says "education purpose only, not buy/sell recommendations". No SEBI order against him turned up in searches (6 Oct 2026). This is "not found", not proof of absence. | Video descriptions; web search |
| Telegram | [t.me/snbdia](https://t.me/s/snbdia), about 50K subscribers, bio "Trader, Youtuber, Actor". Late Sep 2026 posts are **crypto trade calls**: "Bitcoin planning for swing trade", "My limit order", "Entry done", "Risk reward 1:3", "for scalper TP level 83,583", "80% TP done", "I hold my trade with trailing SL". A second channel, `star_mr_sahil`, is tiny (660). | Public Telegram preview |
| Paid products | The channel has a **paid YouTube membership** ("Join" button and a "Members only" shelf). The order-block video is members-only, dated Jun 2024. In Apr 2025 he says "I've never sold anything paid till today" (`PkBt6Sz5SrU` [16:30], tr.); a paid membership already existed then. A critic video says a "Brahmastra strategy" was members-only. | Channel page; yt-dlp error "members-only content" |
| Referral and sponsor income | Broker referrals: Dhan, Angel One, 5paisa. Crypto exchange referral: Delta Exchange India ("FIU-registered", "10% less brokerage"). Amazon affiliate links. In-video sponsors: Univest (a SEBI RA tips app), Lemon (broker), Sahi (broker), INDmoney (likely), Wint Wealth, ClearTax, PaisaBazaar bonds, StoxKart (likely). Free "9:20 morning setup" and "13 secret setups" videos are **gated behind opening a Dhan or Angel One account through his link** and filling a Google form. | Video descriptions; [Scalping Masterclass](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [21:00]; [Live Scalping](https://www.youtube.com/watch?v=E75wyasjo78) [6:30] |
| Critics | Two roast videos by "Two Faces" (155K and 58K views). [CHAPRI To Trader: Roasting Mr Sahil Star](https://www.youtube.com/watch?v=BoqvTI5Huj8) (Jan 2024) and [Trading Techstreet vs Mr Sahil Star](https://www.youtube.com/watch?v=cenSI-muRds) (Apr 2024). They allege: profit clips that look like paper trading; only winners posted as shorts; "after-market" explanations on days he took no live trade; a paywalled "Brahmastra" strategy. **This critic is biased too.** His own descriptions carry a prop-firm referral link (Goat Funded Trader), and the videos are roasts, not audits. Treat both sides as unverified. | Captions and descriptions of those videos |

### How his own claims compare

| Claim | Source | Status |
|---|---|---|
| "95%+ accuracy" on the 13 setups | `ayUJtEmPNtc` [32:00] | Unsupported |
| "90%, 95%" on the round-number day bias | `PkBt6Sz5SrU` [26:00] | Unsupported |
| "80+%" on the EMA filter | `bzI6eUgoiuQ` [15:00] | Unsupported |
| "9 out of 10" on the big-gap rule | `w2qmbVTidSg` [4:30] | Unsupported |
| SMC gives "1:5–1:10 easily" | `wII9Qycr2L4` [10:00] | Unsupported |

- **None of these comes with a trade list or a backtest file.**
- A 90%+ win rate at 1:2 or better R:R would be one of the best edges ever recorded. No audited trader in section 5 comes close.
- The only live P&L in speech is one Nov 2024 FinNifty-expiry session: about Rs 7,000–7,666 over 3 trades at 10–20 lots ([Live Scalping In Nifty](https://www.youtube.com/watch?v=E75wyasjo78) [13:00–14:00]). One day proves nothing.
- He says he is "in a proper income-tax slab" from "trading and YouTube" (`gKwzM3ZrXFs` [2:30]). That does not separate trading income from YouTube income.

### Regulatory context for his content

- **SEBI, Oct 2024 and 29 Jan 2025:**
  - SEBI-regulated entities (brokers, RAs) may not associate with unregistered persons who give advice or make return claims.
  - "Education" content may not use price data from the preceding 3 months ([Business Standard](https://www.business-standard.com/markets/news/sebi-finfluencer-circular-live-stock-data-market-education-rules-125013000571_1.html); [exchange4media](https://www.exchange4media.com/influence-zone-news/sebi-restricts-finfluencers-from-using-live-stock-market-data-educational-content-140505.html)).
- **How his content changed:**
  - He cites the 3-month rule himself (`O4HFo8ZwtgI` [12:30]).
  - His live trading moved to gold and crypto, on a second channel at 7:30–8:30 pm (`vt092qEJf-Y` [21:00]).
  - His Telegram calls are crypto. Crypto is outside SEBI's remit; Indian crypto exchanges are FIU-registered for anti-money-laundering only, not for investor protection.
- **Precedents (other finfluencers, for context only):**
  - "Baap of Chart", Nasiruddin Ansari: Rs 17.2 crore ordered refunded for courses sold on promised returns ([Business Today](https://www.businesstoday.in/visualstories/news/what-is-baap-of-chart-and-why-did-sebi-fine-it-72286-26-10-2023)).
  - Asmita Patel, "options queen": Rs 53.67 crore impounded in Feb 2025. Her "education" contained buy/sell calls with stop and target ([YourStory](https://yourstory.com/2025/02/sebi-bans-fininfluencer-asmita-patel-5-others-from-mkt-impound-illegal-gains-of-over-rs-53-cr)).
  - Avadhut Sathe (2025), and Ravindra Bharti (Rs 9.5 crore) ([Storyboard18](https://www.storyboard18.com/how-it-works/sebi-bans-finfluencer-avadhut-sathe-impounds-rs-546-crore-for-running-unregistered-advisory-85392.htm)).
  - **None of these concerns Star Sahil.** They show why screenshots and "live" profit are not evidence.

---

## 2. What he actually teaches

Timestamps are into each video. "SMC" marks his use of smart-money vocabulary.

### 2.1 Overall picture

- **Mostly classic price action, wrapped in an "operator traps retail" story.** His working assumption is "the market goes where the stop-losses are" ([Stop-Loss Hunting Levels](https://www.youtube.com/watch?v=7SrNG4O8B-E) [0:00]). From that he concludes: fade obvious breakouts, fade textbook patterns, and trade where retail stops sit.
- **His reference levels:**
  - previous-day high, low and close;
  - day high and low;
  - first-candle high and low;
  - round numbers ("PSY / operator levels": 500 and 1000 multiples, calibrated on BankNifty);
  - multi-touch trendlines and S/R zones;
  - Fibonacci 0.5–0.6 (he says "0.6", which is almost certainly 0.618).
- **SMC vocabulary:** liquidity, sweep or stop hunt, displacement, acceptance, equal highs and lows, BOS and CHoCH, internal vs external liquidity.
- **What is missing:**
  - He never defines an order block in free content. He says OB is "advanced supply/demand" ([SMC vs Price-Action](https://www.youtube.com/watch?v=wII9Qycr2L4) [18:00]), and the dedicated video is paywalled.
  - FVG appears only in a topic list.
  - No killzones or sessions in the ICT sense.
  - He calls SMC "70–80% price action renamed" [18:00].
- **Distinctive idea: Nifty vs BankNifty divergence.**
  - One index breaks a reference level while the other holds. It is used as an entry ([13 Secret Setups](https://www.youtube.com/watch?v=ayUJtEmPNtc), [Closing Candle](https://www.youtube.com/watch?v=w2qmbVTidSg)) and as a no-trade filter ([No Trading Zone](https://www.youtube.com/watch?v=ZjpRdCA1qwU)).
  - This is ICT's "SMT divergence", but he never uses that name.
- **Indicators:**
  - EMA 9/15 for scalping, 21/50 for swing, 200 for positional ([EMA Masterclass](https://www.youtube.com/watch?v=bzI6eUgoiuQ) [4:00]);
  - EMA 9/18 elsewhere ([SL + Target](https://www.youtube.com/watch?v=kCaFb_k2lEA) [8:30]);
  - 20 EMA as a trailing stop ([Scalping Masterclass](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [25:30]);
  - no VWAP, no RSI.
  - He told viewers to open a Lemon account "to get access to EMA" (`bzI6eUgoiuQ` [28:30], tr.). EMA is a free standard indicator.
- **Timeframes** ([Scalping Masterclass](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [1:30–5:00]):
  - 4H and 1H for structure and trend;
  - 15m for levels;
  - 5m or 1m for confirmation and entry. Use 1m only if experienced; 5m is safer.
  - Elsewhere: 1-minute for the "13 secret setups" (`ayUJtEmPNtc` [6:00]), and 15m for the round-number break (`PkBt6Sz5SrU` [18:00]).
- **Internal contradictions** that a backtest has to choose between:
  - **Sweep then reversal vs "invitation" then continuation.** The [Liquidity Masterclass](https://www.youtube.com/watch?v=fIEWofNZxQY) [15:00] fades a failed breakout. The [Stop-Loss Hunting](https://www.youtube.com/watch?v=7SrNG4O8B-E) video [6:00] says 30 minutes of chop at support means *expect* the breakdown.
  - **Entry timing.** "Enter on the touch, don't wait" (`wII9Qycr2L4` [7:30]) vs "wait for a candle to close back inside" (`fIEWofNZxQY` [15:00]).
  - **Max trades per day.** 1 (`zYFHO2PJb6A` [15:00]), 2 (`yf3KIIr6Yjo` [1:00], `8nbB2J7_OFw` [41:30]), 3 (`01NifNJ_fN8` [3:00]), 2–4 (`MUv6JQ4uLYk` [36:00]).
  - **Trading window.** 9:15–12:00 (`01NifNJ_fN8` [3:30]) vs 9:30–11:30 plus 14:00–15:10 (`MUv6JQ4uLYk` [9:00]).
  - **R:R.** "1:1 to 1:1.5 is fine for scalping" (`MUv6JQ4uLYk` [29:00]) vs "fixed 1:2, I won't exit before it" (`kCaFb_k2lEA` [19:30]).

### 2.2 Video-by-video rule extract (condensed)

**[SCALPING TRADING MASTERCLASS](https://www.youtube.com/watch?v=MUv6JQ4uLYk), 7 Apr 2026, 38 min, 54K views**

- **Timeframe stack:** see 2.1.
- **Combined example** [5:00]: 4H and 1H bullish, price at 15m support, then a 5m green candle closes at the level, so go long with the stop below it.
- **"Running entry"** [6:30], [19:30]:
  - Never enter a breakout candle directly.
  - Either wait for a pullback plus one confirmation candle,
  - or, if price is running, let one candle close in the move's direction and enter on the break of its high or low.
- **Instruments** [7:00]: Nifty, BankNifty (his most traded), gold, silver, crypto. Sensex only for experienced traders.
- **Windows (IST)** [9:00]:
  - Indian indices 9:30–11:30 and 14:00–15:10. Midday: "I don't touch it."
  - Gold 18:30–21:30.
  - Crypto 18:00–23:00 is his favourite.
- **Scalp zones = stop clusters** [11:30–20:30]:
  - breakouts and their failures;
  - W/M patterns and head-and-shoulders, taken *against* the textbook trade;
  - S/R and supply/demand;
  - multi-touch trendline breaks (stops cascade).
  - He says twice "this is not a setup, backtest it".
- **Sizing** [22:00–25:00]:
  - Fixed rupee risk, for example Rs 1,000. If the setup needs Rs 2,000 of risk, skip it.
  - If the structural stop is wider than planned, keep the stop and **cut quantity**.
- **Stops** [23:00–28:30]. His favourite is the first:
  1. 2–3 points beyond the last strong candle (hammer or marubozu);
  2. beyond the swing low;
  3. a close through the 20 EMA (not in sideways markets);
  4. beyond a small "fresh buying level" where buyers defended a price several times.
- **Management** [29:00–31:30]:
  - Example: 10-pt stop, 12–15 pt target.
  - At +1R move the stop to breakeven, then trail. "Mostly I don't place a system target."
- **Don'ts** [33:00–37:30]:
  - no scalping in sideways ranges;
  - always place a real stop order;
  - don't chase;
  - don't raise quantity while tightening the stop;
  - fix 2–4 trades a day in advance;
  - keep a journal.

**[DAY-5: Make Daily Profit With Closing Candle](https://www.youtube.com/watch?v=w2qmbVTidSg), 5 Feb 2025, 154K views**

- **Anchor** [0:00–2:00]: the previous day's close, which he says is where big players' overnight option-selling positions sit.
- **Big-gap bias** [2:00–3:30], [16:30]:
  - Trigger: the open gaps "more than normal" away from the previous close. Nifty **≥ 80 / 100 / 120 pts** (he gives all three); BankNifty **≥ about 220–300**.
  - He says the gap won't fill that day, so trade only in the gap direction all day.
  - "Wrong about 1 in 10" [4:30].
- **Setup 1** [12:00–13:30], gap up then a non-stop rally:
  - Draw a Fib from the previous close to the high.
  - Buy the 0.5–0.6 pullback on a good candle. Stop at that candle's low.
  - Target 1:2–1:3 with trailing.
- **Setup 2** [13:30–16:00], gap up, non-stop rally, then a resistance breakout:
  - Fade the FOMO breakout on the first selling candle. Stop at its high.
  - Target 1:1, or the 0.5–0.6 retracement of the rally.
- **Setup 3** [17:30–23:00], gap up then a fall:
  - If one index breaks its previous close and the other holds, **buy the one that broke**.
  - Reason given: the stops above (to the previous-day high, 42 pts away) are cheaper to reach than the previous close (144 pts away).
  - Example: about 97 pts.
- **Timeframe:** not stated.

**[Live Scalping In Nifty, Zero Hero Trade](https://www.youtube.com/watch?v=E75wyasjo78), 19 Nov 2024 (pre-SEBI 3-month rule)**

- **Trade 1** [0:30–6:00]:
  - Context: the first 15m candle was very bullish and broke a trendline.
  - Bought 23600 CE at about Rs 133, **10 lots (250 qty)**, on a 1-min premium chart.
  - Stop about 13 premium pts on a candle close below a level (**visual only**).
  - Took a partial (150 of 250) at a Rs 5,000 money target, moved the stop to breakeven, then trailed to the candle low.
  - Also said his "**9:20 setup**" had formed.
- **The "9:20 setup" is never defined in speech.** It is the item gated behind his Dhan referral.
- **Trade 2** [8:00–13:00]:
  - Around 14:00, after a sideways morning, a support breakdown and pullback, then a 15m close beyond the EMA. He went short.
  - 10 lots, then **doubled to 20 lots** for a 10–15 pt scalp.
- **Result:** about Rs 7,000–7,666 for the day.

**[13 SECRET SETUPS, OPERATOR MINDSET](https://www.youtube.com/watch?v=ayUJtEmPNtc), 9 Dec 2023, 135K views (part 1, about 7 of the 13)**

- **Timeframe:** 1-minute, chosen "because retail trades 1-min and gets trapped there" [6:00].
- Always keep Nifty and BankNifty side by side.
- **BBE, Both Breakout Entry** [1:30]: both indices break resistance, so **short** either one after a small red candle.
- **BBDE, Both Breakdown Entry** [3:30]: both break support, so **buy** after a small green candle.
  - Stop **20–30 index pts** (about 10–15 premium pts).
  - Target: Fib 50–60% of the prior swing.
- **ORE, One Retracement Entry** [6:00–11:30]: one index breaks resistance while the other turns down, which makes the breakout fake. Short the one that broke out. The mirror applies at support.
- **OBRTR** [10:30–15:00]: the same idea at a Fib 50–60% level. "Don't take the first touch."
- **CUTE / CDTE** [15:00–21:30]:
  - After a stretched day (Nifty 200+ pts vs a normal 100–150), wait for **5–7 (or 7–8) small indecision candles**, then enter on a strong candle in the trend direction.
  - No stop or target given.
- **"Brahmastra line"** [21:30–24:00]:
  - After every **75-pt Nifty move** (BankNifty 150), price retraces "8 of 10 times".
  - Counter-trade for 40–60 Nifty pts (70–150 BankNifty).
  - **No stop given; the start point of the 75 pts is unspecified.**
- **Back examples** [25:00–31:30]: first-candle-low divergences between the indices, 40 to 170 pts each.

**[Fake Breakout Masterclass + Strategy](https://www.youtube.com/watch?v=-LI_X41aCF4), 29 Aug 2026 (SMC framing)**

- **A real breakout needs** [1:30–3:00]: a level, liquidity beyond it, **displacement** (a full-body close past the level; wicks don't count) and **acceptance** (it holds rather than reversing).
- **Fake-breakout signs** [11:30–18:30]:
  1. stretched market (a long run with no pullback);
  2. a slow grind of small higher highs and hammers into resistance (the "retail invitation");
  3. a weak breakout candle (small or doji);
  4. rejection within 1–2 candles.
- **Retest checklist** [23:00–26:30]: activation candle; structure holds; displacement after time-pass; opposite liquidity already swept.
- **Breakout-failure setup** [30:30–32:30]:
  1. fast rejection;
  2. a displacement candle closes back through the level;
  3. wait for the retest;
  4. structure shift: a close beyond the displacement swing.
  - Then enter. Stop beyond the zone. Target 1:2–1:3.
  - Examples are on 15m; he says it works on 1m, 5m and 1H.

**[No Trading Zone](https://www.youtube.com/watch?v=ZjpRdCA1qwU), 24 May 2025**

1. **Opposite big candles** [1:00–3:30]:
   - A big candle, then within 0–2 bars an opposite big candle: no trading inside that range.
   - The zone ends only after **2 consecutive big candles one way**.
2. **Big-player fight** [8:00–12:30]: a strong trend runs into S/R that has been touched 2–3 times, then time-passes. Example: 1h50m with no trade.
3. **Inside-bar zone** [13:30–17:30]: a big candle followed by 2–3 inside candles. Patterns inside it "have no value" until the range breaks.
4. **Opposite index** [17:30–21:30]: Nifty breaks its first-candle low while BankNifty breaks its first-candle high. Wait **30–60 min** until they align.

**[Advance Liquidity Masterclass](https://www.youtube.com/watch?v=fIEWofNZxQY), 5 Jul 2025, 394K views (SMC)**

- **Definitions** [3:00–4:00], [2:30–4:30]:
  - "Read 'liquidity' as 'stop-loss'" [3:00, tr.].
  - Internal vs external liquidity means inside vs outside a "big structure" range.
- **Sweep trade** [15:00–16:00]:
  - Trigger: a failed breakout of a range S/R, then one candle on the same timeframe closes back on the original side.
  - Enter at that close. Stop at that candle's extreme.
  - Target is garbled ("1 3 4"); probably 1:3 to 1:4.
- **Filters** [20:30–23:00]:
  - never trade in the "90% retail direction";
  - never buy a breakout made by back-to-back candles without a pause;
  - never take the **first** breakout of a range.
- **Gated perks** [12:00]: a giveaway/community open only to those who open Dhan through his link and trade for a month.

**[Structure Mapping (SMC)](https://www.youtube.com/watch?v=gKwzM3ZrXFs), 26 Aug 2025**

- **Valid BOS** [4:30–5:30]: momentum, then a **40–60% pullback**, then the break.
- **Valid swing** [6:00–8:00]: a swing low only becomes valid after a 40–60% retrace from it. Internal swings are ignored until the next BOS.
- **Breaks count on candle close only** [12:30].
- **CHoCH** [15:30]: a close through the protected valid low (in an uptrend).
- **Trade** [20:30–21:30]: CHoCH, then a valid pullback, then entry on the next close-based BOS in the new direction. No stop or target given.
- **Sideways:** between the valid high and low, stay out or trade small.

**[Advance Stop-Loss Hunting Levels 2026](https://www.youtube.com/watch?v=7SrNG4O8B-E), 22 Dec 2025 (theory, no chart examples)**

- **Equal highs** act as a magnet or target, not a reversal point [0:30–3:00].
- **"Invitation" rule** [5:30–7:00]: 30 minutes of chop at support with hammers means stops have built up below, so plan a **short**.
- **PDH/PDL** [8:30]: "when the market takes out PDH/PDL it moves quite well". No direction rule.
- **Round numbers** [10:00]; stops sit on the far side of the **20/50/200 EMA** [11:00].
- **Fib 0.5–0.6** [13:00]: pullback buyers' stops sit below it.
- **Nearest-stops rule** [19:30–21:30]: price goes to the side that needs fewer points to reach more stops.
- **Range rule** [21:30–23:30]: in a 2–4 hour range, after **two false moves** (one to each side), trade the **third**.

**[Trade With Operator Levels](https://www.youtube.com/watch?v=PkBt6Sz5SrU), 14 Apr 2025, 154K views**

- **Operator level** = a price ending in 500 or 1000 [4:00]. Examples are BankNifty. He mentions 200/800 levels but never explains them.
- **Day bias** [7:30–11:00]:
  - Below the nearest round level, sells only; above it, buys only.
  - Flip for the rest of the day if price breaks the level and holds (the caption reads "4–5 candles").
  - Skip if price chops at a second level reached the same day.
- **15-minute close** beyond a round level with no reversal on the next candle means continuation [18:00–19:30].
- **Favourite setup** [30:30–33:30], [37:00]:
  - Break, pullback to the level, and a strong candle at the level (hammer, inverted hammer, marubozu, morning/evening star).
  - Enter. Stop at that candle's extreme. "1:3 or 1:4".
- **Rejection setup** [33:30–39:00]: touch the level without breaking, then enter on a close beyond the nearest opposing swing. Stop at the candle extreme.
- **Burnt level** [39:00]: if a round level is crossed **3 times in a day**, it is dead for that day.

**[The Only 4 Fibonacci Setups](https://www.youtube.com/watch?v=vt092qEJf-Y), 4 Jul 2026 (examples mostly BTC, one Nifty)**

- **Fast retracement** [2:00–10:30]:
  - The pullback is aggressive: consecutive big counter candles with no green between, and much less time than the impulse (example 1h15 vs 3h25).
  - It reaches 0.618 (a wick or close below is OK).
  - Long on the first strong green close back above 0.5. Stop at the swing low. Target 1:2–1:3.
- **Slow retracement** [11:30–17:30]: same zone, but the strong candle must come within about 2–3 candles.
- **Round number just below 0.618** (within about 5–10 pts) [21:30–28:00]: don't buy the Fib. Wait for the round number to be swept and reclaimed, then buy.
- **Round number overhead** [28:00–30:30]: buy the Fib; the target is the round number.

**Trade-management videos**

Sources: [Trail Your Stop-loss](https://www.youtube.com/watch?v=O4HFo8ZwtgI), [SL + Target](https://www.youtube.com/watch?v=kCaFb_k2lEA), [Risk Management](https://www.youtube.com/watch?v=Vwmy__gKZTM), [Trading Journal](https://www.youtube.com/watch?v=yf3KIIr6Yjo), [7 Habits](https://www.youtube.com/watch?v=01NifNJ_fN8), [Roadmap](https://www.youtube.com/watch?v=zYFHO2PJb6A). Collected in section 4.

---

## 3. Testable rules for a 1-minute index-options bot

**Overlap key.**
- **OVERLAP:X** = the bot already has concept X: OB/FVG retest, liquidity sweep, BOS/CHoCH, 4h trend filter, ATR pace gate, last-half-hour momentum.
- **REPO** = something close already exists per [PAPERS.md](PAPERS.md):
  - round-number levels as a headroom veto (`arb/levels.py`);
  - a Fib 0.5–0.618 pocket playbook;
  - opening range as a liquidity pool;
  - `ib_breakout`, `vwap_pullback`, and the noise-breakout test;
  - trailing to the last opposite swing (§0b);
  - cooldown after a loss; per-playbook daily cap.
- **NEW** = not present.

**Rule A to J are his.** None has a published backtest. They are hypotheses, and every unspecified parameter has to be swept, not guessed.

| # | Rule (IF … THEN) | Source | Overlap | Unspecified, so sweep it |
|---|---|---|---|---|
| **A** | **Gap-day direction lock.**<br>IF \|open − prev close\| ≥ G (Nifty G ∈ {80, 100, 120}; BankNifty ≥ 220–300; scale for Sensex), THEN allow only gap-direction entries all day.<br>Entry: pullback into the Fib 0.5–0.618 of (prev close → day extreme) plus a confirming 1/5-min close.<br>SL: that candle's extreme. TP: 2R, trail. | [Closing Candle](https://www.youtube.com/watch?v=w2qmbVTidSg) [2:00–4:30], [12:00–15:30] | NEW as a direction gate (sits beside the 4h trend filter); entry is REPO (Fib pocket) | G; use G as a multiple of 14-day ATR instead of points; latest entry time |
| **B** | **Index-divergence entry (SMT).**<br>IF one of Nifty/BankNifty breaks a shared reference (prev close, first-15m-candle low, day low) and the other holds its own, THEN go long the index that broke, on its first green 1-min close. Mirror for highs.<br>SL: 20–30 Nifty pts (or that candle's low). TP: about 40 pts or 50% of the prior swing. | [13 Setups](https://www.youtube.com/watch?v=ayUJtEmPNtc) [6:00–11:30], [25:00–30:00]; [Closing Candle](https://www.youtube.com/watch?v=w2qmbVTidSg) [17:30–23:00] | OVERLAP: liquidity sweep (it is a sweep confirmed by the other index); the cross-index confirmation is NEW | Break by close or by wick; how long the other index must hold; Sensex pairing |
| **C** | **Divergence no-trade filter.**<br>IF Nifty breaks its first-candle low while BankNifty breaks its first-candle high (or the reverse), THEN block new entries for 30–60 min or until both point the same way. | [No Trading Zone](https://www.youtube.com/watch?v=ZjpRdCA1qwU) [17:30–21:30] | NEW filter | First-candle timeframe (5m or 15m); block length |
| **D** | **Sweep and reclaim at his level set.**<br>IF price trades beyond PDH/PDL, prev close, a round number, or the first-candle high/low, and within ≤ k bars a candle closes back inside, THEN enter on that close.<br>SL: beyond the sweep extreme. TP: 2–3R.<br>Prefer cases where the approach was "stretched" (back-to-back same-colour candles) or the breakout candle was weak. | [Liquidity Masterclass](https://www.youtube.com/watch?v=fIEWofNZxQY) [13:00–16:00], [21:00–23:00]; [Fake Breakout](https://www.youtube.com/watch?v=-LI_X41aCF4) [11:30–18:30], [30:30] | OVERLAP: liquidity sweep. The level set is partly REPO (opening range, round-number grid); prev close and first-candle extremes may be NEW | k; overshoot limit; "stretched" (ATR multiple of the last N bars) |
| **E** | **Breakout acceptance = continuation.**<br>IF a full-body candle closes beyond the level (displacement) AND the next 1–2 candles do not close back inside (acceptance), THEN trade the break direction on the next pullback or candle-high break. Never on the breakout candle itself. | [Fake Breakout](https://www.youtube.com/watch?v=-LI_X41aCF4) [1:30–3:00], [28:00]; [Scalping](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [6:30], [19:30]; [Operator Levels](https://www.youtube.com/watch?v=PkBt6Sz5SrU) [18:00] | OVERLAP: BOS (close-based), ATR pace gate (displacement size) | Body/range ratio for "full body"; acceptance bars |
| **F** | **Round-level day bias, and burnt levels.**<br>Longs only above the nearest round level, shorts only below. Flip on a close beyond it held for about 4–5 bars.<br>A level crossed 3× in a day is ignored for the rest of the day. | [Operator Levels](https://www.youtube.com/watch?v=PkBt6Sz5SrU) [7:30–11:00], [39:00] | REPO has round numbers only as a veto; the bias and the 3-cross rule are NEW | Grid spacing: 500/1000 suits BankNifty; Nifty needs 100/500 or an ATR-derived grid as in `levels.py`; what counts as a "cross" |
| **G** | **Time windows.**<br>Variant 1: entries only 09:30–11:30 and 14:00–15:10.<br>Variant 2: entries only 09:15–12:00. | [Scalping](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [9:00]; [7 Habits](https://www.youtube.com/watch?v=01NifNJ_fN8) [3:30] | Interacts with last-half-hour momentum: his window ends at 15:10 and he skips the close. The repo's §0f found best times empirically; compare against it | None: both are fully specified |
| **H** | **Chop no-trade filters.**<br>(a) A big candle followed within ≤ 2 bars by an opposite big candle: block trades inside that range until 2 consecutive big same-direction candles.<br>(b) A big candle with ≥ 2 inside bars: block until its range breaks. | [No Trading Zone](https://www.youtube.com/watch?v=ZjpRdCA1qwU) [1:00–3:30], [13:30–17:30] | OVERLAP: ATR pace gate (both are "is the market moving cleanly" gates); the specific patterns are NEW | "Big" = range ≥ m × ATR(14) on 1m/5m |
| **I** | **Valid-swing qualifier for BOS/CHoCH.**<br>A swing high or low counts only after a 40–60% retracement of the leg. Breaks count on close only. Entry on the first close-based BOS after a CHoCH plus a valid pullback. | [Structure Mapping](https://www.youtube.com/watch?v=gKwzM3ZrXFs) [4:30–8:00], [12:30], [20:30] | OVERLAP: BOS/CHoCH; the 40–60% qualifier is NEW | Threshold 40 vs 50 vs 60%; SL/TP (none given; use the bot's) |
| **J** | **Fast-retracement Fib entry.**<br>IF the pullback into 0.5–0.618 took ≤ ⅓ of the impulse's time, with ≥ 3 consecutive counter candles, THEN go long on the first close back above 0.5.<br>SL: swing low. TP: 2R.<br>Skip if a round number sits within about 10 pts below 0.618 (wait for its sweep instead). | [4 Fibonacci Setups](https://www.youtube.com/watch?v=vt092qEJf-Y) [2:00–10:30], [21:30–28:00] | REPO (Fib pocket); OVERLAP: OB/FVG retest (a discount-zone retest); the speed and round-number conditions are NEW | Time ratio; candle count; swing definition |

### Rules not worth coding as given

- **"Brahmastra" 75/150-pt counter-trend** (`ayUJtEmPNtc` [21:30]). It has **no stop**, and the starting anchor is undefined. A counter-trend option buy with no stop is the classic retail blow-up.
- **"Invitation" chop means continuation** (`7SrNG4O8B-E` [6:00]). It contradicts rule D on the same configuration. Only test it as the paired opposite of D.
- **The "9:20 setup".** It is never defined; it is a referral lead magnet.
- **"Zero-hero" expiry lottery trades.** These are what blew up his own account (`HsQKYnl67lo` [5:30]). PAPERS.md already excludes them.

### Evidence-based intraday rules from the literature (for comparison: these have published backtests)

- **5-minute opening-range breakout.**
  - Rule: trade in the direction of the first 5-min candle. Stop at the opening-range extreme. Target 10R or end of day.
  - Results: QQQ 2016–23; stocks-in-play version Sharpe 2.81 net of commissions (Zarattini & Aziz, ["Can Day Trading Really Be Profitable?"](https://papers.ssrn.com/abstract=4416622), SSRN 4416622).
  - Caveat: PAPERS.md cites Mesfin (2026), where ORB does not clear friction. The repo uses the opening range only as a sweep pool.
- **Noise-area breakout with a VWAP / dynamic trailing stop.**
  - SPY 2007–2024: 19.6% a year net, Sharpe 1.33 (Zarattini, Barbon & Aziz, ["Beat the Market"](https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/), SFI RP 24-97).
  - The repo already ran `noise_breakout_test.py`.
- **First half-hour return predicts last half-hour return.**
  - S&P 500 ETF 1993–2013; stronger on volatile, high-volume and news days (Gao, Han, Li & Zhou, *J. Financial Economics* 129 (2018) 394–414, [doi](https://doi.org/10.1016/j.jfineco.2018.05.009)).
  - This is the academic basis of the bot's **last-half-hour momentum** (OVERLAP). Sahil's 15:10 cut-off would forfeit it.
- **Stop clustering beyond round numbers.**
  - Osler (2003, *J. Finance*), already in PAPERS.md. It is the only peer-reviewed support for Sahil's "operator levels".
  - It cuts both ways: price accelerates once the stops are hit, and stalls into take-profit orders. That is why the repo uses round numbers as a veto, not a signal.

---

## 4. Risk-management rules

### 4.1 Sahil's own stated rules (from transcripts)

| Rule | Value he states | Source |
|---|---|---|
| Per-trade risk | 2% for beginners; 2–5% for Rs 10–20k accounts. He rejects 1% as a "big-account" rule. 5% (Rs 500 on Rs 10k) in the 2024 course. **His own live % is never stated.** | [Risk Mgmt](https://www.youtube.com/watch?v=Vwmy__gKZTM) [1:30–2:30]; [Price-Action Course](https://www.youtube.com/watch?v=8nbB2J7_OFw) [41:30] |
| Position size | qty = rupee risk ÷ stop distance. He says "lots" but the formula gives units. If the needed risk exceeds the budget, skip the trade. If the structural stop is wider, cut quantity; never shrink the stop. | [Risk Mgmt](https://www.youtube.com/watch?v=Vwmy__gKZTM) [3:00–4:00]; [Scalping](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [22:00–25:00]; [SL+Target](https://www.youtube.com/watch?v=kCaFb_k2lEA) [18:00] |
| Stop placement | Structural, never "fixed points for Nifty": 1–2 pts beyond the swing low, or 2–3 pts beyond the last strong candle (scalp), or beyond an equal-lows cluster | [SL+Target](https://www.youtube.com/watch?v=kCaFb_k2lEA) [12:00–17:00] |
| Min R:R | 1:1.5–1:2 (beginner); 1:1–1:1.5 is acceptable for scalps | [Risk Mgmt](https://www.youtube.com/watch?v=Vwmy__gKZTM) [1:30]; [Scalping](https://www.youtube.com/watch?v=MUv6JQ4uLYk) [29:00] |
| Breakeven | Move to breakeven at **+1R** (= half of a 1:2 target), unconditionally. He says he backtested this "many times" (no data shown). | [Trail SL](https://www.youtube.com/watch?v=O4HFo8ZwtgI) [12:00–13:30]; [SL+Target](https://www.youtube.com/watch?v=kCaFb_k2lEA) [22:00] |
| Partials | At 2R book **80%**, put the stop on the remaining 20% at +1R, then trail in 10–20 pt steps. Beginners exit 100% at the target. | [SL+Target](https://www.youtube.com/watch?v=kCaFb_k2lEA) [24:00]; [Trail SL](https://www.youtube.com/watch?v=O4HFo8ZwtgI) [21:30] |
| Trailing | "Last defeat level": after ≥ 1 counter candle and a **closed** resume candle, move the stop to the pullback extreme ± 1–2 pts. Never trail on a single big impulse candle. **No re-entry after a trailed stop.** | [Trail SL](https://www.youtube.com/watch?v=O4HFo8ZwtgI) [6:00–11:30], [21:00–23:30]; REPO §0b is the same idea |
| Daily stop | Stop for the day when **any** of these hits: max trades N, max loss L, or max profit P = 2L. His own: N = 3, L = Rs 2,000, P = Rs 4,000. Elsewhere: N = 1 or 2, L = Rs 1,000, P = Rs 2,000. | [7 Habits](https://www.youtube.com/watch?v=01NifNJ_fN8) [3:00]; [Journal](https://www.youtube.com/watch?v=yf3KIIr6Yjo) [1:00–3:30]; [Roadmap](https://www.youtube.com/watch?v=zYFHO2PJb6A) [15:00] |
| Revenge / martingale | No recovery trade with the remaining balance after 3 losses; never raise size after losses. "Revenge trading wiped out my biggest account." | [Roadmap](https://www.youtube.com/watch?v=zYFHO2PJb6A) [5:00], [16:00] |
| Instruments | Trade 2–3 instruments only; don't hop | [7 Habits](https://www.youtube.com/watch?v=01NifNJ_fN8) [4:30] |
| Process | Backtest 3–6 months of charts; forward-test 15–20 days; then one setup only for at least 6 months. Journal: entry reason (setup / FOMO), emotion, exit reason, mistake. Review weekly. | [Roadmap](https://www.youtube.com/watch?v=zYFHO2PJb6A) [10:30–12:30]; [Journal](https://www.youtube.com/watch?v=yf3KIIr6Yjo) |

**Assessment.** These are conventional and sensible, and most already exist in the engine as cooldown, daily caps, structural stops and trailing to the last opposite swing.

**The genuinely testable new items:**
- the daily **profit** cap (P = 2L);
- the **80% partial at 2R**;
- **breakeven at exactly 1R**.

Breakeven-at-1R is not free. On a 1-minute option chart, noise often tags breakeven before the move continues. PAPERS.md §3 already says exits are where most of the measurable value is, so test it rather than adopt it.

**The 5% per-trade risk he teaches small accounts is reckless by any professional standard.** At a 40% win rate, 1:2, the chance of a long losing streak is high. The engine's own per-trade cap is a separate decision already made (8% indices / 7% stocks per the 6 Oct commit).

### 4.2 Rules the evidence supports (regulators, academics, professionals)

- **Trade less.**
  - SEBI FY26: the loss-maker share rises with options turnover, from about 86% (Rs 10k–1L) to about 95% (> Rs 10 crore). Large-turnover traders were 20% of traders but **82% of losses**; average loss Rs 4.87 lakh vs Rs 40k for medium turnover (§6.31, §6.33).
  - SEBI intraday cash FY23: 70% of intraday traders lost, rising to **80% for those making > 500 trades a year**.
  - Barber, Lee, Liu & Odean, and Chague et al.: no evidence that traders learn by trading more.
  - Bot implication: a hard daily trade cap and a profit cap are justified by data, not just psychology.
- **Costs decide marginal strategies.**
  - FY26: individuals paid about Rs 25,000 crore in transaction costs; STT share rose from 13% to 27% over FY22–26.
  - In FY25 and FY26, 5.3 and 4.4 lakh traders respectively had gross profits wiped out by costs (§6.36).
  - In the intraday cash study, loss-makers paid a further 57% of their trading losses in costs.
  - Bot implication: any rule above must be backtested with exact costs. The repo already does this ([costs_check.py](costs_check.py)).
- **The option buyer's handicap.** SEBI FY26: about 97% of individual traders were predominantly option buyers; **option sellers were the only strategy group with a positive median return** (press summary of the Aug 2026 study). The bot is an option buyer by design; this is the structural headwind it must overcome.
- **Expiry and 0DTE concentration.** 59% of FY26 index-option turnover was on 0DTE contracts (70% in FY25). Sahil's own blow-up was an expiry "zero-hero" trade. The bot should treat expiry-day afternoon entries as their own regime in the journal.
- **Professional scalpers.** No audited professional discloses a fixed rule set, so nothing is quoted here. The well-documented, consistent points:
  - daily loss stops;
  - cutting size after losses;
  - never adding to losers;
  - trading only the liquid core session.
  - All are already in the engine's `IntradayLimits` per PAPERS.md.

---

## 5. Profitable scalpers and day traders: what is actually verified

| Person | Style | What is verified | Verification level |
|---|---|---|---|
| **Larry Williams** | Short-term futures | 1987 Robbins World Cup: 11,376% on a real-money account ([hall of fame](https://futures.ireallytrade.com/halloffame)) | Competition, real broker accounts. One year, one account. He now sells courses. |
| **Andrea Unger** | Intraday systematic futures | Robbins World Cup **winner 2008 (672%), 2009 (115%), 2010 (240%)**, plus a 4th title. The only 4-time winner ([hall of fame](https://futures.ireallytrade.com/halloffame)). | Competition, real money. The account may not be representative of all his accounts (site disclaimer). Sells education. |
| **Michael Cook** | Futures | Robbins World Cup 2007 (250%) and 2014 (366%) | Competition |
| **Marty Schwartz** | S&P futures day trader (holds < 1 day) | US Investing Championship: won in 1984; average non-annualised 210% across 9 four-month contests, 1983–87 ([TurtleTrader](https://www.turtletrader.com/trader-schwartz)). Interviewed in *Market Wizards*. | Competition (1980s). Book *Pit Bull*. |
| **Linda Raschke** | Short-term futures | Registered CTA (LBR Group) 1991–2015; fund ranked 17th of 4,500 by BarclayHedge for 5-year performance ([bio](https://lindaraschke.net/bio-linda/)) | NFA-registered CTA with disclosed performance. The strongest form of retail-visible verification. |
| **Paul Rotter, "The Flipper"** | Eurex Bund scalper, order-book "flipping" | Widely reported €50–60M a year in the mid-2000s, **never independently verified**. His style was controversial as possible spoofing. | Press reports only |
| **Takashi Kotegawa, "BNF"** | Japanese equity day trader | Reported to have turned about $13k into about $150M; famous J-Com fat-finger trade (2005) ([summary](https://behindtheinvestment.substack.com/p/bnf-and-his-trading-journey)) | Japanese press. Partly corroborated by public large-share-holding filings; not audited P&L. |
| **Indian scalpers** | — | **No Indian intraday options scalper with an audited or competition-verified track record turned up.** | The closest public tool is [Sensibull Verified P&L](https://blog.sensibull.com/2022/11/09/verified-profit-and-loss-by-sensibull/): free, broker-backend snapshots via Zerodha, Upstox, Angel One and others. It verifies a moment, not a track record. I found no Verified P&L from Star Sahil. |

**Takeaways:**
1. Real verification (competition accounts, CTA filings) exists, and the people who have it show it.
2. Even the verified winners trade futures with stop discipline, not option buying on 1-minute charts.
3. Competition winners are a survivorship sample. Thousands of entrants did worse.
4. In India the profitable side of intraday derivatives is identifiable from SEBI data: **algorithmic proprietary traders and FPIs**. In FY26, 99% of prop and FPI profits came from algo entities, and the top 10 prop entities took about 75% of prop gross profit. That edge comes from speed, rebates and market making, not chart patterns.

---

## 6. Base rates: SEBI and peer-reviewed evidence

### SEBI, equity F&O (individuals)

| Study | Period | Loss-makers | Aggregate / average | Notes | Source |
|---|---|---|---|---|---|
| Jan 2023 | FY22 | **89%** (90% of active traders) | Average loss Rs 1.1 lakh (Rs 1.25 lakh for active traders) | The 11% who profited averaged Rs 1.5 lakh. Top-10 brokers. | [Business Today](https://www.businesstoday.in/amp/markets/stocks/story/loss-makers-of-dalal-street-9-out-of-10-individual-fo-traders-lost-money-in-fy22-says-sebi-367628-2023-01-25) |
| Sep 2024 | FY22–FY24 | **93%** of over 1 crore traders | Rs 1.8 lakh crore over 3 years; about Rs 2 lakh each | Only 7.2% profited; **only 1% made > Rs 1 lakh** after costs. Top 3.5% of loss-makers lost Rs 28 lakh each. Over 75% kept trading after consecutive loss years. 97% of FPI and 96% of prop profits came from algo trading. | [SEBI PR 37/2024](https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2024/updated-sebi-study-reveals-93-of-individual-traders-incurred-losses-in-equity-fando-between-fy22-and-fy24-aggregate-losses-exceed-1-8-lakh-crores-over-three-years_86906.html); [Moneylife](https://www.moneylife.in/article/93-percentage-of-individual-traders-lost-rs18-lakh-crore-in-equity-fo-in-past-3-years-sebi/75210.html) |
| Jul 2025 | FY25 | **91%** | Rs 1.06 lakh crore net (later revised to Rs 1.12 lakh crore); about Rs 1.1 lakh average | Losses up 41% year on year despite the Nov 2024 reforms | [Business Today](https://www.businesstoday.in/amp/markets/story/despite-reforms-91-of-retail-traders-still-lose-money-in-indias-booming-derivatives-market-sebi-study-483461-2025-07-07); [Business Standard](https://www.business-standard.com/amp/markets/news/net-losses-of-traders-in-fo-widens-in-fy25-sebi-study-125070701221_1.html) |
| **Aug 2026** | FY25–FY26 | FY25 90.9%; **FY26 87.7%**. Options 87.7% vs futures 66.0%. | FY26 Rs 91,685 crore; **Rs 1.17 lakh each** | See the bullets below. | [SEBI study PDF](https://www.sebi.gov.in/sebi_data/attachdocs/aug-2026/1787233506209.pdf); [SEBI page](https://www.sebi.gov.in/reports-and-statistics/research/aug-2026/study-profitability-of-individual-traders-in-the-equity-derivatives-segment-fy25-fy26-_103835.html) |

**More from the Aug 2026 study (FY26):**
- Average loss among loss-makers Rs 1.47 lakh vs average profit Rs 1.22 lakh among profit-makers.
- Options were 92% of losses.
- Under-30s were 43% of traders, and **89% of them lost**.
- About 90% of those who lost two years in a row lost again.
- 59% of index-option turnover was 0DTE.
- New and regular traders lost at the same rate (87.8% vs 87.7%). **Experience did not reduce the chance of losing.**
- Individuals flagged as algo users still lost (PAPERS.md quotes Rs 51,728 crore gross).

### SEBI, equity cash intraday (Jul 2024 study, FY23)

- **About 70% (7 in 10) of individual intraday traders lost money.** The share rose to **80% for traders with > 500 trades a year**.
- Participation was up more than 300% since FY19.
- Under-30s rose from 18% to 48% of intraday traders.
- Loss-makers paid a further **57%** of their losses as costs; profit-makers paid 19% of their profits.
- Sample: top-10 brokers, about 86% of clients.
- Sources: [Motilal Oswal summary](https://www.motilaloswal.com/learning-centre/2024/7/sebi-study-reveals-key-insights-on-individual-intraday-traders-in-india), [Taxmann](https://taxmann.com/post/blog/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-made-losses).

### Peer-reviewed and working papers

- **Barber, Lee, Liu & Odean, "The cross-section of speculator skill: Evidence from day trading"** (*J. Financial Markets* 18, 2014; Taiwan 1992–2006; [PDF](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)).
  - **Less than 1% of day traders predictably and reliably earn positive abnormal returns net of fees.**
  - The top 500 by past performance earned 37.9 bp a day after fees; the bottom ones lost 28.9 bp a day.
  - A companion paper, "Learning, Fast or Slow" (*Rev. Asset Pricing Studies* 10(1), 2020, [IDEAS](https://ideas.repec.org/a/oup/rasset/v10y2020i1p61-93..html)), finds day traders keep trading despite losses, and learn slowly or not at all.
- **Chague, De-Losso & Giovannetti, "Day Trading for a Living?"** ([SSRN 3423101](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101); Brazil mini-Ibovespa futures, 19,646 people who started 2013–15).
  - **97% of those who persisted more than 300 days lost money.**
  - Only **1.1%** earned more than the minimum wage, and 0.5% more than a bank teller's starting salary.
  - No evidence of learning.
  - The authors explicitly contrast this with what course sellers claim.
- **ESMA (2018) CFD intervention:** national regulators found **74–89% of retail CFD accounts lose money**, with average losses of €1,600–29,000 per client ([ESMA](https://www.esma.europa.eu/node/84933)).

**What the base rates mean for judging an educator.** For an under-30 Indian option buyer, the prior probability of being profitable over a year is about 11%. Over a multi-year horizon it is about 1–7%. A self-reported turnaround with no broker-verified record does not move that prior.

---

## 7. Assessment

**Is Star Sahil verifiably profitable? No, it cannot be verified.**

- There is no audited statement, no competition result, no broker-backed (e.g. Sensibull) track record, and no SEBI registration.
- His own biography describes about 3+ years of losses and at least two blow-ups, with the channel running through most of that period.
- His income visibly comes from things that pay whether he trades well or not:
  - a 601K-subscriber channel;
  - about 8 sponsors;
  - broker and crypto-exchange referrals that gate "secret setups";
  - a paid channel membership;
  - affiliate links.
- His accuracy claims (80–95%) have no evidence behind them and conflict with the SEBI base rates and with what verified traders report.
- **None of this proves he is unprofitable or dishonest.** The two critic videos are themselves biased and unverified, and no regulator has acted against him. **It is unverifiable, and the incentives do not depend on his trading results.**

**Is his material useless? Also no.**

- Several ideas are concrete enough to test and fit the bot's design. Most promising, with no edge assumed:
  - the Nifty/BankNifty divergence entry and filter (B, C);
  - the gap-day direction lock (A);
  - previous close and first-candle extremes as sweep levels (D);
  - the chop no-trade filters (H).
- His risk advice is mostly standard, apart from the 5% per-trade figure, which is too high.
- Several setups contradict each other. A backtest has to pick one side of each pair (sweep reversal vs "invitation" continuation) and let the data decide.

**Suggested order if the owner wants these tested** (not run here):
1. **C and H** (filters). They are cheap, only remove trades, and are the least likely to overfit.
2. **A** (gap lock) as a direction gate.
3. **B** (divergence entry).
4. **D** with the extra level set.
5. **Breakeven at 1R and the 80% partial at 2R**, against the current trailing logic.

Run each with the repo's usual method: exact costs, the option-pricing model, a held-out period, and the trades and win-rate tables the owner asks for. Treat any parameter Sahil left unspecified as a sweep, and count how many variants were tried before trusting the best one.

---

### Video index (all on youtube.com/watch?v=…)

| ID | Title | Date | Views |
|---|---|---|---|
| MUv6JQ4uLYk | SCALPING TRADING "MASTERCLASS" FREE | 2026-04-07 | 54K |
| w2qmbVTidSg | DAY-5, Make Daily Profit With Closing Candle | 2025-02-05 | 154K |
| E75wyasjo78 | Live Scalping In Nifty, Zero Hero Trade | 2024-11-19 | 52K |
| ayUJtEmPNtc | 13 SECRET SETUP'S OF TRADING, OPERATOR MINDSET | 2023-12-09 | 135K |
| -LI_X41aCF4 | Fake Breakout Masterclass + Strategy | 2026-08-29 | 35K |
| ZjpRdCA1qwU | No Trading Zone (Save Your Money) | 2025-05-24 | 57K |
| fIEWofNZxQY | Advance Liquidity Masterclass | 2025-07-05 | 394K |
| gKwzM3ZrXFs | Structure Mapping "Untold Secret" (SMC) | 2025-08-26 | 79K |
| wII9Qycr2L4 | SMC Vs Price-Action: Which Wins? | 2026-06-23 | 23K |
| 7SrNG4O8B-E | "Advance" Stop-Loss Hunting Levels 2026 | 2025-12-22 | 56K |
| PkBt6Sz5SrU | Trade With Operator Levels | 2025-04-14 | 154K |
| bzI6eUgoiuQ | "EMA" Indicator "Masterclass" With Setups | 2026-03-17 | 46K |
| vt092qEJf-Y | The Only 4 "Fibonacci Setup" I Use | 2026-07-04 | 29K |
| 8nbB2J7_OFw | Free Real Price-Action Course In Hindi | 2024-05-19 | 306K |
| CTyQiOPeCbY | DAY-1, ZERO TO OPERATOR | 2024-02-11 | 373K |
| HsQKYnl67lo | My Trading Journey, Age-18 | 2025-06-14 | 362K |
| x-iKqixC_e4 | My Other 3 INCOME SOURCES | 2025-06-02 | 47K |
| Vwmy__gKZTM | "Master Risk Management: Trade Smart" | 2026-02-08 | 33K |
| O4HFo8ZwtgI | "The Right Way To Trail Your Stop-loss" | 2025-12-15 | 21K |
| kCaFb_k2lEA | Master To Set Perfect (Stop-Loss + Target) | 2025-07-09 | 128K |
| JKCph_JlWsc | STOPLOSS FINDOUT BY FIBONACHI | 2024-01-27 | 108K |
| yf3KIIr6Yjo | "Trading Journal = Consistent Profit Secret" | 2025-10-15 | 105K |
| 01NifNJ_fN8 | 7 Powerful Trading Habits That Made Me Profitable In 6 Months | 2026-02-22 | 28K |
| zYFHO2PJb6A | This "Roadmap" Changed My Trading Forever | 2026-01-02 | 52K |
| De1uwfURAns | SMC (ORDER BLOCK) (**members-only, not read**) | 2024-06-16 | — |
