"""HAR regression (quant/har.py): window helpers, coefficient recovery, forecasts on persistent volatility."""
from __future__ import annotations

import numpy as np

from quant import har
from quant.estimators import daily_variance
from tests.quant.conftest import stochastic_vol, synth_ohlc


def test_rolling_and_forward_means():
    v = np.arange(1.0, 11.0)                         # 1..10
    rm = har.rolling_mean(v, 3)
    assert np.isnan(rm[:2]).all() and rm[2] == 2.0 and rm[-1] == 9.0
    fm = har.forward_mean(v, 3)
    assert fm[0] == 3.0                              # mean(2, 3, 4)
    assert fm[6] == 9.0 and np.isnan(fm[7:]).all()   # needs t+1..t+3 inside the data
    v2 = v.copy()
    v2[[3, 4]] = np.nan                              # 2 of 3 missing: below the 80% share
    assert np.isnan(har.rolling_mean(v2, 3)[4])


def test_usable_at_never_admits_an_open_target():
    m = har.usable_at(100, T=50, h=21)
    assert m[29] and not m[30]                       # 29 + 21 = 50 <= T, 30 + 21 > T


def test_fit_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(5000, 3))
    beta = np.array([-0.5, 0.3, 0.4, 0.2])
    y = beta[0] + X @ beta[1:] + rng.normal(0, 0.1, 5000)
    f = har.fit(X, y)
    assert np.allclose(f.coef, beta, atol=0.01)
    assert abs(f.s2 - 0.01) < 0.002
    # The level forecast carries the log-normal correction exp(s2 / 2).
    assert np.isclose(f.predict_var(X[:1])[0], np.exp(f.predict_log(X[:1])[0] + f.s2 / 2))


def test_fit_refuses_too_few_rows():
    assert har.fit(np.ones((10, 3)), np.ones(10)) is None


def test_har_beats_trailing_on_mean_reverting_volatility():
    rng = np.random.default_rng(3)
    sig = stochastic_vol(3000, rng)
    b = synth_ohlc(sig, rng, steps=60)
    v = daily_variance(b["open"], b["high"], b["low"], b["close"])
    X, y = har.features(v), har.target(v)
    rv = har.forward_mean(v, 21)
    trailing = har.rolling_mean(v, 21)
    train = slice(0, 1500)
    f = har.fit(X[train], y[train])
    # Persistent but mean-reverting: the lag coefficients are positive and sum below 1.
    assert 0.5 < f.coef[1:].sum() < 1.0
    test = np.arange(1600, 2970)
    ok = np.isfinite(rv[test]) & np.isfinite(trailing[test])
    test = test[ok]
    fc = f.predict_var(X[test])

    def qlike(r, h):
        return np.mean(r / h - np.log(r / h) - 1)
    assert qlike(rv[test], fc) < qlike(rv[test], trailing[test])
