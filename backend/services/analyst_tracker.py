"""Service for tracking analyst rating changes and persisting them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AnalystRating
from utils.data_sources import DataSourceClient
from utils.scoring import calculate_impact_score, classify_rating_type, get_firm_tier

logger = logging.getLogger(__name__)


class AnalystTracker:
    """Scans for new analyst ratings, scores them, and persists to the DB."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------ #
    # Scan & persist
    # ------------------------------------------------------------------ #

    async def scan_ratings(self, tickers: list[str]) -> list[AnalystRating]:
        """Fetch ratings for each ticker, deduplicate, and persist new ones.

        Returns the list of newly inserted ``AnalystRating`` objects.
        """
        all_new: list[AnalystRating] = []
        loop = asyncio.get_event_loop()

        for ticker in tickers:
            try:
                raw_ratings: list[dict] = await loop.run_in_executor(
                    None, self._data_client.get_analyst_ratings, ticker
                )
            except Exception:
                logger.exception("Failed to fetch ratings for %s", ticker)
                continue

            for raw in raw_ratings:
                try:
                    rating_obj = await self._process_rating(raw)
                    if rating_obj is not None:
                        all_new.append(rating_obj)
                except Exception:
                    logger.exception(
                        "Failed to process rating for %s from %s",
                        ticker,
                        raw.get("firm", "unknown"),
                    )

        if all_new:
            await self._session.flush()
            logger.info("Persisted %d new analyst ratings", len(all_new))

        return all_new

    async def _process_rating(self, raw: dict) -> AnalystRating | None:
        """Transform a raw rating dict into an ORM object and persist it.

        Returns ``None`` if the rating already exists in the database.
        """
        ticker: str = raw.get("ticker", "")
        firm: str = raw.get("firm", "")
        previous_rating: str | None = raw.get("previous_rating") or None
        new_rating: str | None = raw.get("new_rating") or None
        previous_pt = raw.get("previous_pt")
        new_pt = raw.get("new_pt")

        # Parse published_at
        published_raw = raw.get("published_at", "")
        if isinstance(published_raw, str) and published_raw:
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now(tz=timezone.utc)
        elif isinstance(published_raw, datetime):
            published_at = published_raw
        else:
            published_at = datetime.now(tz=timezone.utc)

        # Deduplicate: same ticker + firm + published_at already in DB?
        existing = await self._session.execute(
            select(AnalystRating).where(
                AnalystRating.ticker == ticker,
                AnalystRating.analyst_firm == firm,
                AnalystRating.published_at == published_at,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        # Scoring
        rating_type = classify_rating_type(previous_rating, new_rating)
        firm_tier = get_firm_tier(firm)

        pt_change_pct = 0.0
        if previous_pt is not None and new_pt is not None:
            try:
                prev = float(previous_pt)
                curr = float(new_pt)
                if prev != 0:
                    pt_change_pct = ((curr - prev) / prev) * 100.0
            except (TypeError, ValueError):
                pt_change_pct = 0.0

        impact_score = calculate_impact_score(rating_type, firm_tier, pt_change_pct)

        # Build ORM object
        obj = AnalystRating(
            ticker=ticker,
            analyst_firm=firm,
            analyst_name=raw.get("analyst_name") or None,
            rating_type=rating_type,
            previous_rating=previous_rating,
            new_rating=new_rating,
            previous_pt=Decimal(str(previous_pt)) if previous_pt is not None else None,
            new_pt=Decimal(str(new_pt)) if new_pt is not None else None,
            firm_tier=firm_tier,
            published_at=published_at,
            impact_score=Decimal(str(round(impact_score, 2))),
        )
        self._session.add(obj)
        return obj

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    async def get_recent_ratings(
        self, ticker: str | None = None, hours: int = 48
    ) -> list[AnalystRating]:
        """Return ratings from the last *hours*, optionally filtered by ticker.

        Results are ordered by ``published_at`` descending.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(AnalystRating)
            .where(AnalystRating.published_at >= cutoff)
            .order_by(AnalystRating.published_at.desc())
        )
        if ticker is not None:
            stmt = stmt.where(AnalystRating.ticker == ticker)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_tier_changes(self, hours: int = 48) -> list[AnalystRating]:
        """Return only TIER_CHANGE ratings from the last *hours*."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(AnalystRating)
            .where(
                AnalystRating.published_at >= cutoff,
                AnalystRating.rating_type == "TIER_CHANGE",
            )
            .order_by(AnalystRating.published_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
