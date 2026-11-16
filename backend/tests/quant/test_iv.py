"""Implied vol (quant/iv.py): Black-76 inversion, parity forward, ATM IV per expiry, 30-day interpolation."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from quant import iv

R = 0.045


def _chain(F: float, T: float, sigma_of_k, strikes, *, half_spread: float = 0.02) -> dict:
    cols = {k: [] for k in ("strike", "side", "bid", "ask", "last", "iv", "volume", "oi")}
    for k in strikes:
        for side, call in (("C", True), ("P", False)):
            p = iv.black76(F, k, T, R, sigma_of_k(k), call)
            cols["strike"].append(k)
            cols["side"].append(side)
            cols["bid"].append(round(max(p - half_spread, 0.01), 4))
            cols["ask"].append(round(p + half_spread, 4))
            cols["last"].append(p)
            cols["iv"].append(0.99)              # deliberately wrong: the parity path must not use it
            cols["volume"].append(10)
            cols["oi"].append(100)
    return cols


@pytest.mark.parametrize("call", [True, False])
def test_black76_inversion_round_trip(call):
    for sigma in (0.08, 0.3, 1.2):
        p = iv.black76(100.0, 105.0, 0.1, R, sigma, call)
        assert iv.implied_vol(p, 100.0, 105.0, 0.1, R, call) == pytest.approx(sigma, abs=1e-5)
    assert iv.implied_vol(0.0, 100, 100, 0.1, R, True) is None
    assert iv.implied_vol(1000.0, 100, 100, 0.1, R, True) is None        # above the no-arbitrage bound


def test_atm_iv_recovers_sigma_and_the_dividend_forward():
    spot, T = 100.0, 30 / 365
    F = 99.2                                         # forward below spot: a dividend inside the expiry
    out = iv.expiry_atm_iv(_chain(F, T, lambda k: 0.25, range(80, 121)), spot, T, R)
    assert out["method"] == "parity_b76"
    assert out["forward"] == pytest.approx(F, abs=0.05)
    assert out["iv"] == pytest.approx(0.25, abs=0.004)


def test_atm_iv_interpolates_the_smile_to_the_forward():
    T, F = 45 / 365, 100.5
    out = iv.expiry_atm_iv(_chain(F, T, lambda k: 0.30 - 0.004 * (k - 100), range(90, 111), half_spread=0.0001),
                           100.0, T, R)
    assert out["iv"] == pytest.approx(0.30 - 0.004 * 0.5, abs=0.002)


def test_atm_iv_falls_back_to_yahoo_iv_without_two_sided_quotes():
    cols = {"strike": [95.0, 100.0, 105.0], "side": ["C", "C", "C"], "bid": [None, None, None],
            "ask": [6.0, 2.5, 0.8], "last": [None] * 3, "iv": [0.4, 0.33, None], "volume": [None] * 3, "oi": [1] * 3}
    out = iv.expiry_atm_iv(cols, 101.0, 0.1, R)
    assert out == {"iv": 0.33, "forward": None, "method": "yahoo_iv"}
    assert iv.expiry_atm_iv({**cols, "iv": [None] * 3}, 101.0, 0.1, R) is None


def test_constant_maturity_is_linear_in_total_variance():
    pts = [(20 / 365, 0.30), (40 / 365, 0.40)]
    out = iv.constant_maturity(pts)
    w = 0.09 * 20 / 365 + (0.16 * 40 / 365 - 0.09 * 20 / 365) * 0.5
    assert out["method"] == "interpolated"
    assert out["iv"] == pytest.approx(math.sqrt(w / (30 / 365)), abs=1e-9)
    assert out["t_days"] == [20.0, 40.0]
    # An expiry under min_dte is ignored; a lone expiry 5 days off is used flat; one 20 days off is not.
    assert iv.constant_maturity([(1 / 365, 0.9), (35 / 365, 0.35)])["method"] == "nearest"
    assert iv.constant_maturity([(50 / 365, 0.35)]) is None
    assert iv.constant_maturity([(30 / 365, 0.31), (60 / 365, 0.5)])["iv"] == pytest.approx(0.31)


def test_year_fraction_counts_to_the_expiry_close():
    snap = datetime(2026, 11, 16, 20, 40, tzinfo=timezone.utc)        # 15:40 ET
    assert iv.year_fraction(snap, date(2026, 11, 16)) * 365 * 24 == pytest.approx(20 / 60, abs=1e-6)
    assert iv.year_fraction(snap, date(2026, 11, 13)) == 0.0


def test_cm30_from_stored_rows():
    snap = datetime(2026, 11, 16, 20, 40, tzinfo=timezone.utc)
    rows = [{"snapshot_at": snap, "expiry": date(2026, 12, 4), "atm_iv": 0.30},
            {"snapshot_at": snap, "expiry": date(2026, 12, 24), "atm_iv": 0.40},
            {"snapshot_at": snap, "expiry": date(2027, 1, 15), "atm_iv": None}]
    out = iv.cm30_from_snapshots(rows, R)
    assert out["method"] == "interpolated" and 0.30 < out["iv"] < 0.40
