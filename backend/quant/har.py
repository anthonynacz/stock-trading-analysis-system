"""HAR regression on log realised variance (pure numpy, no DB).

Model, per sample t of one ticker's daily variance series v:
    log RV(t+1 .. t+H) = b0 + b_d log v_t + b_w log mean(v_{t-4..t}) + b_m log mean(v_{t-20..t}) + e
with RV the mean daily variance of the next H sessions (H = 21). A forecast is exp(fitted + s2/2), the mean of a
log-normal with the regression's residual variance s2, because QLIKE is minimised by the conditional mean.

Sample t needs its whole target window: when fitting "as of" session index T, only samples with t + H <= T may
enter (`usable_at`). That rule is what keeps the walk-forward (quant/evaluate.py) free of look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_VALID_SHARE = 0.8    # a lag or target window needs this share of valid days


def rolling_mean(v: np.ndarray, k: int, min_share: float = MIN_VALID_SHARE) -> np.ndarray:
    """Mean of v[t-k+1 .. t] ignoring NaN, NaN when fewer than min_share * k values are valid or t < k - 1."""
    v = np.asarray(v, dtype=np.float64)
    n = len(v)
    out = np.full(n, np.nan)
    if n < k:
        return out
    ok = np.isfinite(v)
    s = np.concatenate([[0.0], np.cumsum(np.where(ok, v, 0.0))])
    cnt = np.concatenate([[0], np.cumsum(ok.astype(np.int64))])
    tot = s[k:] - s[:-k]
    num = cnt[k:] - cnt[:-k]
    with np.errstate(invalid="ignore", divide="ignore"):
        m = tot / num
    m[num < max(1, int(np.ceil(min_share * k)))] = np.nan
    out[k - 1:] = m
    return out


def forward_mean(v: np.ndarray, h: int, min_share: float = MIN_VALID_SHARE) -> np.ndarray:
    """Mean of v[t+1 .. t+h] (the target), NaN when the window runs past the data or is too sparse."""
    v = np.asarray(v, dtype=np.float64)
    n = len(v)
    out = np.full(n, np.nan)
    back = rolling_mean(v, h, min_share)     # back[j] = mean(v[j-h+1 .. j]) -> forward at t = back[t+h]
    if n > h:
        out[: n - h] = back[h:]
    return out


def features(v: np.ndarray, lags: tuple[int, ...] = (1, 5, 21), floor: float = 1e-8) -> np.ndarray:
    """(n, len(lags)) log of the trailing means; NaN rows where a window is not complete enough."""
    v = np.asarray(v, dtype=np.float64)
    cols = []
    for k in lags:
        m = v.copy() if k == 1 else rolling_mean(v, k)
        cols.append(np.log(np.maximum(m, floor)))
    X = np.column_stack(cols) if cols else np.zeros((len(v), 0))
    X[~np.isfinite(X)] = np.nan
    return X


def target(v: np.ndarray, h: int = 21, floor: float = 1e-8) -> np.ndarray:
    """log mean variance of the next h sessions (NaN where unknown)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.log(np.maximum(forward_mean(v, h), floor))
    y[~np.isfinite(y)] = np.nan
    return y


def usable_at(n: int, T: int, h: int) -> np.ndarray:
    """Boolean mask of samples 0..n-1 whose target window (t+1 .. t+h) is observed by session index T."""
    return np.arange(n) + h <= T


@dataclass
class HarFit:
    coef: np.ndarray                 # [b0, b_d, b_w, b_m]
    s2: float                        # residual variance of the log regression
    n: int                           # samples used
    r2: float = float("nan")
    extra: dict = field(default_factory=dict)

    def predict_log(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        return self.coef[0] + X @ self.coef[1:]

    def predict_var(self, X: np.ndarray) -> np.ndarray:
        """Forecast of the mean daily variance over the horizon (level, bias-corrected)."""
        return np.exp(self.predict_log(X) + 0.5 * self.s2)

    def to_json(self) -> dict:
        return {"coef": [round(float(c), 6) for c in self.coef], "s2": round(float(self.s2), 6), "n": int(self.n),
                "r2": None if not np.isfinite(self.r2) else round(float(self.r2), 4), **self.extra}


def fit(X: np.ndarray, y: np.ndarray) -> HarFit | None:
    """OLS with intercept on the complete rows; None with fewer rows than 5x the parameters."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    Xo, yo = X[ok], y[ok]
    p = X.shape[1] + 1
    if len(yo) < 5 * p:
        return None
    A = np.column_stack([np.ones(len(yo)), Xo])
    coef, *_ = np.linalg.lstsq(A, yo, rcond=None)
    resid = yo - A @ coef
    s2 = float(resid @ resid / max(len(yo) - p, 1))
    tss = float(((yo - yo.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / tss if tss > 0 else float("nan")
    return HarFit(coef=coef, s2=s2, n=int(len(yo)), r2=r2)


def stack(series: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-ticker (X, y) pairs into one pooled design."""
    if not series:
        return np.zeros((0, 3)), np.zeros(0)
    return np.vstack([s[0] for s in series]), np.concatenate([s[1] for s in series])
