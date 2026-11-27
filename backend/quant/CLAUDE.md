# Quant layer: working notes

Measured models shown in the portal. Display only: nothing here feeds conviction scores, the radar engine or
any verdict, and the API labels every reading "not advice". Writes only the `quant_*` tables, through
`quant/store.py`. Every tunable is a commented constant in `quant/config.py`.

## Pieces

- `bars.py` → `quant_daily_bars`: FMP `/historical-price-full` (key in the `apikey` header via
  `radar.fetch.HttpTransport`), Yahoo v8 chart fallback; ≤ 3 FMP req/s; 10-year backfill, then the last
  10 days re-read after each close.
- `chains.py` → `quant_chain_snapshots`: Yahoo v7 chains through `radar.options._get` (no second Yahoo
  client), ≤ 8 expiries out to 120 DTE, strikes within ±30% of spot. One row per (ticker, ET date, expiry);
  `contracts` is columnar (parallel arrays), about half the size of one JSON object per contract.
- `estimators.py` / `har.py` / `iv.py` / `reading.py`: pure functions on arrays, unit-tested without a DB.
- `cleaning.py`: `variance_series`, the one place the data rules act (evaluation, forecast job and audit all call
  it): no-trade bars, missing sessions, split defects, shell history, plus the live `pinned_price` flag.
- `forecast.py` → `quant_vol_forecasts`, each fit → `quant_model_runs` (kind `fit`).
- `evaluate.py` → scorecard printed, recorded as kind `evaluation`: losses per model, the three-state verdict
  (`verdict()`), the per-ticker breakdown (`breakdown()`: median / trimmed per-ticker QLIKE, tickers holding 50% /
  90% of the loss, worst tickers, worst ticker-days with forecast and target) and what the cleaning rules removed.
- `audit.py` → `python -m quant.audit [--csv f] [--json]`, read-only integrity checks of the stored bars (invalid
  OHLC, no-trade / stale runs, split-sized adj_close / close jumps and whether the prices moved with them, gaps vs
  SPY, near-zero trailing vol, per-ticker estimator scale, source mix). Thresholds: `config.AUDIT`.
- Production data is only visible through the Deploy workflow's `quant-audit` request (audit + evaluation
  `--no-record`, nothing written); its output is a public log, so nothing secret may ever be printed.
- Jobs: `python -m quant.run bars|chains|forecast`, scheduled by `services/quant_runner.py`.
  After the close job it also re-runs the evaluation when the scorecard is missing, for another
  `MODEL_VERSION`, or older than `EVALUATION_MAX_AGE_DAYS` (30); logged as `quant_evaluate`.

## Rules

- **Evaluate before shipping a model change.** Bump `MODEL_VERSION`, run `python -m quant.evaluate`, compare
  with the last evaluation row. Ship only what beats `trailing_21d` out of sample; say so when it does not.
  Keep the candidate set declared in `evaluate.MODELS`; do not tune against the out-of-sample window.
- **The verdict has three states** (`evaluate.verdict`, `VERDICT_T` = 2): `better_significant` (lower QLIKE and
  Newey-West t < -2), `lower_not_significant`, `not_better`; plus `long_run_beats_har`. `har_beats_trailing` is
  true only for the first. The API recomputes it from `models`, so older recorded scorecards read the same way.
- **Cleaning rules** live in `ESTIMATOR` with a comment saying which data defect justifies them, act inside
  `cleaning.variance_series` (so every model and the forecast job see the same input), are counted per rule in
  the scorecard's `cleaning.by_rule` (bars, tickers, OOS ticker-days with only that rule off, shell first-usable
  dates, split events), mirrored in `quant.audit`, and bump `MODEL_VERSION`. Never choose one by how much it
  helps HAR; a threshold changed after seeing detections is recorded in its config comment.
- **har-gk-3 rules** (details and thresholds: `config.ESTIMATOR`): *gaps*: each ticker on the panel's majority
  session calendar, a missing session is a NaN row and the bar after it has no overnight return; *splits*: only
  the split day's overnight return is wrong, repaired when level, bar and share volume all say split, missing when
  ambiguous, left when the level did not hold; dividend-sized factor jumps are special dividends; *shell*: a
  start-of-life pinned regime (closes within ±3% for 21 sessions) that the ticker left for good is cut. Mid-life
  pinned regimes (a cash takeover) are the same company and stay. The cut is hindsight by design (a different
  security's prices); its live counterpart is `pinned_price` (reading "not comparable", basis `pinned_price`,
  excluded from the cross-section and own-history references).
- **No look-ahead:** a training sample needs its whole 21-session target closed by the fit date
  (`har.usable_at`). `tests/quant/test_walk_forward.py` fails if that rule is removed.
- **The reading is relative.** IV normally exceeds later realised vol (variance risk premium); compare
  IV / forecast with the ticker's own earlier ratios (60+), else today's cross-section of 30-day-IV names
  (30+), else "insufficient history". Earnings inside the horizon or a pinned price: "not comparable".
