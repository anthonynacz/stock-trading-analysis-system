"""Helpers for writing rows to `pipeline_run_log`.

All scheduler-driven invocations (phase runs, per-user worker ticks) go
through `record_run_start` / `record_run_finish` so the /schedule page
can show what ran when.

Design notes:
  - Logging uses its OWN AsyncSession so it doesn't get rolled back when
    a phase's session rolls back on error.
  - All writes swallow exceptions internally — observability must never
    break the pipeline itself. Failures are logged at WARNING level.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update

from db.connection import async_session
from db.models import PipelineRunLog

logger = logging.getLogger(__name__)


STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


async def record_run_start(
    phase: str,
    user_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> int | None:
    """Insert a RUNNING row and return its id. Returns None on failure."""
    session = async_session()
    try:
        row = PipelineRunLog(
            phase=phase,
            user_id=user_id,
            status=STATUS_RUNNING,
            started_at=datetime.now(tz=timezone.utc),
            meta=meta,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id
    except Exception:
        logger.warning("record_run_start failed for phase=%s user=%s", phase, user_id, exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
        return None
    finally:
        await session.close()


async def record_run_finish(
    run_id: int | None,
    status: str = STATUS_SUCCESS,
    error_message: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Update the row created by record_run_start with finish state.

    `meta` is merged with whatever was set at start (start-meta keys are
    preserved unless finish-meta overrides them)."""
    if run_id is None:
        return
    session = async_session()
    try:
        # Fetch to merge meta; cheaper than a JSONB concat for this volume
        row = await session.get(PipelineRunLog, run_id)
        if row is None:
            return
        finished = datetime.now(tz=timezone.utc)
        started = row.started_at
        # row.started_at is tz-aware (TIMESTAMPTZ); guard against the rare
        # case where it isn't and the subtraction would explode.
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)

        merged_meta = dict(row.meta or {})
        if meta:
            merged_meta.update(meta)

        await session.execute(
            update(PipelineRunLog)
            .where(PipelineRunLog.id == run_id)
            .values(
                status=status,
                finished_at=finished,
                duration_ms=duration_ms,
                error_message=(error_message or None),
                meta=merged_meta or None,
            )
        )
        await session.commit()
    except Exception:
        logger.warning("record_run_finish failed for id=%s", run_id, exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
    finally:
        await session.close()
