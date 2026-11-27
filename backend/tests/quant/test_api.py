"""Quant routes (api/quant_routes.py) through FastAPI's TestClient on a SQLite store, and the job runner."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert

from api import quant_routes
from auth import get_current_user
from db.models import PipelineRunLog
from quant.config import MODEL_VERSION
from services import quant_runner

ROW_KEYS = {"ticker", "sector", "price", "forecast_vol", "realized_vol_21d", "implied_vol_30d", "iv_source",
            "iv_to_forecast", "ratio_pctl", "reading", "reading_basis", "earnings_in_horizon", "earnings_date", "flags"}


def _client(store, role="ADMIN"):
    app = FastAPI()
    app.include_router(quant_routes.router)
    app.dependency_overrides[quant_routes.get_quant_store] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role=role, email="t@example.com")
    return TestClient(app)


def _forecast(ticker, d, ratio=1.2):
    return {"ticker": ticker, "as_of_date": d, "model_version": MODEL_VERSION, "horizon": 21, "price": 100.0,
            "forecast_vol": 0.25, "realized_vol_21d": 0.22, "implied_vol_30d": 0.25 * ratio, "iv_source": "chain_cm30",
            "iv_to_forecast": ratio, "ratio_pctl": 0.5, "reading": "fair", "reading_basis": "cross_section",
            "earnings_in_horizon": False, "earnings_date": None, "flags": []}


def test_vol_latest_and_history_shapes(store):
    store.upsert_forecasts([_forecast("SPY", date(2026, 11, 13)), _forecast("SPY", date(2026, 11, 16), 1.3),
                            _forecast("AAA", date(2026, 11, 16))])
    c = _client(store)
    body = c.get("/api/quant/vol").json()
    assert body["as_of"] == "2026-11-16" and body["model_version"] == MODEL_VERSION and "advice" in body["disclaimer"]
    assert [r["ticker"] for r in body["rows"]] == ["AAA", "SPY"]
    assert set(body["rows"][0]) == ROW_KEYS
    assert body["rows"][1]["sector"] == "ETF"
    hist = c.get("/api/quant/vol/spy?days=3650").json()
    assert hist["ticker"] == "SPY" and [r["as_of"] for r in hist["rows"]] == ["2026-11-13", "2026-11-16"]
    assert set(hist["rows"][0]) == (ROW_KEYS - {"ticker", "sector"}) | {"as_of"}
    assert c.get("/api/quant/vol/NONE?days=3650").json()["rows"] == []
    assert c.get("/api/quant/vol/bad$ticker").status_code == 422


def test_vol_empty_database(store):
    body = _client(store).get("/api/quant/vol").json()
    assert body["rows"] == [] and body["as_of"] is None


def test_status_panel(store):
    store.upsert_bars([{"ticker": "SPY", "bar_date": date(2026, 11, 13), "open": 1, "high": 2, "low": 0.5,
                        "close": 1.5, "adj_close": 1.5, "volume": 1, "source": "fmp"}])
    store.record_model_run(kind="evaluation", model="har_logrv", version=MODEL_VERSION, n_obs=10, n_tickers=2,
                           window_start=date(2020, 1, 1), window_end=date(2026, 10, 1),
                           metrics={"models": {"trailing_21d": {"qlike": 0.37},
                                               "har_pooled": {"qlike": 0.29, "nw_t_vs_trailing": -3.6}},
                                    "har_beats_trailing": True, "data_source": "csv", "days": 5})
    with store.engine.begin() as conn:
        conn.execute(insert(PipelineRunLog.__table__).values(
            phase="quant_bars", status="SUCCESS", started_at=datetime(2026, 11, 16, 22, 5, tzinfo=timezone.utc),
            duration_ms=1000, meta={"result": {"status": "ok"}}))
    body = _client(store, role="USER").get("/api/quant/status").json()
    assert body["bars"]["tickers"] == 1 and body["bars"]["last_date"] == "2026-11-13"
    assert body["chains"]["days_collected"] == 0
    assert body["jobs"]["bars"]["status"] == "SUCCESS" and body["jobs"]["bars"]["result"] == {"status": "ok"}
    assert body["jobs"]["chains"] is None
    assert body["evaluation"]["har_beats_trailing"] is True and body["evaluation"]["n_obs"] == 10
    assert body["evaluation"]["har_verdict"] == "better_significant"
    assert body["evaluation"]["best_har"] == "har_pooled" and body["evaluation"]["long_run_beats_har"] is None
    assert body["model_runs"] == {"evaluation": 1} and body["model"]["version"] == MODEL_VERSION
    assert set(body["running"]) == {"bars", "chains", "forecast"}


def test_manual_run_is_admin_only(store, monkeypatch):
    started = []

    async def fake(job, scheduled=True):
        started.append((job, scheduled))
        return {"status": "ok"}

    monkeypatch.setattr(quant_runner, "run_quant_job", fake)
    assert _client(store, role="USER").post("/api/quant/run/bars").status_code == 403
    c = _client(store)
    assert c.post("/api/quant/run/nope").status_code == 404
    r = c.post("/api/quant/run/forecast")
    assert r.status_code == 202 and r.json() == {"job": "forecast", "status": "started"}
    assert started == [("forecast", False)]
    monkeypatch.setitem(quant_runner.quant_run_state["bars"], "running", True)
    assert c.post("/api/quant/run/bars").status_code == 409


def test_runner_skip_rules_and_result_parsing():
    et = lambda y, m, d, h, mi: datetime(y, m, d, h + 5, mi, tzinfo=timezone.utc)   # EST
    assert quant_runner.scheduled_skip_reason("bars", et(2026, 11, 26, 17, 5)) == "not a trading day"
    assert quant_runner.scheduled_skip_reason("bars", et(2026, 11, 16, 17, 5)) is None
    assert quant_runner.scheduled_skip_reason("chains", et(2026, 11, 16, 15, 40)) is None
    assert quant_runner.scheduled_skip_reason("chains", et(2026, 11, 27, 15, 40)) == "regular session closed"
    assert quant_runner.parse_result(b"log line\n{\"status\": \"ok\", \"rows\": 3}\n") == {"status": "ok", "rows": 3}
    assert quant_runner.parse_result(b"no json") == {}


@pytest.mark.asyncio
async def test_runner_logs_success_failure_and_timeout(monkeypatch):
    log = []

    async def start(phase, user_id=None, meta=None):
        log.append(("start", phase))
        return 7

    async def finish(run_id, status="SUCCESS", error_message=None, meta=None):
        log.append(("finish", status, error_message))

    import services.run_log as run_log
    monkeypatch.setattr(run_log, "record_run_start", start)
    monkeypatch.setattr(run_log, "record_run_finish", finish)
    outcomes = iter([(0, {"status": "ok"}, None), (1, {"status": "error", "message": "no bars"}, None),
                     (None, {}, "timeout after 900 s")])

    async def fake_sub(job, timeout):
        return next(outcomes)

    monkeypatch.setattr(quant_runner, "_subprocess", fake_sub)
    assert (await quant_runner.run_quant_job("forecast", scheduled=False))["status"] == "ok"
    assert (await quant_runner.run_quant_job("forecast", scheduled=False))["message"] == "no bars"
    assert (await quant_runner.run_quant_job("forecast", scheduled=False))["message"].startswith("timeout")
    assert log[1] == ("finish", "SUCCESS", None) and log[3] == ("finish", "FAILED", "no bars")
    assert log[5][1] == "FAILED" and not quant_runner.quant_run_state["forecast"]["running"]


def test_evaluation_is_stale_rules():
    from datetime import datetime, timedelta, timezone

    from quant.config import EVALUATION_MAX_AGE_DAYS, MODEL_VERSION
    from services.quant_runner import evaluation_is_stale

    now = datetime(2026, 11, 16, 22, 0, tzinfo=timezone.utc)
    fresh = {"version": MODEL_VERSION, "created_at": now - timedelta(days=EVALUATION_MAX_AGE_DAYS - 1)}
    assert evaluation_is_stale(None, now)
    assert not evaluation_is_stale(fresh, now)
    assert evaluation_is_stale({**fresh, "version": "older-model"}, now)
    assert evaluation_is_stale({**fresh, "created_at": now - timedelta(days=EVALUATION_MAX_AGE_DAYS)}, now)
    # a naive timestamp (SQLite) is read as UTC
    assert not evaluation_is_stale({**fresh, "created_at": (now - timedelta(days=1)).replace(tzinfo=None)}, now)


def test_status_recomputes_the_verdict_of_an_old_scorecard(store):
    """A scorecard recorded before the three-state verdict (har_beats_trailing meant "lower QLIKE") is read
    again from its models block: the 2026-11-27 production result is not shown as a win."""
    store.record_model_run(kind="evaluation", model="har_logrv", version=MODEL_VERSION, n_obs=480778, n_tickers=270,
                           metrics={"models": {"trailing_21d": {"qlike": 16.2103},
                                               "long_run_mean": {"qlike": 0.6, "nw_t_vs_trailing": -1.13},
                                               "har_pooled": {"qlike": 2.62, "nw_t_vs_trailing": -1.14},
                                               "har_ticker": {"qlike": 2.6334, "nw_t_vs_trailing": -1.13}},
                                    "best_har": "har_pooled", "har_beats_trailing": True})
    ev = _client(store, role="USER").get("/api/quant/status").json()["evaluation"]
    assert ev["har_verdict"] == "lower_not_significant"
    assert ev["har_beats_trailing"] is False and ev["har_lower_qlike"] is True
    assert ev["long_run_beats_har"] is True and ev["har_t_vs_trailing"] == -1.14
