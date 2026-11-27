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
  `daily_variance` (so every model and the forecast job see the same input), are counted in the scorecard's
  `cleaning` block, and bump `MODEL_VERSION`. Never choose one by how much it helps HAR.
- **No look-ahead:** a training sample needs its whole 21-session target closed by the fit date
  (`har.usable_at`). `tests/quant/test_walk_forward.py` fails if that rule is removed.
- **The reading is relative.** IV normally exceeds later realised vol (variance risk premium); compare
  IV / forecast with the ticker's own earlier ratios (60+), else today's cross-section of 30-day-IV names
  (30+), else "insufficient history". Earnings inside the horizon: "not comparable".
- **IV source is always recorded** (`iv_source`): `chain_cm30` > `radar_iv30` > `options_snapshot_atm`
  (the call nearest the money across expiries, not a 30-day value; flagged `iv_not_30d`). The cross-section reference uses only the first two.

## What was measured

| When | Question | Result | Decision |
|---|---|---|---|
| 2026-11-16 | HAR vs trailing 21d RV, walk-forward (Yahoo daily bars, 56 large US stocks + ETFs, 2018-11 .. 2026-10, 111k ticker-days) | QLIKE: trailing 0.373, long-run mean 0.460, HAR pooled 0.286 (NW t -3.6), HAR per-ticker 0.304. RMSE vol: 0.149 / 0.143 / 0.128 / 0.126 | HAR beats trailing; ships |
| 2026-11-16 | Pooled vs per-ticker by year | per-ticker lower QLIKE in every year but 2020 (1.26 vs pooled 0.95); pooled beats trailing every year (ratio 0.70-0.86) | pooled (`HAR.pooled`): robust to a regime break |
| 2026-11-16 | Estimator scale vs close-to-close variance | 1.016 on real bars (synthetic 390-step paths: 0.93, the discrete-sampling bias) | no correction; the reading is relative |
| 2026-11-27 | Same evaluation on the server's `quant_daily_bars` (FMP, 270 tickers, 481k ticker-days, 2018-11 .. 2026-10, har-gk-1) | QLIKE: trailing 16.21, long-run mean 0.60, HAR pooled 2.62 (NW t -1.1), per-ticker 2.63. RMSE vol: 0.324 / 0.319 / 0.283 / 0.285. Scale 1.13 | not confirmed (verdict now `lower_not_significant`, long-run mean lower). Cause unknown: run `quant-audit` and read the breakdown. A mean of 16 needs ~120 ticker-days with the trailing forecast at the 1e-8 floor (QLIKE ~6e4 each against a 40% vol target), i.e. a 21+-session run of flat bars; real quiet-then-explosive names (SPAC shells) give at most ~650 |
| 2026-11-27 | Reproduce on Yahoo bars, 180 tickers (universe + ETFs + split / IPO / SPAC / foreign / thin names), 332k ticker-days | har-gk-1: trailing 0.396, long-run 0.482, HAR pooled 0.308 (t -3.8). har-gk-2: 0.390 / 0.473 / 0.303 (t -3.7), RMSE vol 0.226 / 0.234 / 0.199, scale 1.04; the rule removed 304 bars in 9 tickers (726 OOS ticker-days). Worst days: LCID (CCIV shell, Jan 2021) and RGTI, genuine | blow-up not reproduced. Adding small caps alone lifts RMSE (0.149 → 0.226), so RMSE needs no bad data; the scale only reached 1.04 (per-ticker up to 1.29 for TLN, 1.19 GME / GRAB), so 1.13 is not explained, nor is the QLIKE level |

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
- **Yahoo's history can be broken for one name:** ROKU returned 14 rows for any range (2026-11-27); SQ is XYZ
  since 2025 (404); ADYEN has no US listing (ADYEY); APLS returned nothing.
