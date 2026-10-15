"""Unit tests for trend_detector (synthetic sessions). Run: python -m pytest test_trend_detector.py -q"""
import math

import numpy as np
import pytest

from radar.trend import (EXIT, RECOMMENDED, THRESHOLDS, TrendEntry, trend_exit_reason, trend_features,  
                            trend_flags, update_trend_list)

N = 78
TH = THRESHOLDS[RECOMMENDED]
SIGD = 0.02


def session(paths, vols=None, spy=0):
    """Grid arrays from close paths (rows = symbols). Each bar opens at the previous close; high/low wrap
    open/close by a hair. The first bar opens at paths[:, 0] / (1 + first-bar move)."""
    c = np.asarray(paths, dtype=float)
    S, n = c.shape
    o = np.empty_like(c)
    o[:, 1:] = c[:, :-1]
    o[:, 0] = c[:, 0]
    h = np.maximum(o, c) * 1.0002
    l = np.minimum(o, c) * 0.9998
    v = np.full((S, n), 1000.0) if vols is None else np.asarray(vols, dtype=float)
    return o, h, l, c, v


def baselines(S, cvm_per_bar=1000.0, n=N):
    cvm = np.cumsum(np.full((S, n), cvm_per_bar), axis=1)
    return dict(beta=np.ones(S), sigd=np.full(S, SIGD), pc=np.full(S, 100.0), cvm=cvm)


def trend_path(total=0.05, n=N, start=100.0, noise=0.0, seed=0):
    """Steady log-linear path from `start` gaining `total` (log) over the first 24 bars, flat after."""
    rng = np.random.default_rng(seed)
    steps = np.r_[np.full(24, total / 24), np.zeros(n - 24)] + noise * rng.standard_normal(n)
    return start * np.exp(np.cumsum(steps))


def run(paths, k, vol_mult=1.5, spy=0, base=None):
    o, h, l, c, v = session(paths)
    v = v * vol_mult
    v[spy] = 1000.0
    b = base or baselines(len(paths))
    f = trend_features(o, h, l, c, v, k=k, spy=spy, beta=b["beta"], sigd=b["sigd"], pc=b["pc"],
                       cvm_k=b["cvm"][:, k], slope_bars=TH["slope_bars"])
    return f, trend_flags(f, k, TH)


FLAT = np.full(N, 100.0)


def test_steady_uptrend_is_flagged_up():
    f, flags = run([FLAT, trend_path(0.05)], k=20)
    assert flags[1] and not flags[0]
    assert f["dir"][1] == 1
    assert f["zopen"][1] > TH["zopen"] and f["eff"][1] == pytest.approx(1.0, abs=1e-6)
    assert f["vwap_share"][1] == 1.0 and f["since_ext"][1] == 0


def test_steady_downtrend_is_flagged_down():
    f, flags = run([FLAT, trend_path(-0.05)], k=20)
    assert flags[1] and f["dir"][1] == -1


def test_market_move_alone_is_not_a_trend():
    up = trend_path(0.05)
    f, flags = run([up, up.copy()], k=20)          # the stock only follows SPY (beta 1)
    assert not flags[1]
    assert abs(f["zopen"][1]) < 1e-9


def test_no_flag_outside_the_window():
    _, flags = run([FLAT, trend_path(0.08)], k=TH["first_slot"] - 1)
    assert not flags.any()
    path = 100.0 * np.exp(np.cumsum(np.full(N, 0.08 / 60)))         # still trending after the last slot
    _, flags = run([FLAT, path], k=TH["last_slot"] + 1)
    assert not flags.any()
    _, flags = run([FLAT, path], k=TH["last_slot"])
    assert flags[1]


def test_low_volume_blocks():
    _, flags = run([FLAT, trend_path(0.05)], k=20, vol_mult=1.0)   # rvolc = 1.0 < 1.2
    assert not flags[1]


def test_choppy_path_fails_efficiency():
    x = np.zeros(N)
    x[1:21] = np.where(np.arange(20) % 2 == 0, 0.03, -0.0225)      # big swings, same small drift
    path = 100.0 * np.exp(np.cumsum(x))
    f, flags = run([FLAT, path], k=20)
    assert f["eff"][1] < TH["eff"]
    assert not flags[1]


def test_small_move_fails_size():
    _, flags = run([FLAT, trend_path(0.01)], k=20)                  # 0.5 sigma_d
    assert not flags[1]


