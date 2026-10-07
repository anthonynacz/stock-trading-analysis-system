# SUMMARY
Momentum Radar signal model from the state-machine robustness and false-positive lens. The main finding came from a replay I built: 491 US stocks, 39 sessions from 2026-08-03 to 2026-09-25, Yahoo 5-minute bars. A list that only reacts to sharp 15-30 minute moves is worse than a coin flip. Continuation 30 minutes later was 46.7%, with about 92 entries and 13.5 flaps a day; sharp moves in quiet names tend to reverse.
Two rules turned that around. The first is an "in play" gate: the stock's own day move (after removing the market) must be at least 2.5 of its daily standard deviations, and cumulative relative volume at least 1.5. The second is a 2-tick confirmation. With both, a list of about 5.7 entries a day continued 58.3% of the time at 30 minutes (95% CI about 52-65%) and 61% at 15 minutes, and 91.5% held at least half of the move that got them listed.
Hysteresis cuts the flap rate from 9.5% to 3.6% (0.2 flaps a day). It combines a minimum dwell of 3 bars, 2 consecutive soft fails, hard exits that ignore dwell, cooldowns of 6 and 3 bars, a new-peak rule and at most 3 episodes a day. Median time on the list is 40 minutes (IQR 25-55).
Other design points:
- The engine runs on bar time, not wall time. Features are adjusted for SPY and for time of day. The opening bar is only an anchor. Entries run 09:50-15:00 ET and every member exits at 15:55.
- Extended hours are not used: Yahoo pre and post-market 5-minute bars had zero volume in 100% of cases.
- Halts freeze a member's state. Caps are 12 members and 4 new per tick. A market mode (SPY 30-minute z of 3 or more, or breadth of 0.60 or more) raises thresholds and caps entries in the market's direction.
- The 0-100 score measures how hard a stock is moving, not the odds it keeps going (Spearman -0.09 with the next 30 minutes).
- A cheap two-stage scan fetches 5-minute bars for only about 6% of the universe each tick.

# RISKS
- Validated only on a calm 39-session sample (2026-08-03 to 2026-09-25; largest SPY day +1.78%). Precision differed by 9 points between the two splits (53% vs 62%), and with n=223 the 95% interval is about 52-65%. The absolute 58% may not hold in other regimes; the gain over the naive thrust list (47%) is more robust.
- Untested parts of the design: breadth-based MARKET mode and the per-direction cap, halt/LULD handling (the one real case, LCID on 2026-07-14, fell before the replay window), the sector cap, and the Stage-A quote prefilter's volume field (a quote-endpoint day-volume field was assumed and has not been checked against the 5-minute bar volumes).
- Yahoo live 5-minute bar volume may differ from the historical (revised) volume the replay used. A live shadow log comparing the two is needed before trusting relative-volume thresholds live.
- A full yfinance fetch for 491 symbols took about 60 s per tick and risks Yahoo rate limits (roughly 100 requests a minute). The two-stage scan reduces this but adds a dependency on a batch quote endpoint whose availability and crumb handling were not tested here.
- The in-play gate makes the list late and mostly empty (median concurrency 0 on 491 names). If the product wants a busier list, loosening the gate quickly brings back mean-reversion false positives (below 50% continuation).
- The composite score does not predict continuation (Spearman -0.09). If the UI or email presents it as confidence, users will be misled.
- Market crash or melt-up days (correlations near 1, unstable betas) were not in the sample. Relative z-scores and the MARKET-mode guard could still flood or starve the list.
- Replay scratch caches in scratchpad/radar_design/fp_lens/data are about 922 MB and can be deleted after design review. The scripts (features.py, replay.py, grid.py, ablate.py, final_rec.py) are the reference implementation to port for live/replay parity.

# Momentum Radar: signal model and state machine (robustness and false-positive lens)

Educational analysis only. The radar describes what is moving now. It does not tell anyone to trade, and it never places trades.

---

## 0. Evidence first (replay I ran for this design)

**Setup**
- Replay code: `.../scratchpad/radar_design/fp_lens/` (`features.py`, `replay.py`, `grid.py`, `ablate.py`, `final_rec.py`, `condstudy.py`, `probe.py`).
- Data: Yahoo 5-minute bars (yfinance 1.2.0, 60-day window) plus 1 year of daily bars.
- Universe: 491 US names (S&P 500 plus high-beta mid caps), with SPY, QQQ and IWM as references.
- Sessions: 20 sessions to seed the baselines, then 39 replay sessions from 2026-08-03 to 2026-09-25, split into 19 for calibration and 20 for validation.
- No look-ahead: all baselines use prior sessions only.

**How to read the metrics**
- prec@h: the share of entries whose SPY-relative return, in the entry direction, was above 0 h minutes after the entry bar closed.
- hold30: the stock gave back less than 50% of the thrust that got it listed within 30 minutes.
- flap: an episode that lasted 2 bars or less, or a re-entry of the same symbol within 30 minutes of its exit.

