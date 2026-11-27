"""The relative reading (quant/reading.py) and the earnings horizon (quant/forecast.py)."""
from __future__ import annotations

from datetime import date

from quant import reading as rd
from quant.forecast import earnings_in_window, horizon_end

CFG = {"rich_pctl": 0.8, "cheap_pctl": 0.2, "own_min_obs": 60, "own_window_days": 365, "xs_min_names": 30}


def test_percentile_rank():
    assert rd.percentile_rank(5, [1, 2, 3, 4]) == 1.0
    assert rd.percentile_rank(0, [1, 2, 3, 4]) == 0.0
    assert rd.percentile_rank(2, [1, 2, 3]) == 0.5
    assert rd.percentile_rank(1, []) is None


def test_own_history_takes_precedence_and_ratio_above_one_can_be_cheap():
    own = [1.4 + 0.01 * i for i in range(60)]        # this name's IV usually runs 1.4-2.0x the forecast
    out = rd.classify(1.3, own_ref=own, xs_ref=[1.0] * 50, earnings_in_horizon=False, cfg=CFG)
    assert out[:2] == ("cheap", "own_history")       # IV above the forecast, still cheap for this name
    assert rd.classify(2.5, own_ref=own, xs_ref=[], earnings_in_horizon=False, cfg=CFG)[0] == "rich"
    assert rd.classify(1.7, own_ref=own, xs_ref=[], earnings_in_horizon=False, cfg=CFG)[0] == "fair"


def test_cross_section_then_insufficient():
    xs = [1.0 + 0.01 * i for i in range(40)]
    assert rd.classify(1.5, own_ref=[1.2] * 10, xs_ref=xs, earnings_in_horizon=False, cfg=CFG)[:2] == \
        ("rich", "cross_section")
    assert rd.classify(1.2, own_ref=[], xs_ref=xs[:10], earnings_in_horizon=False, cfg=CFG) == \
        ("insufficient history", "none", None)


def test_earnings_and_missing_iv():
    assert rd.classify(1.2, own_ref=[1.0] * 100, xs_ref=[], earnings_in_horizon=True, cfg=CFG) == \
        ("not comparable", "earnings_in_horizon", None)
    assert rd.classify(None, own_ref=[], xs_ref=[], earnings_in_horizon=False, cfg=CFG)[0] == "no implied vol"
    assert rd.classify(float("nan"), own_ref=[], xs_ref=[], earnings_in_horizon=False, cfg=CFG)[0] == "no implied vol"


def test_horizon_end_and_earnings_window():
    as_of = date(2026, 11, 13)                       # Friday; Thanksgiving 11-26 is not a session
    end = horizon_end(as_of, 21, 30)
    assert end == date(2026, 12, 15)                 # 21st session after 11-13
    assert earnings_in_window(as_of, end, [(date(2026, 12, 1), "AMC")]) == date(2026, 12, 1)
    assert earnings_in_window(as_of, end, [(date(2027, 1, 20), None)]) is None
    assert earnings_in_window(as_of, end, [(as_of, "BMO")]) is None          # already out at the open
    assert earnings_in_window(as_of, end, [(as_of, "AMC")]) == as_of          # after the close: tomorrow's move
    assert earnings_in_window(as_of, end, [(date(2026, 11, 1), "AMC")]) is None


def test_pinned_price_is_not_comparable():
    own = [1.0 + 0.01 * i for i in range(100)]
    assert rd.classify(1.3, own_ref=own, xs_ref=[], earnings_in_horizon=False, pinned=True, cfg=CFG) == \
        (rd.NOT_COMPARABLE, rd.BASIS_PINNED, None)
    assert rd.classify(None, own_ref=own, xs_ref=[], earnings_in_horizon=False, pinned=True, cfg=CFG)[0] == rd.NO_IV
