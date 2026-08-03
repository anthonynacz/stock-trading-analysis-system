"""Recommendation outcome scoring.

Nightly worker (18:00 ET weekdays, see services/scheduler.py) that marks
every recommendation's spot forward return at T+1, T+5, and T+20 trading
days and records whether the call was directionally right. This is the
feedback loop the rest of the system lacks: per-signal hit rates aggregated
from these rows (GET /api/outcomes/summary) show which signals actually
earn and inform the user-tunable signal weights.

Mechanics:
  - "Pending" = recs from the last BACKFILL_DAYS with no outcome row or an
    unmatured one (t20 not yet filled). Grouped by ticker so each ticker
    costs one yfinance daily-history fetch per run.
  - Horizons are trading days: T+N = the Nth close strictly after
    recommendation_date in the ticker's daily series.
  - Entry price = the rec's captured current_price; falls back to the last
    close on/before the rec date. Recs with neither are skipped.
  - hit_* is direction-adjusted: BUY/STRONG_BUY want return > 0,
    SELL/STRONG_SELL want return < 0, HOLD keeps returns but hit stays NULL.
  - A rec older than MATURE_FALLBACK_DAYS with no fillable data is force-
    matured so delisted tickers don't stay pending forever.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from db.connection import async_session
from db.models import Recommendation, RecommendationOutcome
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)

BACKFILL_DAYS = 180          # how far back the first run scores history
MATURE_FALLBACK_DAYS = 60    # force-mature recs this old even without data
HORIZONS = (1, 5, 20)        # trading-day marks

_BULLISH = {"BUY", "STRONG_BUY"}
_BEARISH = {"SELL", "STRONG_SELL"}


def _direction(action: str) -> int:
    if action in _BULLISH:
        return 1
    if action in _BEARISH:
        return -1
    return 0


def _pick_period(oldest: date) -> str:
    """yfinance period covering the oldest pending rec + a 2-month tail so
    T+20 can mature."""
    days = (date.today() - oldest).days + 40
    if days <= 85:
        return "3mo"
    if days <= 175:
        return "6mo"
    return "1y"


def _score_rec(
    rec: Recommendation,
    closes: list[tuple[date, float]],
    outcome: RecommendationOutcome,
) -> bool:
    """Fill any newly-mature horizons on `outcome`. Returns True if the row
    changed."""
    rec_date = rec.recommendation_date
    direction = _direction(rec.action)

    entry: float | None = float(rec.current_price) if rec.current_price else None
    if entry is None:
        on_or_before = [c for d, c in closes if d <= rec_date]
        entry = on_or_before[-1] if on_or_before else None
    if not entry or entry <= 0:
        return False

    after = [(d, c) for d, c in closes if d > rec_date]
    changed = False
    if outcome.entry_price is None:
        outcome.entry_price = Decimal(str(round(entry, 2)))
        changed = True

    for n in HORIZONS:
        if getattr(outcome, f"return_t{n}_pct") is not None:
            continue
        if len(after) < n:
            continue
        price = after[n - 1][1]
        ret = (price - entry) / entry * 100.0
        setattr(outcome, f"price_t{n}", Decimal(str(round(price, 2))))
        setattr(outcome, f"return_t{n}_pct", Decimal(str(round(ret, 2))))
        if direction != 0:
            setattr(outcome, f"hit_t{n}", (ret > 0) if direction > 0 else (ret < 0))
        changed = True

    if outcome.return_t20_pct is not None and not outcome.matured:
        outcome.matured = True
        changed = True
    return changed


async def run_outcome_scoring() -> dict[str, Any]:
    """Cron entry point. One yfinance history fetch per ticker with pending
    recs; fills every newly-mature horizon."""
    from services.run_log import (
        STATUS_FAILED,
        STATUS_SUCCESS,
        record_run_finish,
        record_run_start,
    )

    run_id = await record_run_start(phase="outcome_scoring", user_id=None)
    started = datetime.now(tz=timezone.utc)
    today = date.today()
    cutoff = today - timedelta(days=BACKFILL_DAYS)
    summary: dict[str, Any] = {
        "tickers": 0, "scored": 0, "matured": 0, "skipped": 0, "errors": 0,
    }
    session = async_session()
    client = DataSourceClient()
    error: str | None = None
    try:
        rows = await session.execute(
            select(Recommendation, RecommendationOutcome)
            .outerjoin(
                RecommendationOutcome,
                RecommendationOutcome.recommendation_id == Recommendation.id,
            )
            .where(
                Recommendation.recommendation_date >= cutoff,
                Recommendation.recommendation_date < today,
            )
        )
        by_ticker: dict[str, list[tuple[Recommendation, RecommendationOutcome | None]]] = {}
        for rec, outcome in rows.all():
            if outcome is not None and outcome.matured:
                continue
            by_ticker.setdefault(rec.ticker, []).append((rec, outcome))

        loop = asyncio.get_event_loop()
        for ticker, pending in sorted(by_ticker.items()):
            summary["tickers"] += 1
            try:
                oldest = min(r.recommendation_date for r, _ in pending)
                closes = await loop.run_in_executor(
                    None, client.get_daily_closes, ticker, _pick_period(oldest)
                )
                for rec, outcome in pending:
                    if outcome is None:
                        outcome = RecommendationOutcome(
                            recommendation_id=rec.id,
                            recommendation_date=rec.recommendation_date,
                            ticker=rec.ticker,
                            action=rec.action,
                            conviction_score=rec.conviction_score,
                        )
                        session.add(outcome)
                    changed = _score_rec(rec, closes, outcome)
                    stale = rec.recommendation_date < today - timedelta(days=MATURE_FALLBACK_DAYS)
                    if stale and not outcome.matured:
                        outcome.matured = True  # aged out (delisted / no data)
                        changed = True
                    if changed:
                        summary["scored"] += 1
                        if outcome.matured:
                            summary["matured"] += 1
                    else:
                        summary["skipped"] += 1
                await session.commit()
            except Exception:
                logger.exception("outcome scoring failed for %s", ticker)
                summary["errors"] += 1
                await session.rollback()
    except Exception as exc:
        logger.exception("outcome scoring run failed")
        error = f"{type(exc).__name__}: {exc}"
        await session.rollback()
    finally:
        client.close()
        await session.close()

    summary["duration_s"] = round((datetime.now(tz=timezone.utc) - started).total_seconds(), 1)
    logger.info("Outcome scoring done: %s", summary)
    await record_run_finish(
        run_id,
        status=STATUS_FAILED if error or summary["errors"] else STATUS_SUCCESS,
        error_message=error,
        meta=summary,
    )
    return summary
