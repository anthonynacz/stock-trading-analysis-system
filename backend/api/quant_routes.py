"""Quant layer routes (/api/quant/...): volatility forecast vs implied vol, coverage and honesty panel.

Reads go through quant/store.py (sync) in the threadpool, like the radar routes. Display only; the readings
are relative statistics, not advice.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from auth import get_current_user
from db.models import User

router = APIRouter(prefix="/api/quant", tags=["quant"])

DISCLAIMER = ("Statistical volatility forecast and a relative reading of option prices against it. "
              "Not a recommendation and not financial advice.")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
ROW_FIELDS = ("price", "forecast_vol", "realized_vol_21d", "implied_vol_30d", "iv_source", "iv_to_forecast",
              "ratio_pctl", "reading", "reading_basis", "earnings_in_horizon", "earnings_date", "flags")
_store = None


def get_quant_store():
    """Process-wide QuantStore, imported lazily so a broken quant package cannot stop the API."""
    global _store
    if _store is None:
        from quant.store import QuantStore
        _store = QuantStore()
    return _store


def _sector_map(store) -> dict[str, str | None]:
    from quant.config import CORE_ETFS
    try:
        from config import SECTOR_ETFS
        etfs = set(SECTOR_ETFS.values())
    except Exception:  # noqa: BLE001
        etfs = set()
    out: dict[str, str | None] = {t: "ETF" for t in set(CORE_ETFS) | etfs}
    out.update({t: s for t, s in store.watchlist_sectors().items() if s})
    out.update(store.universe_sectors())
    return out


def _row(r: dict, sectors: dict[str, str | None]) -> dict:
    return {"ticker": r["ticker"], "sector": sectors.get(r["ticker"]), **{k: r.get(k) for k in ROW_FIELDS}}


def _latest(store) -> dict:
    as_of, version, rows = store.latest_forecasts()
    sectors = _sector_map(store) if rows else {}
    return {"as_of": as_of, "model_version": version, "disclaimer": DISCLAIMER,
            "rows": [_row(r, sectors) for r in rows]}


def _history(store, ticker: str, days: int) -> dict:
    rows = store.forecast_history(ticker, date.today() - timedelta(days=days))
    version = rows[-1]["model_version"] if rows else None
    rows = [r for r in rows if r["model_version"] == version]
    sector = _sector_map(store).get(ticker) if rows else None
    return {"ticker": ticker, "sector": sector, "model_version": version, "disclaimer": DISCLAIMER,
            "rows": [{"as_of": r["as_of_date"], **{k: r.get(k) for k in ROW_FIELDS}} for r in rows]}


def _job_view(row: dict | None) -> dict | None:
    if not row:
        return None
    meta = row.get("meta") or {}
    return {"status": row.get("status"), "started_at": row.get("started_at"), "finished_at": row.get("finished_at"),
            "duration_ms": row.get("duration_ms"), "error_message": row.get("error_message"),
            "result": meta.get("result") if isinstance(meta, dict) else None}


def _evaluation_view(run: dict | None) -> dict | None:
    if not run:
        return None
    m = run.get("metrics") or {}
    return {"created_at": run.get("created_at"), "model": run.get("model"), "version": run.get("version"),
            "window_start": run.get("window_start"), "window_end": run.get("window_end"),
            "n_obs": run.get("n_obs"), "n_tickers": run.get("n_tickers"), "days": m.get("days"),
            "data_source": m.get("data_source"), "models": m.get("models"), "best_har": m.get("best_har"),
            "har_beats_trailing": m.get("har_beats_trailing"),
            "estimator_to_close_close": m.get("estimator_to_close_close")}


def _status(store) -> dict:
    from quant.config import HAR, MODEL_NAME, MODEL_VERSION
    from services.quant_runner import quant_run_state

    st = store.status()
    return {
        "model": {"name": MODEL_NAME, "version": MODEL_VERSION, "horizon": HAR["horizon"], "pooled": HAR["pooled"]},
        "bars": st["bars"], "chains": st["chains"], "forecasts": st["forecasts"],
        "jobs": {job: _job_view(row) for job, row in st["jobs"].items()},
        "running": {job: bool(s["running"]) for job, s in quant_run_state.items()},
        "evaluation": _evaluation_view(store.latest_model_run("evaluation")),
        "model_runs": store.count_model_runs(),
        "disclaimer": DISCLAIMER,
    }


@router.get("/vol")
async def quant_vol(store=Depends(get_quant_store), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Latest forecast row per ticker: forecast vs trailing realised vs ~30-day implied vol, and the reading."""
    return await run_in_threadpool(_latest, store)


@router.get("/vol/{ticker}")
async def quant_vol_ticker(ticker: str, days: int = Query(365, ge=1, le=3650), store=Depends(get_quant_store),
                           user: User = Depends(get_current_user)) -> dict[str, Any]:
    """One ticker's forecast history (same fields, one row per as-of date, current model version)."""
    t = ticker.strip().upper()
    if not _TICKER.match(t):
        raise HTTPException(status_code=422, detail="invalid ticker")
    return await run_in_threadpool(_history, store, t, days)


@router.get("/status")
async def quant_status(store=Depends(get_quant_store), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Coverage of bars / chain snapshots / forecasts, the last run of each job, the latest evaluation."""
    return await run_in_threadpool(_status, store)


@router.post("/run/{job}", status_code=202)
async def quant_run(job: str, background: BackgroundTasks, user: User = Depends(get_current_user)) -> dict:
    """Start one quant job now (admin only; no holiday / session check). 404 unknown job, 409 running."""
    from services.quant_runner import QUANT_JOBS, quant_run_state, run_quant_job

    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Quant jobs are admin only")
    if job not in QUANT_JOBS:
        raise HTTPException(status_code=404, detail={"error": "unknown_job", "jobs": list(QUANT_JOBS)})
    state = quant_run_state[job]
    if state["running"]:
        raise HTTPException(status_code=409, detail={"error": "already_running", "started_at": state["started_at"]})
    background.add_task(run_quant_job, job, scheduled=False)
    return {"job": job, "status": "started"}
