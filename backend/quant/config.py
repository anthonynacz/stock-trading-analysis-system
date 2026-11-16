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
MODEL_VERSION = "har-gk-1"       # bump on any change to ESTIMATOR or HAR
TRADING_DAYS = 252
ESTIMATOR = {
    # Daily variance = squared overnight log return (dividend-corrected) + Garman-Klass open-to-close variance.
    "kind": "gk_overnight",
    "floor": 1e-8,               # daily variance floor before logs (a zero-range day; ~0.16% annualised)
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