- **IV source is always recorded** (`iv_source`): `chain_cm30` > `radar_iv30` > `options_snapshots.atm_iv`. Since
  the IV cost score that column holds the clean IV30 of the day's `iv_daily` reading (`snapshot_iv30`, used only
  when the reading's day is inside the fallback window); older rows hold the call nearest the money across
  expiries (`options_snapshot_atm`, not a 30-day value; flagged `iv_not_30d`). The cross-section reference uses
  only the first two.

## What was measured

| When | Question | Result | Decision |
|---|---|---|---|
| 2026-11-16 | HAR vs trailing 21d RV, walk-forward (Yahoo daily bars, 56 large US stocks + ETFs, 2018-11 .. 2026-10, 111k ticker-days) | QLIKE: trailing 0.373, long-run mean 0.460, HAR pooled 0.286 (NW t -3.6), HAR per-ticker 0.304. RMSE vol: 0.149 / 0.143 / 0.128 / 0.126 | HAR beats trailing; ships |
| 2026-11-16 | Pooled vs per-ticker by year | per-ticker lower QLIKE in every year but 2020 (1.26 vs pooled 0.95); pooled beats trailing every year (ratio 0.70-0.86) | pooled (`HAR.pooled`): robust to a regime break |
| 2026-11-16 | Estimator scale vs close-to-close variance | 1.016 on real bars (synthetic 390-step paths: 0.93, the discrete-sampling bias) | no correction; the reading is relative |
| 2026-11-27 | Same evaluation on the server's `quant_daily_bars` (FMP, 270 tickers, 481k ticker-days, 2018-11 .. 2026-10, har-gk-1) | QLIKE: trailing 16.21, long-run mean 0.60, HAR pooled 2.62 (NW t -1.1), per-ticker 2.63. RMSE vol: 0.324 / 0.319 / 0.283 / 0.285. Scale 1.13 | not confirmed. Explained by the next row: no-trade bars plus two SPAC shells |
| 2026-11-27 | Reproduce on Yahoo bars, 180 tickers (universe + ETFs + split / IPO / SPAC / foreign / thin names), 332k ticker-days | har-gk-1: trailing 0.396, long-run 0.482, HAR pooled 0.308 (t -3.8). har-gk-2: 0.390 / 0.473 / 0.303 (t -3.7), RMSE vol 0.226 / 0.234 / 0.199, scale 1.04; the rule removed 304 bars in 9 tickers (726 OOS ticker-days). Worst days: LCID (CCIV shell, Jan 2021) and RGTI, genuine | blow-up not reproduced. Adding small caps alone lifts RMSE (0.149 → 0.226), so RMSE needs no bad data; the scale only reached 1.04 (per-ticker up to 1.29 for TLN, 1.19 GME / GRAB), so 1.13 is not explained, nor is the QLIKE level |
| 2026-11-27 | `quant-audit` on the server (har-gk-2, same 270 tickers, 479k ticker-days) | QLIKE: trailing 1.135, long-run mean 0.533, HAR pooled 0.555 (NW t -2.06), per-ticker 0.574. RMSE vol 0.306 / 0.304 / 0.256 / 0.258. Per-ticker median QLIKE: 0.345 / 0.359 / 0.267 / 0.268. The no-trade rule dropped 1,135 bars in 23 tickers (near-zero trailing days 199 → 48). NAMS and QBTS hold 55% of the trailing loss and 35% of HAR's: pre-merger SPAC shells pinned near trust value (~2% vol) followed by 200-340% vol. Audit: all bars from FMP; median per-ticker scale 1.02 (pooled 1.13 comes from a few names: BMNR 2.18, NAMS 1.89, QXO 1.43); split-ratio moves left in OHLC for BMNR, QXO, SMMT, LUNR, MARA, APLD; adj_close / OHLC inconsistent around splits for QXO, RIOT, WULF; BMNR misses 1,059 sessions, ONDS 440 | the blow-up was no-trade bars (fixed) plus shell history. HAR is the best model on a typical ticker; its mean QLIKE is not better than the long-run mean until shell history and the split defects are handled. Readings stand as display-only with that caveat |

