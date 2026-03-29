"""Earnings calendar service.

Manages earnings date ingestion, catalyst-window calculations, and
peer-earnings detection across the sector universe.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import SECTORS
from db.models import EarningsCalendar
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)


class EarningsCalendarService:
    """Fetches, stores, and queries earnings calendar data."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------
    # 1. Refresh calendar from external sources
    # ------------------------------------------------------------------

    async def refresh_calendar(self, tickers: list[str]) -> int:
        """Fetch earnings dates for *tickers* and upsert into the database.

        Returns the count of records upserted.
        """
        loop = asyncio.get_event_loop()
        upserted = 0

        for ticker in tickers:
            try:
                results: list[dict] = await loop.run_in_executor(
                    None, self._data_client.get_earnings_calendar, [ticker]
                )
            except Exception:
                logger.exception("Failed to fetch earnings calendar for %s", ticker)
                continue

            for entry in results:
                raw_date = entry.get("earnings_date")
                if not raw_date:
                    continue

                try:
                    if isinstance(raw_date, date):
                        earnings_date = raw_date
                    else:
                        # Handle datetime strings — take date portion
                        earnings_date = date.fromisoformat(str(raw_date)[:10])
                except (ValueError, TypeError):
                    logger.warning(
                        "Unparseable earnings_date %r for %s — skipping",
                        raw_date,
                        ticker,
                    )
                    continue

                catalyst_window_start = earnings_date - timedelta(days=7)
                catalyst_window_end = earnings_date + timedelta(days=10)

                # Check for existing record (unique on ticker + earnings_date)
                stmt = select(EarningsCalendar).where(
                    EarningsCalendar.ticker == ticker,
                    EarningsCalendar.earnings_date == earnings_date,
                )
                result = await self._session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.earnings_time = entry.get("earnings_time")
                    existing.fiscal_quarter = entry.get("fiscal_quarter")
                    existing.catalyst_window_start = catalyst_window_start
                    existing.catalyst_window_end = catalyst_window_end
                else:
                    record = EarningsCalendar(
                        ticker=ticker,
                        earnings_date=earnings_date,
                        earnings_time=entry.get("earnings_time"),
                        fiscal_quarter=entry.get("fiscal_quarter"),
                        catalyst_window_start=catalyst_window_start,
                        catalyst_window_end=catalyst_window_end,
                    )
                    self._session.add(record)

                upserted += 1

        try:
            await self._session.flush()
        except Exception:
            logger.exception("Failed to flush earnings calendar upserts")
            await self._session.rollback()
            raise

        logger.info("Upserted %d earnings calendar records", upserted)
        return upserted

    # ------------------------------------------------------------------
    # 2. Get catalyst window for a single ticker
    # ------------------------------------------------------------------

    async def get_catalyst_window(self, ticker: str) -> dict | None:
        """Return the nearest relevant catalyst window for *ticker*.

        Relevant means the next upcoming earnings, or recent earnings
        still within T+10.  Returns ``None`` if no relevant record exists.
        """
        today = date.today()
        cutoff_past = today - timedelta(days=10)

        stmt = (
            select(EarningsCalendar)
            .where(
                EarningsCalendar.ticker == ticker,
                EarningsCalendar.earnings_date >= cutoff_past,
            )
            .order_by(EarningsCalendar.earnings_date.asc())
            .limit(1)
        )

        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None

        window_status = self._compute_window_status(today, record.earnings_date)

        return {
            "ticker": record.ticker,
            "earnings_date": record.earnings_date,
            "earnings_time": record.earnings_time,
            "catalyst_window_start": record.catalyst_window_start,
            "catalyst_window_end": record.catalyst_window_end,
            "window_status": window_status,
        }

    # ------------------------------------------------------------------
    # 3. Get upcoming catalysts across all tickers
    # ------------------------------------------------------------------

    async def get_upcoming_catalysts(self, days: int = 14) -> list[dict]:
        """Return all earnings records within the next *days* calendar days."""
        today = date.today()
        horizon = today + timedelta(days=days)

        stmt = (
            select(EarningsCalendar)
            .where(
                EarningsCalendar.earnings_date >= today,
                EarningsCalendar.earnings_date <= horizon,
            )
            .order_by(EarningsCalendar.earnings_date.asc())
        )

        result = await self._session.execute(stmt)
        records = result.scalars().all()

        catalysts: list[dict] = []
        for rec in records:
            days_until = (rec.earnings_date - today).days
            window_status = self._compute_window_status(today, rec.earnings_date)
            catalysts.append(
                {
                    "ticker": rec.ticker,
                    "earnings_date": rec.earnings_date,
                    "earnings_time": rec.earnings_time,
                    "days_until": days_until,
                    "window_status": window_status,
                }
            )

        return catalysts

    # ------------------------------------------------------------------
    # 4. Detect peer earnings
    # ------------------------------------------------------------------

    async def detect_peer_earnings(self, ticker: str) -> list[dict]:
        """Find same-sector peers with earnings within +/- 5 days of *ticker*."""
        # Identify the sector for the given ticker
        sector_name: str | None = None
        sector_tickers: list[str] = []

        for name, cfg in SECTORS.items():
            if ticker in cfg["universe"]:
                sector_name = name
                sector_tickers = [t for t in cfg["universe"] if t != ticker]
                break

        if not sector_name or not sector_tickers:
            logger.debug("No sector peers found for %s", ticker)
            return []

        # Find the ticker's nearest upcoming earnings
        today = date.today()
        stmt = (
            select(EarningsCalendar)
            .where(
                EarningsCalendar.ticker == ticker,
                EarningsCalendar.earnings_date >= today - timedelta(days=5),
            )
            .order_by(EarningsCalendar.earnings_date.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        anchor = result.scalar_one_or_none()

        if anchor is None:
            logger.debug("No upcoming earnings found for %s", ticker)
            return []

        window_start = anchor.earnings_date - timedelta(days=5)
        window_end = anchor.earnings_date + timedelta(days=5)

        # Query peers with earnings in that window
        peer_stmt = (
            select(EarningsCalendar)
            .where(
                EarningsCalendar.ticker.in_(sector_tickers),
                EarningsCalendar.earnings_date >= window_start,
                EarningsCalendar.earnings_date <= window_end,
            )
            .order_by(EarningsCalendar.earnings_date.asc())
        )

        peer_result = await self._session.execute(peer_stmt)
        peers = peer_result.scalars().all()

        return [
            {
                "ticker": p.ticker,
                "earnings_date": p.earnings_date,
                "earnings_time": p.earnings_time,
                "days_offset": (p.earnings_date - anchor.earnings_date).days,
            }
            for p in peers
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_window_status(today: date, earnings_date: date) -> str:
        """Derive the catalyst window status relative to *today*.

        Returns one of: APPROACHING, ACTIVE, POST, NONE.
        """
        days_diff = (earnings_date - today).days

        if days_diff > 7:
            return "APPROACHING"
        elif days_diff >= 0:
            # Within T-7 to T-0 (inclusive of earnings day)
            return "ACTIVE"
        elif days_diff >= -10:
            # T+1 to T+10 after earnings
            return "POST"
        else:
            return "NONE"
