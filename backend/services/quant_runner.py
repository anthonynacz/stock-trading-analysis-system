"""Runs the quant jobs (backend/quant/) from APScheduler and from POST /api/quant/run/{job}.

Each job is a subprocess, `python -m quant.run <job>`, with a timeout (quant.config.JOB_TIMEOUT_S), so a hung
fetch or a numpy-heavy fit never blocks the API's event loop. Each run is logged to pipeline_run_log as phase
`quant_<job>` (meta = the job's JSON result). One run per job at a time (`quant_run_state`).

Scheduled runs skip NYSE holidays (radar/calendar_nyse.py); the chain snapshot also needs the regular session
to be open (early-close days skip it). A manual run skips neither check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant.config import JOB_TIMEOUT_S
from radar import calendar_nyse as cal

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
QUANT_JOBS = ("bars", "chains", "forecast")

quant_run_state: dict[str, dict[str, Any]] = {
    job: {"running": False, "started_at": None, "finished_at": None, "status": None} for job in QUANT_JOBS
}


def scheduled_skip_reason(job: str, now: datetime) -> str | None:
    """Why a scheduled run of `job` should not run at `now`, else None."""
    day = now.astimezone(cal.ET).date()
    if not cal.is_trading_day(day):
        return "not a trading day"
    if job == "chains":
        phase, _ = cal.phase_at(now)
        if phase != "regular":
            return "regular session closed"
    return None


def parse_result(stdout: bytes | str) -> dict:
    """The JSON result on the last non-empty stdout line, or {} when there is none."""
    text = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else (stdout or "")
    for line in reversed(text.strip().splitlines()):
        try:
            res = json.loads(line)
            return res if isinstance(res, dict) else {}
        except ValueError:
            continue
    return {}


async def _subprocess(job: str, timeout: float) -> tuple[int | None, dict, str | None]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "quant.run", job, cwd=str(BACKEND_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=None)      # stderr: the job's logs go to the container log
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None, {}, f"timeout after {int(timeout)} s"
    return proc.returncode, parse_result(out), None


async def run_quant_job(job: str, *, scheduled: bool = True) -> dict:
    """Run one quant job; returns its result (status ok / degraded / error / skipped / already_running)."""
    from services.run_log import STATUS_FAILED, STATUS_SUCCESS, record_run_finish, record_run_start

    if job not in QUANT_JOBS:
        raise ValueError(f"unknown quant job {job!r}")
    now = datetime.now(timezone.utc)
    if scheduled:
        reason = scheduled_skip_reason(job, now)
        if reason:
            logger.info("quant %s skipped: %s", job, reason)
            return {"status": "skipped", "reason": reason}
    state = quant_run_state[job]
    if state["running"]:
        return {"status": "already_running", "started_at": state["started_at"]}
    state.update(running=True, started_at=now.isoformat(), finished_at=None, status="running")
    run_id = await record_run_start(phase=f"quant_{job}", meta={"scheduled": scheduled})
    error: str | None = None
    result: dict = {}
    try:
        code, result, error = await _subprocess(job, float(JOB_TIMEOUT_S[job]))
        if error is None and (code != 0 or result.get("status") == "error"):
            error = str(result.get("message") or f"exit code {code}")[:500]
    except Exception as exc:  # noqa: BLE001 - logged in the run log
        logger.exception("quant %s could not run", job)
        error = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        status = STATUS_FAILED if error else STATUS_SUCCESS
        state.update(running=False, finished_at=datetime.now(timezone.utc).isoformat(), status=status)
        await record_run_finish(run_id, status=status, error_message=error, meta={"result": _slim(result)})
    if error:
        result = {**result, "status": "error", "message": error}
    logger.info("quant %s finished: %s", job, result.get("status"))
    return result


async def run_quant_close() -> None:
    """After the close: bars, then the forecast (which uses whatever bars are stored)."""
    res = await run_quant_job("bars")
    if res.get("status") in ("skipped", "already_running"):
        return
    await run_quant_job("forecast")


def _slim(result: dict) -> dict:
    """Keep the run-log meta small: long ticker lists are cut to 30 names."""
    out = {}
    for k, v in (result or {}).items():
        out[k] = (v[:30] + [f"... {len(v) - 30} more"]) if isinstance(v, list) and len(v) > 30 else v
    return out