| Variant (same exits unless noted) | entries/day | prec@15 | prec@30 | prec@60 | hold30 | flap rate | flaps/day | dwell median (bars) | concurrent p95 / max |
|---|---|---|---|---|---|---|---|---|---|
| Naive thrust list (thrust gate only, 1 tick, no hysteresis) | 91.8 | 48.3% | 46.7% | 47.7% | 87.1% | 14.7% | 13.5 | 5 | 12 / 12 (cap reached) |
| REC without the in-play gate | 35.9 | 48.7% | 47.6% | 49.1% | 89.5% | 3.6% | 1.28 | 7 | 10 / 12 |
| REC without 2-tick confirmation | 9.0 | 54.6% | 52.9% | 54.0% | 86.9% | 5.7% | 0.51 | 7.5 | 4 / 12 |
| REC without hysteresis (dwell 1, soft fails 1, no cooldown or new-peak rule) | 5.9 | 61.2% | 58.2% | 54.7% | 90.9% | 9.5% | 0.56 | 6 | 2 / 9 |
| **REC (this spec)** | **5.7** | **61.0%** | **58.3%** | **54.7%** | **91.5%** | **3.6%** | **0.21** | **8** | **3 / 9** |

- **Split:** calibration prec@30 was 53.1% (n=96); validation was 62.2% (n=127).
- **Uncertainty:** the 95% Wilson interval for REC prec@30 is about 52-65% (n=223, 194 distinct symbol-days).
- **Latency:** entering one bar (5 minutes) later gives prec@15 59.2% and prec@30 55.6%.
- **Returns:** mean SPY-relative 30-minute return after entry is +19 bp, before costs.
- **Base rates (178-symbol pass):**
  - Following the sign of the 15-minute move on every eligible symbol-tick continued 48.9% of the time at 30 minutes.
  - After a 15-minute move of 3 sigma or more, continuation was only 45.9% (n=8,426). Big quick moves reverse slightly more often than they continue.
  - Continuation rises steadily with the day-level move in the same direction. By the day's z-score (defined in section 2):
    - 0 or below: 46.3%
    - 0-1: 46.8%
    - 1-2: 48.2%
    - 2-3: 49.3%
    - 3-5: 51.0%
    - above 5: 52.4% (prec@60 60.2%)
- **Grid (48 cells, worst of the two splits for prec@30):**

  | in-play zday | worst-split prec@30, range across cells | entries/day |
  |---|---|---|
  | 1.5 | 42-50% | 7-20 |
  | 2.0 | 48-54% | 5-13 |
  | 2.5 | 50-56% (validation 56-64%) | 4-9 |
  | 3.0 | 49-58% | 3-6 |

  - A thrust threshold of z3 = 2.0-2.5 did better than 3.0; very large thrusts show exhaustion.
  - Volume thresholds (rvol3 1.5 vs 2.0, rvolc 1.5 vs 2.0) made little difference.
- **What the evidence says:** most of the false-positive control comes from the in-play gate. The confirmation tick is the second-largest lever. Hysteresis does not change precision; it removes flapping (about 2.6x fewer flaps).
- **Limits:**
  - The sample was calm: the largest SPY day was +1.78% and no crash day occurred.
  - The halt logic, the breadth-based market mode and the sector cap below are rule-based designs that the replay has not tested.

**Data facts measured (Yahoo 5-minute bars, 178 symbols x 60 sessions)**

- **Extended hours:**
  - Volume is 0 on 100% of pre-market and after-hours bars.
  - Slot coverage: pre-market median 80% (p10 30%); after-hours median 92% (p10 38%).
- **Regular hours:**
  - 86 of 10,654 symbol-days had missing slots.
  - Most were thin or odd names. PARA had up to 51 slots missing a day, flat zero-volume bars and 5-minute moves of ±37% (corporate-action noise).
  - Mid-liquidity names such as BIIB, BNTX and REGN often missed 1-5 isolated slots. These are no-trade slots, not halts.
  - LCID on 2026-07-14 missed the 13:30 slot and the 14:05-14:35 slots, next to 5-minute bars of +29% and -19%. That is the LULD-halt pattern.
- **Time-of-day |5m return| profile** (pooled universe; multiple of the median slot, clamped to [0.6, 4.0]; values as of 2026-09-25):

  | Slot | Multiple |
  |---|---|
  | 09:30-09:35 | 3.6 |
  | 09:35 | 3.6 |
  | 09:40 | 2.9 |
  | 09:45 | 2.7 |
  | 09:55 | 1.8 |
  | 10:00 | 2.3 |
  | 10:30 | 1.4 |
  | 11:00 | 1.1 |
  | 12:00-15:00 | 0.75-0.9 |
  | 15:30 | 1.1 |
  | 15:45 | 1.1 |
  | 15:50 | 1.4 |
  | 15:55 | 1.7 |

- **Time-of-day volume:**
  - The 09:30 bar is about 5.9x the median slot, and 2.8x the 09:35 bar.
  - The 15:55 bar is about 10x the median slot, and 2.8x the 15:50 bar.
- **Cost:**
  - Computing features for 491 symbols takes 3.2 ms per tick (numpy, vectorised).
  - `yf.download(491 symbols, period=1d, interval=5m)` took 60 s. That is why section 1.4 uses a two-stage scan.

---

## 1. Clock, inputs and baselines

