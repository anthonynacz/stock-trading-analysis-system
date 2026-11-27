"""Every tunable of the quant layer, as commented constants.

Changing a model constant (HAR, ESTIMATOR) means a new MODEL_VERSION and a fresh `python -m quant.evaluate`
run, so the registry (quant_model_runs) shows what was tried. Reading thresholds are display rules.
"""
from __future__ import annotations

# ---------------------------------------------------------------- tickers

# Always tracked, whatever the universe holds: the market, size, and sector reference ETFs. config.SECTOR_ETFS
# (the industry analyzer's ETFs) is added at run time.
CORE_ETFS = ("SPY", "QQQ", "IWM", "DIA", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV",
             "XLY", "SMH", "SOXX", "ITA")
RADAR_RECENT_DAYS = 10       # names with a radar event this recent get daily bars (not chains: too many requests)

# ---------------------------------------------------------------- daily bars

BARS = {
    "backfill_years": 10,        # first fetch per ticker; FMP's plan may return less (5 years is the floor we need)
    "overlap_days": 10,          # incremental fetch re-reads this many calendar days before the last stored bar
    # A re-read bar whose close or adjustment factor (adj_close / close) moved this much (relative) means a split
    # or a dividend re-scaled the vendor's history: the ticker's whole history is fetched again.
    "restate_tol": 1e-4,
    "final_after_et": "16:30",   # today's bar is stored only after this ET time (before, it is a live, partial row)
    "max_rps": 3.0,              # FMP requests per second (production shares the key: keep <= 3)
    "timeout_s": 20,
}

# ---------------------------------------------------------------- option chain snapshots

CHAINS = {
    "max_dte": 120,              # expiries stored out to this many calendar days
    "strike_band": 0.30,         # strikes within +/- this share of spot
    # At most this many expiries per ticker (one Yahoo request each): the two that bracket 30 days always,
    # then the expiry nearest each target below. Weeklies would otherwise mean ~15 requests per ticker.
    "target_dtes": (7, 14, 21, 45, 60, 90, 120),
    "max_expiries": 8,
    "max_rps": 4.0,              # Yahoo requests per second (the radar's option refresh shares the IP)
    "workers": 4,
    "timeout_s": 12,
    "min_iv": 0.05,              # a contract IV below this is Yahoo's placeholder for "unquoted" (radar.options.MIN_IV)
    "max_iv": 5.0,               # above 500% is a broken quote
    "max_tickers": 400,          # hard cap on one run's ticker list
}

# ---------------------------------------------------------------- implied volatility

IV = {
    "target_days": 30,           # constant-maturity horizon (calendar days)
    "min_dte": 3,                # an expiry closer than this is too noisy to interpolate from
    "max_rel_spread": 0.50,      # an ATM quote with (ask - bid) / mid above this is not used
    "max_extrapolate_days": 10,  # without a bracketing pair, the nearest expiry is used when within this many days
    "fallback_max_age_hours": 30,  # radar metrics / options_snapshots older than this are not used
}

# ---------------------------------------------------------------- volatility model

