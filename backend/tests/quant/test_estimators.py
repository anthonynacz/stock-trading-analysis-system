"""The daily variance estimator (quant/estimators.py) on synthetic bars with known variance."""
from __future__ import annotations

import math

import numpy as np

from quant.estimators import (
    annualise,
    close_to_close_returns,
    daily_variance,
    garman_klass,
    overnight_returns,
    parkinson,
)
from tests.quant.conftest import synth_ohlc


def test_gk_plus_overnight_scale_and_efficiency():
    rng = np.random.default_rng(1)
    sig = np.full(4000, 0.02)                       # 2% daily, 20% of it overnight
    b = synth_ohlc(sig, rng, steps=390)
    v = daily_variance(b["open"], b["high"], b["low"], b["close"], b["adj_close"])[1:]
    r2 = close_to_close_returns(b["close"])[1:] ** 2
    true = 0.02 ** 2
    # Discrete sampling of the high / low biases range estimators down (390 steps, 80% intraday: ~7%).
    assert 0.88 < v.mean() / true < 1.0
    assert abs(r2.mean() / true - 1) < 0.08
    # Efficiency: the range-based estimator is much less noisy than one squared return.
    assert v.var() < 0.5 * r2.var()


def test_gk_without_overnight_misses_the_gap():
    rng = np.random.default_rng(2)
    sig = np.full(3000, 0.02)
    b = synth_ohlc(sig, rng, steps=390, on_share=0.4)
    gk = garman_klass(b["open"], b["high"], b["low"], b["close"])[1:]
    assert gk.mean() / 0.02 ** 2 < 0.7               # ~60% of the variance is intraday
    assert abs(parkinson(b["high"], b["low"])[1:].mean() / gk.mean() - 1) < 0.1


def test_gk_handles_open_or_close_outside_range_and_invalid_bars():
    o = np.array([10.0, 10.0, np.nan, 10.0, 0.0])
    h = np.array([11.0, 10.5, 11.0, 10.0, 11.0])
    l = np.array([9.0, 9.9, 9.0, 10.0, 9.0])
    c = np.array([10.5, 10.8, 10.0, 10.0, 10.0])     # row 1: close above the high
    gk = garman_klass(o, h, l, c)
    assert np.all(gk[[0, 1, 3]] >= 0)
    assert gk[3] == 0.0
    assert np.isnan(gk[2]) and np.isnan(gk[4])
    v = daily_variance(o, h, l, c, floor=1e-8)
    assert v[3] >= 1e-8 and np.isnan(v[2])


def test_overnight_adds_back_the_dividend():
    # Day 1 is the ex-date of a $1 dividend: the stock opens $1 lower with no news.
    c = np.array([100.0, 99.0])
    o = np.array([100.0, 99.0])
    adj = np.array([99.0, 99.0])                     # vendor's dividend-adjusted closes
    assert abs(overnight_returns(o, c)[1] - math.log(0.99)) < 1e-12        # unadjusted: a fake -1% gap
    assert abs(overnight_returns(o, c, adj)[1]) < 1e-12                    # adjusted: no move
    assert np.isnan(overnight_returns(o, c, adj)[0])


def test_annualise():
    assert abs(annualise(0.0004) - 0.02 * math.sqrt(252)) < 1e-12
    assert np.isnan(annualise(np.array([np.nan]))[0])