### 1.1 Bar clock (determinism)
- **Session grid.** Regular session only, America/New_York time. Slot j is the bar starting at 09:30 + 5·j minutes, for j = 0..77. Half-days (13:00 close) have slots 0..41. Use a hard-coded NYSE holiday and half-day list.
- **Bar finality.** Bar j is final when `now_ET >= start_j + 5 min + 60 s`. Always drop Yahoo's last row if it is not final: Yahoo returns the in-progress bar.
- **The engine runs on bars, not wall-clock ticks.** Each run processes every final bar not yet processed, one at a time and in order. Each processed bar is one tick for every counter (confirmation, dwell, soft fails, cooldown, stall). So runner jitter or skipped cron runs never change outcomes, and live and replay share one code path. Port `replay.run()`'s per-bar step.
  - Idempotency: persist `last_processed_slot` and the session date.
  - Catch-up: an entry created while catching up more than 2 bars behind real time is flagged `late: true` for the UI.
- **Session reset.** All per-symbol state resets at the first bar of each session. Nothing carries overnight.

### 1.2 Inputs per symbol per tick
| Input | Source | Notes |
|---|---|---|
| Today's regular-hours 5-minute OHLCV, slot 0 to the last final slot k | Yahoo 5m (`prepost=False`) | Missing slot: O=H=L=C=previous C, V=0, `present=false` |
| SPY 5-minute bars, same slots | Yahoo 5m | Market reference; β is measured against SPY |
| Prior close PC (split-adjusted) | Yahoo daily | For SPY too |
| Baseline pack (1.3) | Built once a day from the prior 20 full sessions of 5-minute bars plus 21 daily bars | Rebuilt pre-market, about 08:30 ET |
| Optional: Nasdaq Trader trade-halt RSS (public, no key) | nasdaqtrader.com | Authoritative halt codes (LUDP = LULD pause, T1 = news pending, H10 = SEC); see 2.3 |

Extended-hours bars are **not** inputs (see 1.5).

### 1.3 Baseline pack (per symbol, per day; prior 20 complete sessions; half-days excluded)
- **Time-of-day multiplier m_j** (pooled across the universe, shared by all symbols):
  - For each session and symbol: a_j = |lr_j| / median over j'≥1 of |lr_j'|, where lr_j = ln(C_j/C_{j-1}) and j ≥ 1.
  - p_j = median over symbols, then median over the 20 sessions.
  - m_j = clamp(p_j / median_{j≥1}(p_j), 0.6, 4.0), with m_0 := m_1.
- **β (beta against SPY):**
  - Zero-intercept OLS of lr_j/m_j on lrS_j/m_j over the 20 × 77 slots. Dividing by m_j keeps the opening minutes from dominating.
  - β = clamp(0.5·β_raw + 0.5, 0.3, 2.5). β_SPY = 1.
- **σ5 (residual 5-minute volatility, robust):**
  - σ5 = max(1.4826 · median |lr_j/m_j − β·lrS_j/m_j|, 0.0002).
  - For SPY: σ5_SPY = 1.4826 · median |lrS_j/m_j|.
  - Measured examples (2026-09-25): AAPL 8.4 bp; NVDA 8.5; TSLA 12.8; COIN 23.7; KO 6.9; SPY 3.6.
- **vm_j (typical volume at slot j):**
  - Median over the 20 sessions of V at slot j.
  - Smoothed as (vm_{j-1}+vm_j+vm_{j+1})/3 for j = 1..76. Slots 0 and 77 are not smoothed because they hold the auctions.
  - Floor of 1 share.
- **cvm_j:** median over the 20 sessions of cumulative volume through slot j.
- **σd:** sample standard deviation of the last 20 daily close-to-close log returns.
- **ADV20$:** median of daily Close × Volume over 20 sessions.
- **medbar$:** median of V_j·C_j over slots 1..76 across the 20 sessions.
- **missing_share:** the share of the 20 × 78 slots with no bar.
- **n_base:** the number of those 20 sessions that have data.

