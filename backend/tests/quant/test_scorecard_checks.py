"""Scorecard instruments (quant/evaluate.py): per-ticker breakdown, three-state verdict, and the cleaning rules of
the estimator (no-trade bars missing, a missing adj_close is not a dividend jump)."""
from __future__ import annotations

from datetime import date

import numpy as np
from quant import estimators, evaluate, forecast
from quant.config import ESTIMATOR
from tests.quant.conftest import panel_rows, stochastic_vol, synth_ohlc, weekdays

EV = {"min_train_days": 300, "refit_every": 21, "long_run_window": 0, "nw_lag": 21}
CFG = {"horizon": 21, "lags": (1, 5, 21), "pooled": True, "min_obs_ticker": 200, "min_bars_forecast": 63}


# ---------------------------------------------------------------- estimator fixes

def test_missing_adj_close_is_not_a_dividend_jump():
    o = np.array([100.0, 101.0, 102.0, 103.0])
    c = np.array([100.5, 101.5, 102.5, 103.5])
    adj = c * 0.6                                   # ten years of dividends: factor 0.6
    adj[2] = np.nan                                 # one row without adj_close
    on = estimators.overnight_returns(o, c, adj)
    plain = np.log(o[1:] / c[:-1])
    assert np.allclose(on[1:], plain)               # no ln(0.6) jump next to the missing row
    # a real ex-dividend factor change is still corrected
    adj2 = c * 0.6
    adj2[3] = c[3] * 0.61
    on2 = estimators.overnight_returns(o, c, adj2)
    assert np.isclose(on2[3], plain[2] + np.log(0.61 / 0.6))


def test_no_trade_bars():
    o = np.array([10.0, 10.0, 10.0, 10.0, 10.2])
    h = np.array([10.5, 10.0, 10.0, 10.1, 10.3])
    l = np.array([9.9, 10.0, 10.0, 10.0, 10.1])
    c = np.array([10.0, 10.0, 10.0, 10.05, 10.2])
    # day 1: flat and same close as day 0; day 2: flat, same close; others have a range
    assert estimators.no_trade_bars(o, h, l, c).tolist() == [False, True, True, False, False]
    # a flat bar at a new price with volume > 0 is one real trade, kept; with volume 0 it is not
    o2, c2 = np.array([10.0, 10.3]), np.array([10.0, 10.3])
    assert estimators.no_trade_bars(o2, o2, o2, c2, [100, 50]).tolist() == [False, False]
    assert estimators.no_trade_bars(o2, o2, o2, c2, [100, 0]).tolist() == [False, True]
    v = estimators.daily_variance(o, h, l, c, None, 1e-8, no_trade_missing=True)
    assert np.isnan(v[1]) and np.isnan(v[2]) and np.isfinite(v[3])
    v0 = estimators.daily_variance(o, h, l, c, None, 1e-8)
    assert v0[1] == 1e-8                            # without the rule: floored zero variance