MODEL_NAME = "har_logrv"
# bump on any change to ESTIMATOR or HAR. gk-2: no-trade bars missing, adj fix. gk-3: missing sessions, split
# defects and shell history (the three rules below, quant/cleaning.py).
MODEL_VERSION = "har-gk-3"
TRADING_DAYS = 252
ESTIMATOR = {
    # Daily variance = squared overnight log return (dividend-corrected) + Garman-Klass open-to-close variance.
    "kind": "gk_overnight",
    "floor": 1e-8,               # daily variance floor before logs (a zero-range day; ~0.16% annualised)
    # Data-cleaning rules. Each acts in quant/cleaning.variance_series, which the evaluation, the forecast job and
    # the audit all call, so every model sees the same input; the scorecard's `cleaning` block counts each one.
    # Thresholds were written down from the defect's definition before their effect on the scorecard was seen.
    #
    # (har-gk-2) A no-trade bar (O = H = L = C with volume 0, or with the same close as the session before;
    # estimators.no_trade_bars) is a vendor repeating a price, not a session with zero variance. Its variance is
    # missing (NaN), so lag / target windows with too few real sessions (har.MIN_VALID_SHARE) drop out for every
    # model alike. Seen in real Yahoo bars of SPAC shells and pre-listing rows (302 of 441 zero-range bars in 180
    # tickers, 2026-11-27).
    "no_trade_missing": True,
    # (har-gk-3) Missing sessions. Defect: a ticker without bars for days or months (BMNR misses 1,059 sessions,
    # ONDS 440 in the FMP bars) made the next bar's "overnight" return a multi-session move and let a 21-row window
    # span months. Each ticker is put on the panel's session calendar (dates that at least `ref_share` of the
    # tickers trading then have; SPY-free, holiday-free); a missing session is a NaN row, so a window needs
    # har.MIN_VALID_SHARE real sessions, and the bar after a missing or invalid one has no known overnight
    # return: its variance is missing too (not Garman-Klass alone, which would understate it).
    "gaps": {"ref_share": 0.5},
    # (har-gk-3) Split defects: a split the vendor did not adjust shows as one overnight return of -ln k (a k:1
    # split) or +ln k (1:k reverse). Only that day's overnight return is wrong (Garman-Klass is within one bar),
    # so the repair is to that number alone. Seen in the FMP bars of BMNR, QXO, SMMT, LUNR, MARA, APLD, and
    # adj_close / OHLC disagreeing around QXO, RIOT, WULF, TPL (2026-11-27 audit). Per day t, m = ln(O_t/C_{t-1})
    # raw, df = the log change of adj_close / close:
    # - |df| > factor_jump with prices continuous (|m|, |close move| <= tol): adj_close jumped, the prices did
    #   not: the jump is not a dividend; the raw overnight return is used (repaired, `adj_only`).
    # - |df| > factor_jump and the open carries it (|m + df| <= tol, range of the day < |df| - tol): a split
    #   or special dividend adjusted in adj_close only; the dividend correction is already right (`adj_confirmed`,
    #   counted, unchanged).
    # - |df| > factor_jump otherwise (the bar mixes units, or the two series disagree): ambiguous.
    # - |df| <= factor_jump and |m| within ratio_tol of ln k (k in `ratios`): both series moved by a split
    #   ratio. Repaired (m - sign(m) ln k) only when all of: the level persists (median close of the `window`
    #   sessions after vs before moved by m within persist_tol: not a bad print that reverts), the split day and
    #   the day before each have a range under calm_range (not a news day), and share volume moved the other way
    #   by at least half of ln k (split shares; needs volume on both sides). Otherwise ambiguous.
    # Ambiguous days: the day's variance is missing (no invented correction), counted and listed.
    "splits": {"ratios": (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100), "ratio_tol": 0.03,
               "factor_jump": 0.10, "tol": 0.05, "window": 10, "persist_tol": 0.15, "calm_range": 0.15},
    # (har-gk-3) Shell history. Defect: before a SPAC merger the ticker's bars are a blank-check trust's (cash in
    # trust, price pinned near $10, ~2% vol), a different security from the operating company that later trades
    # under the ticker (NAMS, QBTS: 55% of the trailing baseline's total loss on the server, 2026-11-27). A session
    # is "pinned" when its trailing `window` sessions have every close within +/- band of their median and a
    # realised vol (this estimator, annualised) under vol_line (or fewer than a quarter of them trade at all).
    # The rule cuts a ticker's history through its last pinned session L only when all of: the first pinned
    # session's window starts within start_within sessions of the first bar (start of life only: a pinned regime
    # later in life, e.g. a cash takeover, is the same company and its end is genuine volatility), at least
    # pinned_share of the sessions from there to L are pinned (one regime, not a bond fund drifting in and out),
    # and at least min_after sessions follow L (it left the regime for good; bond / cash funds and a shell still
    # waiting stay whole). The HAR lag window (21 sessions, MIN_VALID_SHARE) is the burn-in after L.
    # Live: a ticker pinned in its last `window` sessions gets the flag `pinned_price` and the reading
    # "not comparable" (a trust, a pending deal or a cash fund: the risk is the regime ending, which the options
    # price and the forecast cannot).
    "shell": {"window": 21, "band": 0.03, "vol_line": 0.05, "start_within": 63, "pinned_share": 0.75,
              "min_after": 126},
}
HAR = {
    "horizon": 21,               # target: mean daily variance over the next 21 sessions
    "lags": (1, 5, 21),          # daily / weekly / monthly averages of the daily variance
    # Pooled (one regression across tickers) or per-ticker. Decided by measurement (quant/CLAUDE.md): pooled had
    # the lower out-of-sample QLIKE overall on 2026-11-16 (per-ticker was better in calm years, broke down in 2020).
    "pooled": True,
    "min_obs_ticker": 500,       # per-ticker fit needs this many complete samples, else pooled is used
    "min_bars_forecast": 63,     # a ticker with fewer valid bars gets no forecast
}
EVAL = {
    "min_train_days": 504,       # first out-of-sample date: two years of samples behind it
    "refit_every": 21,           # walk-forward refit cadence (sessions)
    "long_run_window": 0,        # 0: the long-run-mean baseline uses all history up to t (expanding)
    "nw_lag": 21,                # Newey-West lag for the loss-difference t statistic (targets overlap by 20 days)
}
# Verdict of the best HAR variant against trailing_21d: "better_significant" needs a lower QLIKE AND a Newey-West
# t below -VERDICT_T (about a 5% two-sided test); a lower QLIKE with t above it is "lower_not_significant".
VERDICT_T = 2.0
BREAKDOWN = {                    # per-ticker breakdown in the scorecard (kept small: it is recorded as JSON)
    "top_tickers": 15,           # worst tickers listed, by the baseline's total loss
    "top_days": 10,              # worst baseline ticker-days listed (half as many per HAR variant)
    "trim": 0.05,                # trimmed mean of the per-ticker QLIKE drops this share at each end
}

