# IV cost score: design

Branch `feat/iv-score`, 2026-10-23. Approved 2026-10-23 with the four proposed decisions (section 9).

One 0–100 score answers a single question for someone **about to buy** an option: *is the volatility I am paying for cheap or expensive right now?* It replaces the IV rank badge (dashboard), the IV/HV badge (Radar) and the IV rank / percentile tiles (Options Lab). Each badge expands to show why. Holder-side warnings (IV crushing today, earnings just reported) stay separate and unchanged.

## 1. What we found in the data

- **The stored IV history is mostly noise.** `options_snapshots.atm_iv` is the IV of the single front-week call nearest the money, often 0 DTE. It is captured at 09:35 and 16:15 ET, when Yahoo quotes are blank or stale. Examples:
  - AMZN reads 0.00 → 0.07 → 0.03 → 0.61 within days.
  - NOK reads 2.96 and 5.88.
  - The median day-to-day change is ±60% (log 0.48).
  
  Rank and percentile built on it are unreliable. A quarter of tickers show percentile and rank 25+ points apart today.
- **History depth:** clean or not, the history starts on 2026-04-03, so no ticker has a year. The median ticker has 61 days.
- **Radar coverage:** only 22 of the 213 Radar names of the last week have any snapshot history.
- **Three definitions of "ATM IV":**
  - Pipeline: front call.
  - Options Lab: median of calls and puts within ±2.5%, nearest expiry.
  - Radar: call at ≥ 7 DTE plus a 30-day reading.
  
  The Options Lab ranks its IV against history built the pipeline's way.
- **Realised vol:** only the Radar computes it (HV20). The pipeline and the Options Lab have none.

So the score cannot simply combine today's rank and percentile. It needs a clean measurement first.

## 2. Step 0: one clean IV measurement and a daily history

**One definition, `utils/iv_measure.py`, used everywhere.**
- **IV30:** constant-maturity 30-day ATM IV.
  - For each of the two expiries around 30 DTE, average the call and put IV at the strike nearest spot.
  - Interpolate in total variance to exactly 30 days.
- **Front IV:** the same measure at the first expiry ≥ 7 DTE. It is used only for the term ratio, front / IV30.
- **Quality gate:** a contract counts only if it has a two-sided quote (bid > 0, ask > 0, spread ≤ 25% of mid) and IV between 5% and 300%. No valid quote means **no reading**, never a junk number.
- **HV20 / HV60:** close-to-close realised volatility, annualised, from daily closes. This is the same method as the Radar's HV20.

**New table `iv_daily`** (ticker, day, iv30, iv_front, front_dte, term_ratio, hv20, hv60, spot, captured_at).
- One row per ticker per trading day.
- Captured by a backend job starting at **15:00 ET** on trading days, during the session when quotes are live.
- Universe: active watchlist, open positions, research tickers of the last 30 days, and the Radar universe (`radar/universe.py`, about 550 names).
- Cost: about 2–3 s per ticker at the 0.5 s yfinance limit, so roughly 25 minutes in all.
- A ticker the pipeline or Research needs that has no reading in the last 5 trading days is measured on demand. Outside market hours that gives no reading, and the score says "IV unknown".

**Backfill (added 2026-10-23):** Yahoo has no IV history, but EODHD's US options product ($29.99/month, about 2 years of end-of-day chains with bid, ask and IV per contract) does.
- `services/iv_backfill.py` rebuilds a year of IV30 for the capture universe with the same code. That is about 150k requests, one per ticker and day.
- Rows are stored as `source='eodhd'`, and live captures as `yahoo`.
- Checked with the demo key:
  - AAPL had a clean reading on 51 of 51 sampled days (18.6–30.4%), with the front IV rising into each earnings date.
  - AMZN's front IV on Oct 19 was 29.4% from EODHD at the close against 29.2% from Yahoo in the afternoon.
- Without a key, the history starts clean on the first capture day.

HV is computed from daily closes each time. The old `atm_iv` history is no longer used for rank or percentile. It is kept, not deleted, and new snapshots store the clean IV30 in that column.

## 3. The score

Every part is a 0–100 sub-score where 50 means normal, higher means more expensive for a buyer.

