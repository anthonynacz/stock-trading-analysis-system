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
- `evaluate.py` → scorecard printed, recorded as kind `evaluation`.
- Jobs: `python -m quant.run bars|chains|forecast`, scheduled by `services/quant_runner.py`.

## Rules

- **Evaluate before shipping a model change.** Bump `MODEL_VERSION`, run `python -m quant.evaluate`, compare
  with the last evaluation row. Ship only what beats `trailing_21d` out of sample; say so when it does not.
  Keep the candidate set declared in `evaluate.MODELS`; do not tune against the out-of-sample window.
- **No look-ahead:** a training sample needs its whole 21-session target closed by the fit date
  (`har.usable_at`). `tests/quant/test_walk_forward.py` fails if that rule is removed.
- **The reading is relative.** IV normally exceeds later realised vol (variance risk premium); compare
  IV / forecast with the ticker's own earlier ratios (60+), else today's cross-section of 30-day-IV names
  (30+), else "insufficient history". Earnings inside the horizon: "not comparable".
- **IV source is always recorded** (`iv_source`): `chain_cm30` > `radar_iv30` > `options_snapshot_atm`
  (nearest expiry, flagged `iv_not_30d`). The cross-section reference uses only the first two.

## What was measured

| When | Question | Result | Decision |
|---|---|---|---|
| 2026-11-16 | HAR vs trailing 21d RV, walk-forward (Yahoo daily bars, 56 large US stocks + ETFs, 2018-11 .. 2026-10, 111k ticker-days) | QLIKE: trailing 0.373, long-run mean 0.460, HAR pooled 0.286 (NW t -3.6), HAR per-ticker 0.304. RMSE vol: 0.149 / 0.143 / 0.128 / 0.126 | HAR beats trailing; ships |
| 2026-11-16 | Pooled vs per-ticker by year | per-ticker lower QLIKE in every year but 2020 (1.26 vs pooled 0.95); pooled beats trailing every year (ratio 0.70-0.86) | pooled (`HAR.pooled`): robust to a regime break |
| 2026-11-16 | Estimator scale vs close-to-close variance | 1.016 on real bars (synthetic 390-step paths: 0.93, the discrete-sampling bias) | no correction; the reading is relative |

Caveat: the 56 names are today's large caps (survivorship). Re-run on `quant_daily_bars` after the backfill.

## Traps

- **Vendors restate history:** a split or dividend re-scales past OHLC / adj_close. `bars.restated` compares
  the re-read overlap and re-fetches the ticker whole. Never mix bars fetched before and after a split.
- **Today's bar is live until the close:** stored only after 16:30 ET (`BARS.final_after_et`).
- **Yahoo option quotes are ~15 min delayed, the quote's spot is not:** the ATM IV uses the put-call parity
  forward from the option quotes themselves, so the spot mismatch does not bias it.
- **Yahoo chains are empty before ~09:46 ET and after hours:** an expiry without any bid is not stored.
  The chain job is skipped on early-close days (15:40 is after the 13:00 close).
- **Yahoo answers 429 from some IPs** (a cloud container, 2026-11-16): options then fail for every ticker.
