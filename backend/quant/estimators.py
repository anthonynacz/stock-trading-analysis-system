"""Daily variance estimators on OHLC arrays (pure numpy, no DB).

The model's estimator is `daily_variance`: the squared overnight log return plus the Garman-Klass
open-to-close variance. Why this one:
- Garman-Klass uses open, high, low and close, about 7x as efficient as one squared close-to-close return for
  a driftless random walk; Parkinson (high/low only) is about 5x and ignores where the day opened and closed.
- Both assume trading is continuous, so on their own they miss the overnight gap, which carries a large share
  of a stock's variance (earnings, news). Adding the squared overnight return puts it back, unbiased.
- Yang-Zhang combines the same pieces but is a multi-day window estimator; HAR wants one number per day.
- Rogers-Satchell is drift-robust, but daily drift is negligible next to daily variance.
Known biases (measured in quant/CLAUDE.md, not corrected): the observed high/low misses the true extremes
(downward) and trade-price noise widens them (upward). The reading is relative (quant/reading.py), so a
constant scale factor cancels.
"""
from __future__ import annotations

import math

import numpy as np

GK_C = 2.0 * math.log(2.0) - 1.0


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)


def valid_bars(o, h, l, c) -> np.ndarray:
    """Rows that can be used: all four prices finite and positive."""
    o, h, l, c = map(_arr, (o, h, l, c))
    with np.errstate(invalid="ignore"):
        return np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0) & (h > 0) & (l > 0) & (c > 0)


def garman_klass(o, h, l, c) -> np.ndarray:
    """Open-to-close Garman-Klass variance per day: 0.5 ln(H/L)^2 - (2 ln2 - 1) ln(C/O)^2.

    Vendor bars sometimes print an open or close outside the high-low range; the range is widened to hold
    them, which keeps the estimate >= 0. NaN where a bar is invalid."""
    o, h, l, c = map(_arr, (o, h, l, c))
    ok = valid_bars(o, h, l, c)
    out = np.full(o.shape, np.nan)
    hi = np.maximum.reduce([h[ok], o[ok], c[ok]])
    lo = np.minimum.reduce([l[ok], o[ok], c[ok]])
    hl = np.log(hi / lo)
    co = np.log(c[ok] / o[ok])
    out[ok] = 0.5 * hl * hl - GK_C * co * co
    return out


def parkinson(h, l) -> np.ndarray:
    """ln(H/L)^2 / (4 ln 2) per day (reference estimator for the tests and the bias check)."""
    h, l = _arr(h), _arr(l)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.log(h / l)
    return x * x / (4.0 * math.log(2.0))


def adjustment_factor(c, adj_c) -> np.ndarray:
    """adj_close / close: changes only across ex-dividend dates (both series are split-adjusted)."""
    c = _arr(c)
    if adj_c is None:
        return np.ones_like(c)
    a = _arr(adj_c)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = a / c
    return np.where(np.isfinite(f) & (f > 0), f, 1.0)


def overnight_returns(o, c, adj_c=None) -> np.ndarray:
    """ln(O_t / C_{t-1}) + ln(f_t / f_{t-1}), f = adj_close / close: the dividend paid at the open of an ex-date
    is added back, so it is not counted as a price move. NaN for the first row and next to invalid prices."""
    o, c = _arr(o), _arr(c)
    f = adjustment_factor(c, adj_c)
    out = np.full(o.shape, np.nan)
    if len(o) < 2:
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.log(o[1:] / c[:-1]) + np.log(f[1:] / f[:-1])
    out[1:] = np.where(np.isfinite(r), r, np.nan)
    return out


def close_to_close_returns(c, adj_c=None) -> np.ndarray:
    """Dividend-adjusted daily log returns (NaN for the first row)."""
    series = _arr(adj_c) if adj_c is not None else _arr(c)
    out = np.full(series.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[1:] = np.log(series[1:] / series[:-1])
    out[~np.isfinite(out)] = np.nan
    return out


def daily_variance(o, h, l, c, adj_c=None, floor: float = 1e-8) -> np.ndarray:
    """The model's daily variance: overnight^2 + Garman-Klass, floored at `floor`. NaN where the bar is invalid;
    the first row (no previous close) is the Garman-Klass part alone."""
    gk = garman_klass(o, h, l, c)
    on = overnight_returns(o, c, adj_c)
    v = gk + np.where(np.isfinite(on), on * on, 0.0)
    v = np.where(np.isfinite(gk), np.maximum(v, floor), np.nan)
    return v


def annualise(var_daily, trading_days: int = 252):
    """Daily variance -> annualised volatility (same shape; NaN stays NaN)."""
    v = np.asarray(var_daily, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        out = np.sqrt(v * trading_days)
    return float(out) if out.ndim == 0 else out