def _rows(n_tickers: int = 3, n: int = 800, seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    dates = weekdays(date(2020, 1, 1), n)
    out = {}
    for i in range(n_tickers):
        r = panel_rows(dates, synth_ohlc(stochastic_vol(n, rng), rng, steps=30))
        r["volume"] = [1000] * n
        out[f"T{i}"] = r
    return out


def _plant_stale(r: dict, start: int, length: int) -> None:
    c = r["close"][start - 1]
    for i in range(start, start + length):
        r["open"][i] = r["high"][i] = r["low"][i] = r["close"][i] = r["adj_close"][i] = c
        r["volume"][i] = 0


def test_no_trade_rule_applies_to_every_model_and_is_reported():
    rows = _rows()
    _plant_stale(rows["T0"], 500, 30)
    on = {**ESTIMATOR, "no_trade_missing": True}
    off = {**ESTIMATOR, "no_trade_missing": False}
    p_on = evaluate.panel_from_rows(rows, CFG, on)
    p_off = evaluate.panel_from_rows(rows, CFG, off)
    res_on = evaluate.walk_forward(p_on, CFG, EV)
    res_off = evaluate.walk_forward(p_off, CFG, EV)
    # Without the rule the trailing forecast right after the stale run sits on the variance floor.
    k = res_off["ticker"] == "T0"
    assert res_off["trailing_21d"][k].min() < 1e-7
    # With it those ticker-days are gone for every model at once (one shared row set), and nothing is floored.
    k = res_on["ticker"] == "T0"
    assert res_on["trailing_21d"][k].min() > 1e-6
    assert len(res_on["rv"]) < len(res_off["rv"])
    for m in evaluate.MODELS:
        assert len(res_on[m]) == len(res_on["rv"]) and np.all(np.isfinite(res_on[m]))
    rep = evaluate.cleaning_report(rows, p_on, CFG, on, EV)
    assert rep["rules"] == ["no_trade_missing"] and rep["bars_removed"] == 30 and rep["tickers_affected"] == 1
    assert rep["oos_ticker_days_removed"] == len(res_off["rv"]) - len(res_on["rv"])
    # The forecast job builds its panel with the same function and the same ESTIMATOR.
    assert forecast.panel_from_rows is evaluate.panel_from_rows


def test_cleaning_rule_is_on_in_the_shipped_config():
    assert ESTIMATOR["no_trade_missing"] is True


# ---------------------------------------------------------------- per-ticker breakdown

def _res():
    rng = np.random.default_rng(0)
    tick, dates, rv, f = [], [], [], []
    for t in ("A", "B", "C", "D"):
        for d in range(50):
            tick.append(t)
            dates.append(np.datetime64("2024-01-01") + np.timedelta64(d, "D"))
            x = 4e-4 * np.exp(rng.normal(0, 0.3))
            rv.append(x)
            f.append(x * np.exp(rng.normal(0, 0.2)))
    f = np.array(f)
    rv = np.array(rv)
    bad = 7                                          # one near-zero forecast for ticker A
    trailing = f.copy()
    trailing[bad] = 1e-8
    return {"ticker": np.array(tick), "date": np.array(dates), "rv": rv, "trailing_21d": trailing,
            "long_run_mean": np.full(len(f), 4e-4), "har_pooled": f, "har_ticker": f * 1.01}


def test_breakdown_finds_the_ticker_and_the_day():
    res = _res()
    bd = evaluate.breakdown(res, top=3, top_days=4)
    assert bd["worst_tickers"][0]["ticker"] == "A"
    assert bd["worst_tickers"][0]["trailing_21d_share"] > 0.9
    tr = bd["models"]["trailing_21d"]
    assert tr["tickers_for_50pct"] == 1 and tr["tickers_for_90pct"] == 1
    assert tr["ticker_median"] < bd["worst_tickers"][0]["trailing_21d"]
    worst = bd["worst_days"][0]
    assert worst["model"] == "trailing_21d" and worst["ticker"] == "A" and worst["forecast_var"] == 1e-8
    assert worst["target_var"] == float(f"{res['rv'][7]:.4g}") and worst["qlike"] > 1000
    assert len([d for d in bd["worst_days"] if d["model"] == "trailing_21d"]) == 4
    assert len([d for d in bd["worst_days"] if d["model"] == "har_pooled"]) == 2
    assert len(bd["worst_tickers"]) == 3
    # small enough to record
    import json
    assert len(json.dumps(bd, default=str)) < 6000


def test_scorecard_carries_breakdown_verdict_and_cleaning():
    card = evaluate.run(_rows(), CFG, EV)
    assert {"breakdown", "cleaning", "har_verdict", "har_lower_qlike", "long_run_beats_har"} <= set(card)
    assert card["cleaning"]["bars_removed"] == 0
    text = evaluate.format_scorecard(card)
    assert "worst tickers" in text and "worst ticker-days" in text and "cleaning (no_trade_missing)" in text


# ---------------------------------------------------------------- verdict

def _card(har_q, t, lr_q=0.9, base_q=0.5):
    return {"models": {"trailing_21d": {"qlike": base_q}, "long_run_mean": {"qlike": lr_q},
                       "har_pooled": {"qlike": har_q, "nw_t_vs_trailing": t},
                       "har_ticker": {"qlike": har_q + 0.01, "nw_t_vs_trailing": t}}}


def test_verdict_three_states():
    v = evaluate.verdict(_card(0.4, -3.6))
    assert v["har_verdict"] == "better_significant" and v["har_beats_trailing"] is True
    assert v["best_har"] == "har_pooled" and v["long_run_beats_har"] is False
    v = evaluate.verdict(_card(0.4, -1.14))
    assert v["har_verdict"] == "lower_not_significant" and v["har_beats_trailing"] is False and v["har_lower_qlike"]
    v = evaluate.verdict(_card(0.4, None))
    assert v["har_verdict"] == "lower_not_significant"
    v = evaluate.verdict(_card(0.4, float("nan")))
    assert v["har_verdict"] == "lower_not_significant"
    v = evaluate.verdict(_card(0.6, -5.0))
    assert v["har_verdict"] == "not_better" and v["har_lower_qlike"] is False and v["har_beats_trailing"] is False
    assert evaluate.verdict({"models": {"trailing_21d": {"qlike": 1}}})["har_verdict"] is None


def test_verdict_reports_a_naive_baseline_beating_the_model():
    # The 2026-11-27 production numbers: HAR lower than trailing, not significant, long-run mean lower still.
    v = evaluate.verdict(_card(2.62, -1.14, lr_q=0.60, base_q=16.21))
    assert v["har_verdict"] == "lower_not_significant" and v["long_run_beats_har"] is True
    text = evaluate.format_scorecard({"models": {}, **v})
    assert "NOT ESTABLISHED" in text and "long-run mean" in text and "YES" not in text