**H · Versus its own history** (needs ≥ 20 clean days of IV30)
- `pct` = share of the window's days with IV30 at or below today's IV30. This is the primary input.
- `size` = today's IV30 / the window median − 1. A 1-point move in a tight range then does not count as extreme.
- `H = 50 + (pct − 50) × min(1, |size| / 20%)`. In words, the percentile only counts in full once IV is at least 20% away from its normal level.
- **Rank** = (IV − low) / (high − low). It is shown in the details as a cross-check only. When the percentile exceeds the rank by 25+ points, the panel says "one spike stretches the 1-year range".
- **Regime:** if the median of the last 60 days is below 0.7× or above 1.4× the 1-year median (and 100+ days exist), the window becomes the last 60 days. The panel says so ("IV regime changed: 60-day median 30% vs 1-year 58%; compared with the last 60 days").

**R · Versus the movement the stock delivers** (IV30 / HV20)
- The mapping is piecewise linear on the log ratio:

  | IV/HV | 0.8 | 1.0 | 1.2 | 1.5 | 2.0 | 3.0 |
  |---|---|---|---|---|---|---|
  | R | 10 | 22 | 35 | 60 | 80 | 100 |

- The knots are chosen so that R alone reproduces today's Radar cut-offs: cheap ≤ 1.2, pricey ≥ 1.5, expensive ≥ 2.0.
- HV60 is also shown, for context.

**Combining:** `score = (wH·H + wR·R) / (wH + wR)`
- wR = 0.5.
- wH = 0.5 × min(1, (clean days − 20) / 100), so 0 below 20 days and full at 120 days.
- A missing part is dropped, and the remaining part carries the score.
- **Confidence:**
  - High: H at full weight, plus R.
  - Medium: H partly weighted.
  - Low: R only, or H only.
  - Shown as 1–3 dots on the badge.

**Labels** (existing palette):

| Score | Label | Colour |
|---|---|---|
| < 35 | Cheap | green |
| 35–59 | Fair | grey |
| 60–79 | Pricey | amber |
| ≥ 80 | Expensive | red |

**Event premium**
- Trigger: earnings before the expiry in question (or within 30 days for IV30), or term ratio ≥ 1.2.
- Effect: a separate **"Event priced"** tag with the date ("Earnings Oct 29 priced in: IV usually drops right after").
- Rule (decision 1): the score stays the cost now, and the label is floored at **Pricey** while the event is ahead. Before this change the Radar showed Expensive in that case.

## 4. Worked examples (made-up numbers, run through a prototype)

| Case | Inputs | Old reading | New score |
|---|---|---|---|
| One spike | IV 25–35% all year plus one 90% day; today 40%, HV 30% | rank 23 "cheap", pct 100 | H 100, R 47 → **73 Pricey**, note "spike stretches range" |
| Tight range | IV 28–33% all year; today 32.5%, HV 28% | pct 87, rank 91 "expensive" | size +6% → H 62; R 33 → **47 Fair** |
| Regime shift | 9 months near 60%, 3 months near 30%; today 32%, HV 27% | 1-year pct 21 "cheap" | 60-day window: H 62; R 34 → **48 Fair**, regime note |
| No history (most Radar names today) | IV 33.9%, HV 18.4% (ICE, 2026-10-22) | Radar "expensive" (earnings) | R 74 → **74 Pricey**, confidence Low, "Earnings Oct 29 priced" tag |

## 5. Where it appears

- **One backend module, `utils/iv_score.py`:** `iv_score(iv30, hv20, history, events) → {score, label, confidence, parts[], notes[], event}`. It is pure and unit-tested, and is called by:
  - the options snapshot (pipeline),
  - the deep analyzer (Options Lab),
  - the Radar options refresh (live IV30 + stored history).
  
  The API adds an `iv_score` object to `/api/options/{ticker}`, research `options_data`, deep analyses and the Radar options doc.
- **One frontend component, `components/ui/IvScoreBadge.tsx`:**
  - The pill reads "IV Pricey 68 ●●○", coloured by label, with the event tag next to it.
  - Clicking opens a "why" panel with one line per part: value, sub-score bar, weight. The panel also shows the notes (spike, regime, confidence) and the plain inputs (IV30, front IV, HV20, rank, percentile).