| 2026-11-27 | har-gk-3 rules on Yahoo bars, 207 tickers (the 180 + the server's trouble names, no cash funds), 371k ticker-days | har-gk-2 rules only: trailing 1.323, long-run 0.587, HAR pooled 0.663 (t -1.98): the server's shape, reproduced. Shell rule alone = all rules: 0.418 / 0.451 / 0.318 (t -3.36), per-ticker 0.324 (t -3.87); RMSE vol 0.256 / 0.267 / 0.224; 3,214 bars cut in 12 tickers (NAMS, QBTS, USAR, LUNR, DKNG, ASTS, CHPT, JOBY, AUR, ACHR, SPCE, OKLO; each end within days of the merger vote / announcement). Gaps and splits change nothing on Yahoo bars. With 13 cash / bond funds added: 0.786 / 0.629 / 0.521 (t -1.45) | ships as har-gk-3; HAR beats trailing on operating-company history. Missed shells: CIFR, LCID, RGTI, HIMS, IONQ (pinned share < 75% or < 63 pinned sessions; thresholds not tuned on QLIKE). Cash funds in March 2020 (forecast 0.2-0.4%, realised 11-25%) are genuine and dominate a QLIKE that includes them |
| 2026-11-27 | FMP-shaped defects planted in those Yahoo bars (unadjusted MARA 1:4 x2, TPL 3:1, NVDA 4:1 with adjusted volume, NVDA adj-only 10:1, 167 missing ONDS / MARA sessions) | all rules: 0.419 / 0.452 / 0.319 vs 0.418 / 0.451 / 0.318 on the clean bars. TPL repaired, NVDA adj-only repaired, NVDA 2021 and MARA 2017 ambiguous (missing), MARA 2019-04-08 missed (an 11% genuine gap on the split day puts it outside ratio_tol) | the rules neutralise planted defects; a split on a day with a big genuine gap is not caught |
| 2026-11-27 | `quant-audit` on the server with har-gk-3 (FMP bars, 270 tickers, 476k ticker-days) | QLIKE: trailing 0.408, long-run mean 0.442, HAR pooled 0.306 (NW t -3.45, wins on 97% of tickers), per-ticker 0.325 (t -2.79). RMSE vol 0.259 / 0.268 / 0.224 / 0.220. Per-ticker median 0.333 / 0.350 / 0.258 / 0.264. Scale 1.07. Rules: shell 2,431 bars in 11 tickers (NAMS, QBTS, LUNR, AUR, ACHR, JOBY, ASTS, DKNG, NVTS, SMR, USAR); no-trade 1,045 bars; 1,504 missing sessions in 3 tickers; splits 0 repaired, 7 ratio-sized moves left as genuine (BMNR, QXO), 9 special dividends. Worst left: CIFR (Jul-Aug 2021) and RGTI (Feb 2022), shells the rule misses | confirmed on the server's own bars: `better_significant`. The loss is no longer concentrated (75+ tickers hold half of it) |

Caveat: every sample is names listed today (survivorship).

## Traps

- **Vendors restate history:** a split or dividend re-scales past OHLC / adj_close. `bars.restated` compares
  the re-read overlap and re-fetches the ticker whole. Never mix bars fetched before and after a split.
- **Today's bar is live until the close:** stored only after 16:30 ET (`BARS.final_after_et`).
- **Yahoo option quotes are ~15 min delayed, the quote's spot is not:** the ATM IV uses the put-call parity
  forward from the option quotes themselves, so the spot mismatch does not bias it.
- **Yahoo chains are empty before ~09:46 ET and after hours:** an expiry without any bid is not stored.
  The chain job is skipped on early-close days (15:40 is after the 13:00 close).
- **Yahoo answers 429 from some IPs** (a cloud container, 2026-11-16): options then fail for every ticker.
- **No-trade bars are not zero variance.** O = H = L = C with volume 0 or a repeated close (pre-listing rows,
  SPAC shells, halts) used to floor at 1e-8 and drag the trailing forecast to ~0, which QLIKE punishes by orders of
  magnitude. Since har-gk-2 they are missing (`ESTIMATOR.no_trade_missing`).
- **A missing adj_close is not a factor of 1.0:** an old row's adj / close is far below 1 after years of
  dividends; that day's overnight return now skips the dividend correction instead.
- **"Split-ratio moves" are mostly genuine:** the server audit's QXO 2020-08-04 / 2024-07-30, SMMT, LUNR, MARA
  2017-06-21 and APLD 2022-07-19 show the same close-to-close moves in Yahoo's split-adjusted bars, and QXO /
  RIOT / WULF's factor jumps are paid special distributions in Yahoo's events. The split rule therefore works on
  the overnight return and demands evidence; the audit flags such moves `ratio_move`, not `unadjusted_split`.
  FMP-only and unresolved: TPL's adj-only jump of 0.141 on 2020-12-10 (Yahoo: 0.015, a $10 dividend) is
  dividend-sized, so it stays; BMNR cannot be checked (Yahoo keeps only ~350 sessions).
- **Trusts are not 2% vol all along:** bid-ask bounce on a ~$10 shell gives the estimator 5-20% while the price
  sits within ±1%; only the final months reach ~2%. The band, not the vol, defines a shell.
- **A version bump restarts the own-history reference** (it is per `MODEL_VERSION`): readings use the
  cross-section until 60 new readings exist.
- **Yahoo's history can be broken for one name:** ROKU returned 14 rows for any range (2026-11-27); SQ is XYZ
  since 2025 (404); ADYEN has no US listing (ADYEY); APLS returned nothing.
