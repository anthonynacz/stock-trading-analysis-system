"""Walk-forward evaluation (quant/evaluate.py): no look-ahead, losses, scorecard."""
from __future__ import annotations

from datetime import date

import numpy as np
from quant import evaluate, har
from tests.quant.conftest import panel_rows, stochastic_vol, synth_ohlc, weekdays

EV = {"min_train_days": 300, "refit_every": 21, "long_run_window": 0, "nw_lag": 21}
CFG = {"horizon": 21, "lags": (1, 5, 21), "pooled": True, "min_obs_ticker": 200, "min_bars_forecast": 63}


def _rows(n_tickers: int = 4, n: int = 900, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    dates = weekdays(date(2020, 1, 1), n)
    return {f"T{i}": panel_rows(dates, synth_ohlc(stochastic_vol(n, rng), rng, steps=30)) for i in range(n_tickers)}


def _forecasts_by_key(res: dict) -> dict:
    return {(t, str(d)): (res["har_pooled"][i], res["har_ticker"][i], res["trailing_21d"][i], res["long_run_mean"][i])
            for i, (t, d) in enumerate(zip(res["ticker"], res["date"]))}


def test_no_look_ahead_future_data_does_not_change_past_forecasts():
    rows = _rows()
    base = evaluate.walk_forward(evaluate.panel_from_rows(rows, CFG), CFG, EV)
    cut = np.datetime64(rows["T0"]["date"][650])
    # Blow up every bar after the cut: 10x the ranges and gaps.
    shocked = {}
    for t, r in rows.items():
        r2 = {k: list(v) for k, v in r.items()}
        for i, d in enumerate(r2["date"]):
            if np.datetime64(d) > cut:
                c = r2["close"][i]
                r2["high"][i], r2["low"][i] = c * 1.3, c * 0.7
                r2["open"][i] = c * 1.2
        shocked[t] = r2
    after = evaluate.walk_forward(evaluate.panel_from_rows(shocked, CFG), CFG, EV)
    a, b = _forecasts_by_key(base), _forecasts_by_key(after)
    past = [k for k in a if np.datetime64(k[1]) <= cut and k in b]
    assert len(past) > 500
    for k in past:
        assert np.allclose(a[k], b[k], rtol=0, atol=0), k
    # ... and the test is sensitive: later forecasts did change.
    later = [k for k in a if np.datetime64(k[1]) > cut and k in b]
    assert any(not np.allclose(a[k], b[k]) for k in later)


def test_the_no_look_ahead_test_would_catch_a_leak(monkeypatch):
    """With the target-window rule removed (training on samples whose 21-day target is still open), the same
    perturbation changes forecasts made before it: the check above would fail."""
    rows = _rows()
    cut = np.datetime64(rows["T0"]["date"][650])
    monkeypatch.setattr(har, "usable_at", lambda n, T, h: np.arange(n) <= T)    # the leak: targets not closed
    base = _forecasts_by_key(evaluate.walk_forward(evaluate.panel_from_rows(rows, CFG), CFG, EV))
    shocked = {}
    for t, r in rows.items():
        r2 = {k: list(v) for k, v in r.items()}
        for i, d in enumerate(r2["date"]):
            if np.datetime64(d) > cut:
                c = r2["close"][i]
                r2["high"][i], r2["low"][i], r2["open"][i] = c * 1.3, c * 0.7, c * 1.2
        shocked[t] = r2
    after = _forecasts_by_key(evaluate.walk_forward(evaluate.panel_from_rows(shocked, CFG), CFG, EV))
    past = [k for k in base if np.datetime64(k[1]) <= cut and k in after]
    assert any(not np.allclose(base[k], after[k]) for k in past)


def test_forecast_uses_only_the_refit_before_it():
    res = evaluate.walk_forward(evaluate.panel_from_rows(_rows(n_tickers=2), CFG), CFG, EV)
    refits = [np.datetime64(r["refit"]) for r in res["refits"]]
    assert refits == sorted(refits)
    first_oos = min(np.datetime64(d) for d in res["date"])
    assert first_oos >= refits[0]


def test_losses_and_newey_west():
    rv = np.array([1.0, 2.0, 4.0])
    assert np.allclose(evaluate.qlike(rv, rv), 0.0)
    assert (evaluate.qlike(rv, rv * 2) > 0).all() and (evaluate.qlike(rv, rv / 2) > 0).all()
    assert np.allclose(evaluate.vol_sq_err(np.array([0.0001]), np.array([0.0001])), 0.0)
    rng = np.random.default_rng(0)
    assert abs(evaluate.newey_west_t(rng.normal(0, 1, 2000), 5)) < 3
    assert evaluate.newey_west_t(rng.normal(-1, 1, 500), 5) < -10


def test_run_scorecard_shape_on_synthetic_data():
    card = evaluate.run(_rows(n_tickers=3, n=800), CFG, EV)
    assert set(card["models"]) == set(evaluate.MODELS)
    for m, e in card["models"].items():
        assert e["qlike"] > 0 and e["rmse_vol"] > 0
    assert card["n"] > 0 and card["tickers"] == 3
    assert card["best_har"] in ("har_pooled", "har_ticker")
    # 30 intraday steps: the sampled high / low miss much of the range (scale well under 1).
    assert 0.5 < card["estimator_to_close_close"] < 1.0
    text = evaluate.format_scorecard(card)
    assert "trailing_21d" in text and "against trailing" in text and "per-ticker QLIKE" in text
    assert card["har_verdict"] in ("better_significant", "lower_not_significant", "not_better")
    assert card["har_beats_trailing"] == (card["har_verdict"] == "better_significant")


def test_csv_round_trip(tmp_path):
    p = tmp_path / "bars.csv"
    p.write_text("ticker,date,open,high,low,close,adj_close\n"
                 "aaa,2024-01-03,1,2,0.5,1.5,1.4\naaa,2024-01-02,1,2,0.5,1.2,\n")
    rows = evaluate.rows_from_csv(str(p))
    assert rows["AAA"]["date"] == ["2024-01-02", "2024-01-03"]
    assert np.isnan(rows["AAA"]["adj_close"][0]) and rows["AAA"]["close"] == [1.2, 1.5]