- **Replaced:**

  | Place | Today | New |
  |---|---|---|
  | Radar `IvBadge` / `IvSection` | IV/HV colour | Badge + panel. The red "IV crushing" pill on the exits list stays (holder side). |
  | Dashboard / research Options Flow | "IV Rank" row tagged Bullish/Bearish | IV score row (sentiment tags no longer used for IV) |
  | Options Lab header | IV Rank and IV %tile tiles | IV score tile. A fuller breakdown panel adds the weights, the rank-vs-percentile check, the regime note and an IV30 history chart (median and middle-half band) once 20+ days exist. |
  
  Term shape, skew and front-minus-back stay as they are.

## 6. Rules that key off IV rank today

| # | Rule (file) | Today | Proposed |
|---|---|---|---|
| 1 | Options Lab verdict bucket (`deep_options_analyzer`) | LOW < 30, HIGH ≥ 70 | LOW = score < 35, HIGH = score ≥ 60. This uses the number, not the event-floored label, since the matrix already branches on earnings (changed during the build, 2026-10-23). |
| 2 | IV_CRUSH hidden risk | earnings ≤ 14 d and rank > 60 and a BUY verdict | an event priced in (which floors the label at Pricey) and a BUY verdict. With "score ≥ 60" a BUY verdict could never coincide, since HIGH routes to credit spreads (changed during the build). |
| 3 | Signal "High IV Rank" −10 | rank > 70 | "Rich IV" −10 at score ≥ 70 |
| 4 | Signal "IV Spike Warning" −12 | rank +20 pts in 4–8 days | IV30 up 25%+ over 5 days (relative, on clean data) |
| 5 | Signal "Smart Money" IV leg | rank +10 pts in ~5 days | IV30 up 10%+ over 5 days |
| 6 | Risk level LOW | IV rank < 50 (a missing rank counts as 0) | score < 60, and a missing score does not qualify |
| 7 | Naked vs debit spread | rank > 70 → spread | score ≥ 60 → spread |
| 8 | Radar filter "IV vs realised" + Call-friendly preset | IV/HV ≤ 2 | "Max IV cost" (Cheap / Fair / Pricey); the preset excludes Expensive |
| 9 | Radar verdict IV lines, `option_lag.iv_class` | IV/HV and term ratio | score label |

Rules 4 and 5 are about IV *rising*, not its cost, so they move to the clean IV30 change rather than the score. The Knowledge tabs that quote these cut-offs (Trading Guide, Signals, Options Lab, Strategies, `shared.tsx` IV_BUCKETS, plus a new IV section on the Radar tab) get rewritten to the score bands.

## 7. Validation

- **Unit tests:** spike, tight range, regime shift, no history (confidence), event floor, label boundaries, quality gate (junk quotes give no reading).
- **Outcome check** (script, run once 6–8 weeks of `iv_daily` exist): for each score band, compare IV30 on day t with the realised vol of days t..t+21. Expensive should overprice the realised move more often and by more than Cheap does. The weights (50/50) and the 20% size scale get tuned from it.
- **Limit:** with no IV history to backtest against, the history part cannot be validated today, only the IV/HV part (once forward data accrues).

## 8. Build order (each step its own commit, tests and frontend build, no deploy without a go-ahead)

1. `iv_measure.py`, the `iv_daily` table and migration, and the 15:00 ET job. Rank and percentile move to `iv_daily`.
2. `iv_score.py` with tests, and the `iv_score` object in the APIs.
3. `IvScoreBadge` replacing the three badges; the Options Lab breakdown.
4. The rule switches you approve, plus Knowledge texts.
5. The outcome script, run later.

Until `iv_daily` has 20+ days for a ticker, its score runs on IV/HV alone at Low confidence. The badge says so.

## 9. Decisions (2026-10-23)

1. **Earnings before expiry:** the label is floored at Pricey, and an "Event priced" tag is shown.
2. **Rules 1–9:** all switch as proposed.
3. **Daily IV universe:** the full Radar universe plus the watchlist, positions and recent research tickers.
4. **Weights:** start at 50/50 (history vs IV/HV) once history is full; tune after the outcome check.
