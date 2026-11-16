"""The forecast job end to end on SQLite (quant/forecast.py + quant/store.py): IV sources, earnings, readings."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
from sqlalchemy import func, insert, select

from db.models import (
    EarningsCalendar,
    OptionsSnapshot,
    QuantModelRun,
    QuantVolForecast,
    RadarOptionMetric,
    Sector,
    UniverseStock,
)
from quant import forecast
from quant.config import MODEL_VERSION
from tests.quant.conftest import bars_rows, stochastic_vol, synth_ohlc, weekdays

AS_OF = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 22, 10, tzinfo=timezone.utc)
SNAP = datetime(2026, 11, 16, 20, 40, tzinfo=timezone.utc)
N_CHAIN = 32


def _seed(store, n_tickers: int = 36, n_days: int = 400):
    rng = np.random.default_rng(11)
    days = [d for d in weekdays(date(2025, 1, 1), 2000) if d <= AS_OF][-n_days:]
    tickers = [f"T{i:02d}" for i in range(n_tickers)]
    for t in tickers:
        store.upsert_bars(bars_rows(t, days, synth_ohlc(stochastic_vol(n_days, rng), rng, steps=30)))
    store.upsert_bars(bars_rows("OLD", days[:-1], synth_ohlc(stochastic_vol(n_days - 1, rng), rng, steps=30)))
    chain_rows = []
    for i, t in enumerate(tickers[:N_CHAIN]):
        for exp, iv in ((date(2026, 12, 11), 0.30 + 0.01 * i), (date(2026, 12, 24), 0.32 + 0.01 * i)):
            chain_rows.append({"ticker": t, "snapshot_date": AS_OF, "expiry": exp, "snapshot_at": SNAP,
                               "underlying_price": 100.0, "dte": (exp - AS_OF).days, "n_contracts": 2, "atm_iv": iv,
                               "forward": 100.0, "iv_method": "parity_b76",
                               "earnings_date": date(2026, 12, 3) if t == "T03" else None,
                               "contracts": {"strike": [100.0, 100.0], "side": ["C", "P"]}})
    store.upsert_chain_rows(chain_rows)
    with store.engine.begin() as conn:
        conn.execute(insert(Sector.__table__).values(id=1, name="AI/Semiconductors", max_stocks=12))
        conn.execute(insert(UniverseStock.__table__), [
            {"ticker": t, "sector_id": 1, "source": "SEED", "is_active": True} for t in tickers])
        conn.execute(insert(EarningsCalendar.__table__).values(ticker="T01", earnings_date=date(2026, 11, 20),
                                                               earnings_time="AMC"))
        conn.execute(insert(RadarOptionMetric.__table__).values(
            ticker=tickers[N_CHAIN], as_of=NOW - timedelta(hours=2), metrics={"iv_30d_pct": 41.5}))
        conn.execute(insert(OptionsSnapshot.__table__).values(
            ticker=tickers[N_CHAIN + 1], snapshot_time=NOW - timedelta(hours=1), atm_iv=0.5))
    return tickers


def test_forecast_rows_sources_earnings_and_readings(store):
    tickers = _seed(store)
    res = forecast.run(store, now=NOW)
    assert res["status"] == "ok" and res["as_of"] == AS_OF.isoformat()
    assert res["stale_tickers"] == ["OLD"]
    assert res["iv_sources"] == {"chain_cm30": N_CHAIN, "radar_iv30": 1, "options_snapshot_atm": 1, "none": 2}
    as_of, version, rows = store.latest_forecasts()
    assert as_of == AS_OF and version == MODEL_VERSION and len(rows) == len(tickers)
    by = {r["ticker"]: r for r in rows}
    for r in rows:
        assert 0.05 < r["forecast_vol"] < 2.0 and r["realized_vol_21d"] > 0 and r["horizon"] == 21
    # Calendar earnings (AMC on 11-20) and the Yahoo date stored with the chain (12-03) both fall in the horizon.
    assert by["T01"]["earnings_in_horizon"] and by["T01"]["reading"] == "not comparable"
    assert by["T03"]["earnings_date"] == date(2026, 12, 3) and by["T03"]["reading_basis"] == "earnings_in_horizon"
    # 30 earnings-free chain names: the cross-section is the reference.
    t0 = by["T00"]
    assert t0["reading_basis"] == "cross_section" and t0["reading"] in ("rich", "fair", "cheap")
    assert t0["iv_to_forecast"] == round(t0["implied_vol_30d"] / t0["forecast_vol"], 4)
    assert t0["iv_detail"]["method"] == "interpolated"
    radar = by[tickers[N_CHAIN]]
    assert radar["iv_source"] == "radar_iv30" and radar["implied_vol_30d"] == 0.415 and "iv_fallback" in radar["flags"]
    snap = by[tickers[N_CHAIN + 1]]
    assert snap["iv_source"] == "options_snapshot_atm" and "iv_not_30d" in snap["flags"]
    assert by[tickers[-1]]["reading"] == "no implied vol"
    assert "earnings_unknown" in by[tickers[-1]]["flags"]
    readings = [by[t]["reading"] for t in tickers[:N_CHAIN] if t not in ("T01", "T03")]
    assert {"rich", "cheap"} <= set(readings)


def test_forecast_is_idempotent_and_records_each_fit(store):
    _seed(store)
    forecast.run(store, now=NOW)
    forecast.run(store, now=NOW)
    with store.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(QuantVolForecast)).scalar() == 36
        kinds = conn.execute(select(QuantModelRun.kind)).scalars().all()
    assert kinds == ["fit", "fit"]


def test_own_history_becomes_the_reference(store):
    _seed(store)
    hist = [{"ticker": "T05", "as_of_date": AS_OF - timedelta(days=i + 1), "model_version": MODEL_VERSION,
             "horizon": 21, "forecast_vol": 0.3, "iv_to_forecast": 5.0 + 0.01 * i, "reading": "fair",
             "earnings_in_horizon": False} for i in range(60)]
    store.upsert_forecasts(hist)
    forecast.run(store, now=NOW)
    _, _, rows = store.latest_forecasts()
    t5 = next(r for r in rows if r["ticker"] == "T05")
    assert t5["reading_basis"] == "own_history" and t5["reading"] == "cheap" and t5["ratio_pctl"] == 0.0
    assert len(store.forecast_history("T05")) == 61


def test_no_bars_is_an_error(store):
    assert forecast.run(store, now=NOW)["status"] == "error"