def test_stale_high_fails_still_going():
    # trend over the first 12 bars, then flat-to-slightly-down for 10 bars: no new high in the last 6
    steps = np.r_[np.full(12, 0.005), np.full(10, -0.0002), np.zeros(N - 22)]
    path = 100.0 * np.exp(np.cumsum(steps))
    f, flags = run([FLAT, path], k=21)
    assert f["since_ext"][1] >= TH["ext_bars"]
    assert not flags[1]


def test_no_lookahead():
    a = trend_path(0.05, noise=0.002, seed=1)
    b = a.copy()
    b[25:] *= 0.9                                                    # change the future only
    fa, _ = run([FLAT, a], k=24)
    fb, _ = run([FLAT, b], k=24)
    for key in fa:
        np.testing.assert_allclose(fa[key], fb[key], equal_nan=True)


def test_exit_after_vwap_bars_closes_below_vwap():
    n = EXIT["vwap_bars"]
    e = TrendEntry(dir=1, flag_slot=10, flag_price=105.0, best=0.05)
    for _ in range(n - 1):
        assert trend_exit_reason(e, 0.045, -1.0) is None
    assert e.wrong_side_run == n - 1
    assert trend_exit_reason(e, 0.045, 1.0) is None and e.wrong_side_run == 0   # back above VWAP resets
    for _ in range(n - 1):
        assert trend_exit_reason(e, 0.045, -1.0) is None
    assert trend_exit_reason(e, 0.045, -1.0) == "VWAP"


def test_default_exit_ignores_giveback():
    e = TrendEntry(dir=1, flag_slot=10, flag_price=105.0, best=0.06)
    assert trend_exit_reason(e, 0.001, 1.0) is None                  # still above VWAP: stays listed


def test_exit_giveback_half_when_enabled():
    ex = {"vwap_bars": 4, "giveback": 0.5}
    e = TrendEntry(dir=1, flag_slot=10, flag_price=105.0, best=0.04)
    assert trend_exit_reason(e, 0.06, 1.0, ex) is None and e.best == pytest.approx(0.06)
    assert trend_exit_reason(e, 0.031, 1.0, ex) is None
    assert trend_exit_reason(e, 0.029, 1.0, ex) == "GIVEBACK"


def test_exit_down_direction():
    ex = {"vwap_bars": 2, "giveback": 0.5}
    e = TrendEntry(dir=-1, flag_slot=10, flag_price=95.0, best=0.05)
    assert trend_exit_reason(e, -0.05, -1.0, ex) is None             # below VWAP is the trend side
    assert trend_exit_reason(e, -0.05, 1.0, ex) is None and e.wrong_side_run == 1
    assert trend_exit_reason(e, -0.02, 1.0, ex) == "VWAP"
    e2 = TrendEntry(dir=-1, flag_slot=10, flag_price=95.0, best=0.05)
    assert trend_exit_reason(e2, -0.02, -1.0, ex) == "GIVEBACK"


def test_list_enters_once_and_never_reenters():
    paths = [FLAT, trend_path(0.05)]
    o, h, l, c, v = session(paths)
    v[1] *= 1.5
    b = baselines(2)
    listed: dict = {}
    entered_at = []
    for k in range(N):
        if k == 40:                                                  # crash below VWAP for good
            c[1, 40:] = 90.0
            o[1, 41:], h[1, 40:], l[1, 40:] = 90.0, np.maximum(o[1, 40:], 90.0) * 1.0002, 89.98
        f = trend_features(o, h, l, c, v, k=k, spy=0, beta=b["beta"], sigd=b["sigd"], pc=b["pc"], cvm_k=b["cvm"][:, k])
        new = update_trend_list(listed, ["SPY", "TRND"], f, k, trend_flags(f, k, TH), c[:, k], np.array([False, True]))
        entered_at += [(s, k) for s in new]
    assert [s for s, _ in entered_at] == ["TRND"]
    e = listed["TRND"]
    # first slot where the move since the open (k bars of 0.05/24) and since the prior close (k + 1 bars) reach
    # the zopen and zday thresholds
    per_bar = 0.05 / 24 / SIGD
    expect = max(TH["first_slot"], math.ceil(TH["zopen"] / per_bar - 1e-9), math.ceil(TH["zday"] / per_bar - 1 - 1e-9))
    assert e.dir == 1 and e.flag_slot == expect
    assert e.exit_slot == 40 + EXIT["vwap_bars"] - 1 and e.exit_reason == "VWAP"


def test_thresholds_are_complete():
    need = {"first_slot", "last_slot", "zopen", "zday", "vwap_share", "eff", "slope_bars", "slope_z", "ext_bars", "rvolc"}
    for th in THRESHOLDS.values():
        assert need <= set(th)
    assert set(EXIT) == {"vwap_bars", "giveback"}
    assert math.isfinite(TH["zopen"])