### 1.4 Two-stage scan (keeps the 5-minute loop cheap)
- **Stage A (every tick, all symbols):** batch quotes (last price, regular-session day volume, SPY last).
  - Compute zday_raw = ln(P/PC)/σd, zday_rel (as in 2.1, using SPY's day return) and rvolc_est = dayVol / cvm_k.
  - Pass if (|zday_raw| ≥ 1.5 or |zday_rel| ≥ 1.5) and rvolc_est ≥ 1.2.
  - Replay: about 6.1% of symbol-ticks pass, and 100% of REC entries passed.
- **Stage B:** fetch today's 5-minute bars for the Stage A passers, all members, all HEATING names, SPY, QQQ and IWM. Only these run the full state machine.
- If Stage A is unavailable, fall back to Stage B for the whole universe (about 60 s for 491 names) and accept the latency.
- Symbols that fail Stage A are IDLE by definition (the in-play gate cannot pass). Members never depend on Stage A.

### 1.5 Extended hours policy
- Yahoo 5-minute pre-market and after-hours bars carry no volume, and 20-70% of slots are missing. So in extended hours there is no relative volume, no VWAP and no baseline.
- **No state machine outside 09:30-16:00.**
- **Optional "Pre-market gap watch"** (08:00-09:30, informational only, never members, feeds nothing):
  - Rule: |ln(pre-market price / PC)| ≥ 2·σd.
  - Labelled "volume unconfirmed".
- The gap reaches the radar at the open through zday, which is measured from PC.
- **After 15:55:** the list is frozen as "Session closed". After-hours earnings moves are handled the next morning through zday and the in-play gate.

---

## 2. Features (bar k is the last final bar; d ∈ {+1, −1} is the candidate direction)

### 2.1 Definitions
- **Anchor rule:** a(n) = max(k − n, 0).
  - Returns never reach back before C_0, the close of the 09:30 bar. The opening auction print and the first 5 minutes are an anchor, never part of a thrust.
  - n_eff = k − a(n).
- **Relative n-bar return:** rr_n = ln(C_k/C_a) − β·ln(S_k/S_a), where S is SPY's close.
- **Scale:** sc_n = σ5 · sqrt( Σ_{j=a+1..k} m_j² ).
- **Thrust z-scores:** z3 = rr_3/sc_3 (15 minutes) and z6 = rr_6/sc_6 (30 minutes).
  - Worked example: TSLA z3 = 2.5 means about 47 bp relative in 15 minutes at midday (m ≈ 0.85), or about 126 bp at 09:50.
- **SPY's own z:** zS6 = ln(S_k/S_a)/(σ5_SPY · sqrt(Σ m_j²)).
- **Raw 30-minute return:** r6 = ln(C_k/C_{a(6)}).
- **Acceleration** (k ≥ 6, else 0): acc = [rr(k−3→k) − rr(k−6→k−3)] / (σ5 · sqrt(Σ_{j=k−5..k} m_j²)).
- **Burst relative volume:** rvol3 = Σ_{j=k−2..k} V_j / Σ_{j=k−2..k} vm_j.
- **Session relative volume:** rvolc = Σ_{j=0..k} V_j / cvm_k.
- **VWAP:** VWAP_k = Σ_{j≤k} tp_j·V_j / Σ_{j≤k} V_j, with tp = (H+L+C)/3.
  - σday5 = σ5·sqrt(Σ_{j=1..77} m_j²).
  - dvwap = ln(C_k/VWAP_k)/σday5.
- **New extreme** (k ≥ 3; else false):
  - newhi = max(H_{k−2..k}) > max(H_{0..k−3}).
  - newlo = min(L_{k−2..k}) < min(L_{0..k−3}).
  - ext(d) = newhi if d = +1, else newlo.
- **Efficiency ratio:** er6 = |C_k − C_{a(6)}| / Σ_{j=a(6)+1..k} |C_j − C_{j−1}|. It is 0 if the denominator is 0.
- **Day context (index-relative):** zday = [ln(C_k/PC) − β·ln(S_k/PC_SPY)] / σd.
  - Replay: raw and relative zday performed the same in this calm sample (58.3% vs 60.4% prec@30).
  - Relative is required: it stops every high-beta name from counting as "in play" on a broad selloff.
- **Display only:** gapz = ln(O_0/PC)/σd; cons = the count of consecutive latest bars closing in direction d.
- **Freshness:** fresh = bars k−2..k all have `present` and V > 0.
- **Dollar volume:** dollar3 = Σ_{j=k−2..k} V_j·C_j.

### 2.2 Missing and partial bars
- An in-progress bar is never used (1.1).
- A missing slot is filled flat with V = 0 and `present=false`. Its time still counts in sc_n (the halted or no-trade time is real time).
- Any non-fresh symbol cannot enter. For a member, 3 consecutive non-fresh bars without halt evidence trigger the DATA_STALE exit.
- **First bars:** entries only from k = 3 (bar 09:45-09:50, tick about 09:51). At k = 3, z6 is still anchored at C_0.
  - Replay: pushing the first entry to 10:05 lowered prec@15 from 57.6% to 53.3%, so the early window is worth keeping.
  - The large m_j at the open (3.6 → 1.5) keeps the open from flooding the list.
- **Close:** the 15:55 bar (closing imbalance and MOC flow, about 10x volume) is never evaluated for entries or holds. The tick for bar 76 (15:50-15:55) is SESSION_END.

### 2.3 Halts and LULD
- **Halt evidence** for symbol i at slot k (any one of):
  - (a) The Nasdaq halt RSS lists i as halted.
  - (b) All of the following:
    - slot k is `present=false`, or has V = 0 with H = L;
    - SPY has slot k;
    - missing_share_i ≤ 2%;
    - and either the previous or the next present bar has |lr| ≥ 4·σ5·m_k, or slot k−1 was also missing.
  - Isolated single missing slots in mid-liquidity names are not halts; this is why (b) needs corroboration.
- **HALTED overlay** (on top of HEATING, RACING or COOLING). While halted:
  - there are no transitions except SESSION_END and HALT_LONG;
  - dwell, soft-fail, stall and stale counters are frozen;
  - HEATING is cancelled (back to IDLE).
- **Resume** (first present bar with V > 0) opens a reopen window of 2 bars:
  - No new HEATING or entry for that symbol, because the reopening auction print is not a thrust.
  - A member can still take hard exits (REVERSAL or GIVEBACK) on the reopen bar.
- **HALT_LONG exit:** 6 or more consecutive halted bars (30 minutes), or a T1/H10 news or regulatory halt of any length.

---

## 3. Universe and liquidity filter

**Daily (pre-market, from the baseline pack)**
- Common stocks or ADRs only. ETFs are references only.
- PC ≥ $5.00
- ADV20$ ≥ $25M
- medbar$ ≥ $150k
- missing_share ≤ 2%. This excludes PARA-type data junk and thin names.
- n_base ≥ 15, so recent IPOs and renamed tickers are excluded until they have history.
- **Split or corporate-action guard:** exclude the symbol for the day if |ln(O_0/PC)| ≥ 8·σd and O_0/PC is within 3% of one of 1/2, 1/3, 1/4, 1/5, 1/10, 2, 3, 4, 5 or 10. This catches an unadjusted split.

**Per tick**
- fresh
- dollar3 ≥ $750k
- not HALTED
- not in a reopen window

---

## 4. Entry rule

### 4.1 Direction
d = sign(z6) if |z6|/3.0 ≥ |z3|/2.5, else sign(z3). If that sign is 0, d = +1.

### 4.2 Gates (all must hold on bar k, with 3 ≤ k ≤ 65, meaning bars that close between 09:50 and 15:00)

**E1 thrust**
- (d·z3 ≥ 2.5 + b or d·z6 ≥ 3.0 + b), and d·z3 > 0 and d·z6 > 0.
- b = 0.5 when MARKET mode is on and d equals the market direction (section 7); otherwise b = 0.

**E2 absolute floor**
- d·r6 ≥ 0.75%. This stops utilities and other low-volatility names from triggering on tiny moves.

**E3 participation**
- rvol3 ≥ 2.0 and rvolc ≥ 1.0.

**E4 structure**
- ext(d), d·dvwap > 0 and er6 ≥ 0.45.

**E5 in play (the anti mean-reversion gate)**
- d·zday ≥ 2.5 and rvolc ≥ 1.5.

**E6 eligibility**
- Passes section 3.
- Not blocked by the re-entry rules in section 6.4.

When E1-E6 pass: IDLE → HEATING, recording heat_px = C_k, heat_k = k and dir = d.

### 4.3 Confirmation (next processed bar k+1, with k+1 ≤ 66)
- **C1:** d·(C_{k+1} − heat_px) ≥ 0. Price has not slipped back from where it was flagged.
- **C2:** d·z6 ≥ 2.0.
- **C3:** rvol3 ≥ 1.3.
- **C4:** d·dvwap > 0 and d·z3 > 0.
- **C5:** still eligible, fresh, not halted.
- **All pass:** the name becomes a candidate for RACING (admission in section 7).
- **Any fail:** back to IDLE. There is no cooldown, and the name can re-heat from the next bar.
- **Fast path (single-tick entry): off by default.** Tested with score ≥ 80 and rvol3 ≥ 4, it gave no precision gain (prec@30 about 48-49% against about 50% in the earlier variant grid) and a higher flap rate. Keep the switch `fast_path_score: null`.

### 4.4 Composite score (0-100): "race intensity", used for ranking and display only
With lin(x, a, b) = clip((x − a)/(b − a), 0, 1):
- s1 thrust = lin(max(d·z3/2.5, d·z6/3.0), 0.5, 2.0)
- s2 burst volume = lin(log2(rvol3), 0, 3), so rvol3 = 1 scores 0 and rvol3 = 8 scores 1
- s3 structure = 0.4·ext(d) + 0.3·lin(er6, 0.3, 0.8) + 0.3·lin(d·dvwap, 0, 1.0)
- s4 day context = lin(d·zday, 0.5, 3.0)
- s5 acceleration = lin(d·acc, 0, 2)
- **score = 100·(0.35·s1 + 0.25·s2 + 0.20·s3 + 0.10·s4 + 0.10·s5)**

Among REC entries, the score's Spearman correlation with the 30-minute forward return was −0.09. The top quartile had prec@30 of 54%, against 65% in the bottom quartile. **Never present the score as a probability or as confidence.** Label it "intensity". Very high thrust and acceleration are mildly associated with exhaustion.

---

## 5. States and refresh semantics

| State | Member? | Meaning |
|---|---|---|
| IDLE | no | Nothing is happening, or the name is not eligible |
| HEATING | no (UI may show it as "warming, unconfirmed") | Passed E1-E6 on the last bar and waits for one confirming bar. Lasts exactly 1 bar |
| RACING | **yes** | Confirmed, and the hold conditions pass |
| COOLING | **yes** | A member with at least one consecutive soft fail. It returns to RACING on the first clean bar |
| COOLDOWN | no (UI: "recently dropped", with the reason) | Exited. Re-entry is blocked by the rules in 6.4 |
| HALTED | overlay flag | Frozen (2.3) |

**Recomputed every processed bar for each member (RACING or COOLING):**
- All section 2 features and the score.
- dwell += 1 (bars since entry; not incremented while HALTED).
- peak: max H since entry for d = +1, min L for d = −1.
- last_ext: updated to k when ext(d) is true.
- giveback ratio.
- move since entry: raw %, SPY-relative %, and in σ units.
- soft-fail counter and stale counter.

Evaluate the exits in section 6 in the order given.

**Recorded once, at entry:**
- entry bar and time, entry_px = C_k of the confirming bar, dir, episode_no
- base = min(C_{k−6..k}) for d = +1 (max for d = −1)
- entry features z3, z6, zday, rvol3, rvolc, dvwap, score
- params_version

---

## 6. Exit rule (hysteresis): the first matching rule wins

### 6.1 Session and data rules
1. **SESSION_END:** processing slot 76 (the tick after 15:55). Every member exits and every HEATING name is cleared. The closing auction bar is never judged.
2. **HALT_LONG:** see 2.3. While a member is HALTED and has not hit HALT_LONG, skip rules 3-5.
3. **DATA_STALE:** 3 consecutive non-fresh bars without halt evidence.

### 6.2 Hard exits (fire immediately; they ignore minimum dwell)
4. **REVERSAL:** d·z3 ≤ −2.5. A sharp 15-minute move against the direction.
5. **GIVEBACK:** move = d·(peak − base) > 0 and d·(peak − C_k) ≥ 0.7·move.
6. **VWAP_CROSS:** d·dvwap < 0. Price is back through session VWAP. In the replay this was only a backstop: it fired first in 1.8% of exits.

### 6.3 Soft fails (need minimum dwell and 2 consecutive failing bars)

**Checks**
- **FADE:** d·z6 < 1.0 and d·z3 < 0.0.
- **DRY:** rvol3 < 0.6.
- **STALL:** (k − last_ext) ≥ 6 and d·z6 < 1.5. No new extreme for 30 minutes and momentum is gone.

**Handling**
- If any check is true: soft += 1 and the state is COOLING.
- The member exits when dwell ≥ 3 and soft ≥ 2. The reason is the first true check, in the order FADE, DRY, STALL.
- Otherwise: soft = 0 and the state is RACING.
- **Minimum dwell** is 3 bars (15 minutes). It applies only to soft exits.

### 6.4 Re-entry rules (anti-flap)
- **Same direction:** at least 6 bars since the exit, **and** price beyond the previous episode's peak: d·(C_k − last_peak) > 0. This "new peak" rule means a stock that lingers near its old high cannot flap back in.
- **Opposite direction:** at least 3 bars since the exit.
- **At most 3 episodes** per symbol per session.
- A DISPLACED exit (section 7) gets the same cooldowns.

### 6.5 What the replay says about the exits
- Median dwell was 8 bars (40 minutes), IQR 5-11.
- Exit reasons: FADE 48%, REVERSAL 45%, STALL 2%, VWAP_CROSS 2%, SESSION_END 2%.
- After an exit, the move carried on in the same direction 55% of the time. Exits are slightly early, which is the right side to err on for a "racing now" list.

Tested alternatives:

| Setting | Flap rate | Median dwell (bars) | Note |
|---|---|---|---|
| REVERSAL z3 2.0 / GIVEBACK 0.6 | 6.5% | 7 | More flapping |
| REVERSAL z3 3.0 / GIVEBACK 0.8 | 2.0% | 8 | Still plausible |
| Soft fails = 1 | about the same | 6 | Slightly better exit timing; loses the visible COOLING warning |

Hysteresis ablation, showing each piece's contribution to the flap rate:

| Setting | Flap rate |
|---|---|
| All rules on | 3.7% |
| Without cooldowns | 5.3% |
| Without minimum dwell | 4.1% |
| All hysteresis off | 11.6% |

---

## 7. Caps, ranking and market-wide moves

**Caps**
- MAX_MEMBERS = 12.
- MAX_NEW_PER_TICK = 4.
- Admission order: the bar's confirmations sorted by score, highest first.
- **When the list is full:**
  - A candidate replaces the lowest-score member only if candidate score ≥ that member's score + 15 and that member's dwell ≥ 3. The displaced member exits as DISPLACED.
  - Otherwise the candidate goes back to IDLE. It has no cooldown and may qualify again next bar.
- Replay (491 symbols): concurrency median 0, p95 3, max 9. The caps never bound under REC, but they bound constantly for the naive list.

**Sector cap** (untested design; the replay showed its need):
- At most 3 members per (sector, direction). Further qualifiers go into a single "Sector: Financials ↓ (+N names)" banner row.
- The sector map comes from Yahoo `info['sector']` (the `sector` field of the Yahoo symbol info), refreshed weekly.
- Evidence: on 2026-09-22 the radar listed 10 financial and defense names falling, all index-relative, while SPY was flat.

**Market-wide handling**
1. **Everything is SPY-relative:** z3, z6 and zday strip out β·SPY, so a broad rally does not list every high-beta name.
2. **MARKET mode** switches ON when |zS6| ≥ 3.0 or breadth6 ≥ 0.60.
   - breadth6 = |mean over eligible names of sign(r6)|, the net advance/decline share over the last 30 minutes.
   - Replay distributions:
     - |zS6|: p90 1.59, p97 2.45, p99 3.88.
     - breadth6: p50 0.16, p90 0.38, p97 0.50, p99 0.62.
     - Correlation between the two: 0.57.
   - Mode switches OFF only when |zS6| < 2.0 and breadth6 < 0.45 for 2 consecutive bars. The regime flag has its own hysteresis.
3. **While MARKET mode is on (market direction = sign(zS6)):**
   - A pinned MARKET row (SPY and QQQ with zS6 and breadth) replaces the flood.
   - Entries in the market direction need +0.5 on the E1 thrusts, and at most 2 of them are admitted per tick.
   - Entries against the tape (relative strength or weakness) are unaffected.
   - Existing members keep their normal rules.
4. **Validation status:** MARKET mode was active on 1.8% of ticks in the sample. The replay applied only the SPY-z trigger with the +0.5 bump. The breadth trigger and the per-direction cap are untested, and no day with SPY moving 2% or more was in the sample.

---

## 8. Expected behaviour and calibration

### 8.1 Targets for a 500-600 symbol universe
Scale factor from the replay: 600/491 ≈ 1.2.

| Metric | Target | Replay REC (491) |
|---|---|---|
| Entries per session | 4-10 (median about 5, p90 16 or less) | mean 5.7, median 4, p90 13, max 15; 1 of 39 days had none |
| Concurrent members | p95 4 or less; cap reached on no more than 1% of ticks | median 0, p95 3, max 9 |
| Dwell median | 30-45 min (6-9 bars) | 8 bars |
| Flap rate / flaps per day | 5% or less / 0.5 or less | 3.6% / 0.21 |
| prec@30 | 55% or more, and at least 8 points above the naive thrust list in the same period | 58.3% (naive 46.7%) |
| prec@15 | 55% or more | 61.0% |
| hold30 | 85% or more | 91.5% |
| Post-exit continuation | 55% or less | 55.2% |

- The list is **empty on most ticks**. That is the price of false-positive control.
- If the product needs a busier list, use the "busy" preset: `inplay_zday = 2.0`. Grid: 5-13 entries a day and about 53% prec@30.
- Do not surface thrust-only names as members; they continue less than half the time.

### 8.2 Scorecard definitions (exact)
- **Entry price:** P_e = C at the confirming bar's close. Also report the same metrics from one bar later (the latency view).
- **Forward return:** f_h = d·[ln(C_{k+h}/C_k) − β·ln(S_{k+h}/S_k)] for h = 3, 6, 12 bars. The horizon stops at slot 77.
- **prec@h:** mean(f_h > 0).
- **hold30:** d·(C_{k+6} − P_e) > −0.5·d·(P_e − base).
- **MFE > MAE:** within 6 bars, using H and L.
- **Dwell:** bars from entry to exit. A short episode has dwell ≤ 2.
- **Flap:** a short episode, or a re-entry of the same symbol within 6 bars of its exit, in either direction.
  - flap_rate = flaps / entries.
  - flaps/day = flaps / sessions.
- **Post-exit continuation:** mean(d·relative return from exit to exit + 6 bars > 0).
- **Concurrency:** members after each bar; report median, p95, max, and the share of ticks with the cap reached.
- **Baselines** (always computed on the same period):
  - (i) Every eligible symbol-tick with d = sign(z3), at 30 minutes.
  - (ii) The naive thrust list: E1 + E2, one tick, no hysteresis.
- **Uncertainty:** report Wilson 95% intervals and the number of distinct symbol-days. Entries cluster by symbol-day and by theme.

### 8.3 Calibration method (replay; deterministic)
1. **Data:** Yahoo 5-minute bars for the last 60 days (the maximum Yahoo allows), the universe plus SPY, and 1 year of daily bars.
   - Sessions 1-20 seed the baselines only.
   - Split the remaining sessions into calibration (first half) and validation (second half).
2. **Stage 1, entry grid:**
   - z3 in {2.0, 2.5, 3.0}, with z6 = z3 + 0.5
   - inplay_zday in {1.5, 2.0, 2.5, 3.0}
   - inplay_rvolc in {1.5, 2.0}
   - rvol3 in {1.5, 2.0}
   - Objective: maximise min(prec@30 calibration, prec@30 validation), subject to the 8.1 volume targets.
   - **Prefer a plateau:** choose a cell whose neighbours are within 3 points. Never pick an isolated peak.
3. **Stage 2, exit grid** (entries fixed):
   - REVERSAL z3 in {2.0, 2.5, 3.0}, GIVEBACK in {0.6, 0.7, 0.8}, soft fails in {1, 2}, stall bars in {4, 6, 9}.
   - Objective: minimise flap rate, subject to post-exit continuation ≤ 55% and median dwell ≥ 6.
4. **Cadence:** re-run monthly, on the first weekend. Promote a new `params_version` only if it beats the current one on the newest 20 sessions without breaking any target.
5. **Live shadow log:** each tick, persist the features of members and HEATING names as the engine saw them live.
   - Every week, rebuild the same bars from Yahoo history and compare.
   - Alert if more than 5% of live entries would not have entered on the revised bars (volume revisions or late prints).
6. **Health monitor (no auto-tuning, so it stays deterministic):**
   - Rolling 5-session entries per day greater than 20, flaps per day greater than 1.0, or cap reached on more than 5% of ticks → write a warning to `health.json`.
   - Parameters change only by a config commit.

### 8.4 Parameter block (reference defaults)
```json
{
  "params_version": "radar-sm-1",
  "session": {"first_entry_slot": 3, "last_entry_slot": 65, "last_confirm_slot": 66, "session_end_slot": 76, "bar_final_grace_s": 60},
  "prefilter": {"zday_abs": 1.5, "rvolc": 1.2},
  "universe": {"price_min": 5.0, "adv20_usd_min": 25000000, "median_bar_usd_min": 150000, "missing_share_max": 0.02, "min_baseline_sessions": 15, "tick_dollar3_min": 750000},
  "baseline": {"sessions": 20, "tod_clamp": [0.6, 4.0], "beta_shrink": 0.5, "beta_clamp": [0.3, 2.5], "sigma5_floor": 0.0002},
  "entry": {"z3": 2.5, "z6": 3.0, "abs_r6": 0.0075, "rvol3": 2.0, "rvolc": 1.0, "er6": 0.45, "inplay_zday": 2.5, "inplay_rvolc": 1.5},
  "confirm": {"z6": 2.0, "rvol3": 1.3, "fast_path_score": null},
  "hold": {"fade_z6": 1.0, "fade_z3": 0.0, "dry_rvol3": 0.6, "stall_bars": 6, "stall_z6": 1.5, "min_dwell": 3, "soft_fails": 2},
  "hard_exit": {"reversal_z3": 2.5, "giveback": 0.7, "vwap_cross": 0.0, "stale_bars": 3, "halt_long_bars": 6},
  "reentry": {"cool_same_bars": 6, "cool_opp_bars": 3, "require_new_peak": true, "max_episodes": 3},
  "caps": {"max_members": 12, "max_new_per_tick": 4, "displace_margin": 15, "max_per_sector_dir": 3},
  "market": {"on_spy_z6": 3.0, "on_breadth": 0.60, "off_spy_z6": 2.0, "off_breadth": 0.45, "off_bars": 2, "bump": 0.5, "max_new_mkt_dir": 2},
  "halt": {"reopen_block_bars": 2, "move_sigma": 4.0},
  "score_weights": {"thrust": 0.35, "burst_vol": 0.25, "structure": 0.20, "day": 0.10, "accel": 0.10}
}
```

### 8.5 State to persist (per session; rebuilt from bars if lost)
- **Per member:** sym, dir, state, halted, entry_slot, entry_time_ET, entry_px, base, peak, last_ext, dwell, soft, stale, score, z3, z6, zday, rvol3, rvolc, dvwap, episode_no, late, params_version.
- **Per symbol (memory):** heat_px, heat_slot, heat_dir, last_exit_slot, last_exit_dir, last_peak, episodes_today.
- **Global:** last_processed_slot, session date, market_mode, and the counter for switching market mode off.
- **Exit records:** everything above plus exit_slot, exit_px, reason, and relative return to exit. These make up the history table.

---

## 9. Known failure modes

1. **The edge is small and depends on the regime.**
   - The calm Aug-Sep 2026 sample had no crash day and no SPY day of 2% or more.
   - Calibration and validation differed by 9 points (53% vs 62%).
   - With n = 223 the 95% interval is about 52-65%; the improvement over the naive list is solid, while the absolute level of 58% is not.
2. **Late by design.**
   - The in-play gate needs the stock's day move (after removing the market) to be at least 2.5σd. For TSLA (σd 2.8%) that is about 7% versus the market, so quiet names starting to run at 14:00 are missed until the move is large.
   - Confirmation adds one bar, and cron lag can add more.
   - One bar of lag costs about 2-3 points of precision.
3. **Mean-reversion trap outside the gate.** The rule is sound only while the in-play gate holds. Dropping it (for example to fill the list) gives a list that continues less than half the time.
4. **Yahoo data risks.**
   - The last bar is in progress.
   - Live bar volume can be revised later.
   - Thin names have isolated missing slots.
   - Extended hours have no volume.
   - Unadjusted splits and ticker changes can appear (PARA shows ±37% 5-minute junk).
   - These are mitigated by the finality grace, the missing_share filter, the split guard and the shadow log, but not eliminated.
5. **Halt detection is a heuristic** unless the Nasdaq halt RSS is used. The reopen print can look like a thrust, which the 2-bar reopen block handles. The halt logic is untested in the replay: the one real LULD case, LCID on 2026-07-14, fell before the replay window.
6. **Theme and sector clustering** produces correlated entries (2026-09-22: 10 financial and defense names falling). Without the sector cap the list shows one theme 10 times.
7. **Market crash or melt-up.** Correlations spike and β estimates break down. Relative z is then noisy and breadth saturates. MARKET mode is a guard but has not been tested on a real stress day.
8. **Score is not predictive.** When caps bind, ranking by intensity favours the most extended names, which are mildly exhaustion-prone.
9. **Direction mix.** 57% of the replay entries were down moves (a sample effect). Short-side mechanics (borrow, uptick rule) are ignored. This is educational only.
10. **Session edges.**
    - Everything is forced out at 15:55, so the list cannot express overnight continuation.
    - No entries after 15:00. In the replay, entries from 15:00 to 15:30 continued 15 minutes later only 28% of the time (n = 18).
    - Half-days need the shortened slot map.
11. **Runner outages.** Catch-up processing is correct but late, so entries are flagged `late`. If the runner misses more than 30 minutes, show "radar paused" instead of a stale list.
12. **Baseline shocks.**
    - An earnings day inflates the next 20 sessions' σ5 and volume medians. The next weeks become less sensitive for that name (robust medians only partly help).
    - New listings have no baseline until n_base ≥ 15.

---

## 10. Pseudocode of the per-bar step (to port from `replay.run`)
```
for each final bar k not yet processed (in order):
    f = features(all Stage-B symbols, k)                     # section 2, vectorised
    update market_mode (zS6, breadth6, with on/off hysteresis)
    for s in members:                                        # RACING or COOLING
        if halted(s): freeze; check HALT_LONG / SESSION_END; continue
        dwell++, update peak / last_ext / stale
        reason = SESSION_END | HALT_LONG | DATA_STALE | REVERSAL | GIVEBACK | VWAP_CROSS
                 | soft-fail(FADE / DRY / STALL) with min_dwell=3 and soft>=2
        if reason: record exit, set cooldown memory, state=COOLDOWN
    for s in HEATING: confirm (C1..C5) -> candidate list, or back to IDLE
    if 3 <= k <= 65: for s in IDLE (Stage-B): E1..E6 -> HEATING
    admit candidates by score: max 4 per tick, cap 12, sector cap 3, displacement +15,
          MARKET-mode cap 2 in the market direction
    persist state + tick snapshot (members, heating, market row, health counters)
```