# ---------------------------------------------------------------- bars audit (python -m quant.audit, read-only)

# Thresholds of the audit's checks. They only decide what the audit reports; nothing here changes the model.
AUDIT = {
    "price_tol": 1e-6,           # relative slack before an open / close counts as outside [low, high]
    "outside_gross": 0.01,       # ... and this far outside (1%) counts as a gross violation
    "flat_run_min": 5,           # this many identical closes in a row is a stale run
    "big_return": 0.40,          # |close-to-close log return| above this (a 33% fall / 49% rise) is listed
    "factor_jump": 0.10,         # |log change| of adj_close / close above this is not a dividend: split-sized
    "move_match": 0.05,          # a split shows in a price when its log move is within this of the factor jump
    "near_zero_vol": 0.02,       # a trailing 21-session vol below 2% annualised can blow up QLIKE (see quant/CLAUDE.md)
    "gap_share": 0.02,           # missing sessions (vs SPY's dates) above this share of the ticker's span
    "scale_band": (0.6, 1.6),    # the ticker's estimator / close-to-close variance outside this band
    "top_overnight": 3,          # largest squared overnight returns listed per flagged ticker
    "max_listed": 60,            # flagged tickers printed (all are in --json)
}

# ---------------------------------------------------------------- reading (display only)

# Implied vol normally sits above later realised vol (the variance risk premium), so a reading compares the
# IV / forecast ratio with a reference distribution of the same ratio, never IV with the forecast directly.
READING = {
    "rich_pctl": 0.80,           # ratio at or above the 80th percentile of its reference: options rich
    "cheap_pctl": 0.20,          # at or below the 20th: options cheap; in between: fair
    "own_min_obs": 60,           # the ticker's own history is the reference once it has this many readings
    "own_window_days": 365,      # ... over this many calendar days
    "xs_min_names": 30,          # else today's cross-section, when at least this many comparable names
}

# ---------------------------------------------------------------- scheduling (services/quant_runner.py)

JOB_TIMEOUT_S = {"bars": 1800, "chains": 1500, "forecast": 900, "evaluate": 1800}
EVALUATION_MAX_AGE_DAYS = 30     # the close job re-scores the model when the last evaluation is older, or for another version
