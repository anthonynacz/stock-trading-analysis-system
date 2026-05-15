"""Retention sweeper.

Per-user, per-tier retention enforcement for `research_results` and
`deep_options_analyses`. Runs nightly via APScheduler.

Tier mapping (from services/entitlements.ENTITLEMENTS.research_retention_days):
  FREE    →  7 days
  STARTER →  7 days
  PRO     → 30 days
  PREMIUM → ∞   (UNLIMITED sentinel — skipped)
  ADMIN   → ∞   (skipped)

Implementation note: this is a hard delete, not a soft delete. The strategy
doc explicitly treats "retention" as the user's quota for keeping past
on-demand work, not a soft-archive. If a user upgrades, their previously
deleted research is gone — they'd need to re-run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import DeepOptionsAnalysis, ResearchResult, User
from services.entitlements import (
    UNLIMITED,
    get_entitlements_for,
    get_subscription,
    get_tier_for,
)

logger = logging.getLogger(__name__)


async def _retain_for_user(session: AsyncSession, user: User) -> dict[str, int]:
    """Delete user-owned research/deep-options rows older than the user's
    research_retention_days. Returns {table: deleted_count}."""
    sub = await get_subscription(session, user)
    tier = get_tier_for(user, sub)
    days = get_entitlements_for(tier).research_retention_days
    if days >= UNLIMITED:
        return {"research_results": 0, "deep_options_analyses": 0}

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    res_del = await session.execute(
        delete(ResearchResult)
        .where(
            ResearchResult.user_id == user.id,
            ResearchResult.analyzed_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    deep_del = await session.execute(
        delete(DeepOptionsAnalysis)
        .where(
            DeepOptionsAnalysis.user_id == user.id,
            DeepOptionsAnalysis.analyzed_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    return {
        "research_results": int(res_del.rowcount or 0),
        "deep_options_analyses": int(deep_del.rowcount or 0),
    }


async def run_retention_sweep() -> dict[str, Any]:
    """APScheduler entry point. Walks every user, deletes rows past their
    tier's retention window. Tier-skip (UNLIMITED) is a no-op."""
    from services.run_log import (
        STATUS_FAILED,
        STATUS_SUCCESS,
        record_run_finish,
        record_run_start,
    )

    global_run_id = await record_run_start(phase="retention_sweep", user_id=None)
    started = datetime.now(tz=timezone.utc)
    summary = {
        "started_at": started.isoformat(),
        "users_processed": 0,
        "research_results_deleted": 0,
        "deep_options_analyses_deleted": 0,
        "errors": 0,
    }
    session = async_session()
    try:
        users_q = await session.execute(select(User).where(User.is_active.is_(True)))
        for user in users_q.scalars():
            try:
                counts = await _retain_for_user(session, user)
                summary["users_processed"] += 1
                summary["research_results_deleted"] += counts["research_results"]
                summary["deep_options_analyses_deleted"] += counts["deep_options_analyses"]
                # Per-user row only when something was actually deleted
                if counts["research_results"] or counts["deep_options_analyses"]:
                    u_run = await record_run_start(phase="retention_sweep", user_id=user.id)
                    await record_run_finish(
                        u_run, status=STATUS_SUCCESS, meta=counts,
                    )
            except Exception as exc:
                logger.exception("retention sweep failed for user %s", user.id)
                summary["errors"] += 1
                await session.rollback()
                u_run = await record_run_start(phase="retention_sweep", user_id=user.id)
                await record_run_finish(
                    u_run,
                    status=STATUS_FAILED,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
        await session.commit()
    finally:
        await session.close()

    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    logger.info("Retention sweep done: %s", summary)
    await record_run_finish(
        global_run_id,
        status=STATUS_SUCCESS if summary["errors"] == 0 else STATUS_FAILED,
        meta={k: v for k, v in summary.items() if k not in {"started_at", "finished_at"}},
    )
    return summary
