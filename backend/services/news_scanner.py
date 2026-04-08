"""News scanning service for EdgeFlow.

Fetches, classifies, scores, and persists market news for tracked tickers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import MarketNews, NewsTickerRelevance
from utils.data_sources import DataSourceClient
from utils.finbert import score_batch
from utils.scoring import assign_impact_level, classify_news_category, compute_ticker_relevance

logger = logging.getLogger(__name__)


class NewsScanner:
    """Scans multiple data sources for market news and persists scored results."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------
    # scan_news
    # ------------------------------------------------------------------

    async def scan_news(
        self,
        tickers: list[str],
        ticker_company_map: dict[str, str | None] | None = None,
    ) -> list[MarketNews]:
        """Fetch and persist news for *tickers* plus general market news.

        *ticker_company_map* maps ticker symbols to company names for
        relevance scoring.  When provided, each article is scored against
        all tickers and ``NewsTickerRelevance`` rows are created.

        Returns the list of newly inserted :class:`MarketNews` rows.
        """
        if ticker_company_map is None:
            ticker_company_map = {}

        loop = asyncio.get_event_loop()
        raw_items: list[tuple[str | None, dict]] = []

        # Fetch ticker-specific news ------------------------------------------
        for ticker in tickers:
            try:
                items = await loop.run_in_executor(
                    None, self._data_client.get_news, ticker
                )
                for item in items:
                    raw_items.append((ticker, item))
            except Exception:
                logger.exception("Failed to fetch news for ticker %s", ticker)

        # Fetch general market news --------------------------------------------
        try:
            general_items = await loop.run_in_executor(
                None, self._data_client.get_news, None
            )
            for item in general_items:
                raw_items.append((None, item))
        except Exception:
            logger.exception("Failed to fetch general market news")

        if not raw_items:
            return []

        # Collect existing source_urls to deduplicate --------------------------
        urls = [item.get("url", "") for _, item in raw_items if item.get("url")]
        existing_urls: set[str] = set()
        # Map existing URLs to their MarketNews IDs for relevance backfill
        url_to_news_id: dict[str, int] = {}
        if urls:
            result = await self._session.execute(
                select(MarketNews.id, MarketNews.source_url).where(
                    MarketNews.source_url.in_(urls)
                )
            )
            for row in result.all():
                if row[1]:
                    existing_urls.add(row[1])
                    url_to_news_id[row[1]] = row[0]

        # Also load existing relevance rows for those news IDs so we skip dupes
        existing_relevance_pairs: set[tuple[int, str]] = set()
        if url_to_news_id:
            rel_result = await self._session.execute(
                select(NewsTickerRelevance.news_id, NewsTickerRelevance.ticker).where(
                    NewsTickerRelevance.news_id.in_(url_to_news_id.values())
                )
            )
            existing_relevance_pairs = {(r[0], r[1]) for r in rel_result.all()}

        # Build deduplicated candidate list ------------------------------------
        seen_urls: set[str] = set(existing_urls)
        candidates: list[tuple[str | None, dict]] = []
        # Track duplicate items that need relevance backfill
        duplicate_items: list[tuple[str | None, dict]] = []

        for ticker, item in raw_items:
            source_url = item.get("url", "") or ""
            if source_url and source_url in seen_urls:
                # Article already exists — queue for relevance backfill
                if source_url in url_to_news_id and ticker_company_map:
                    duplicate_items.append((ticker, item))
                continue
            if source_url:
                seen_urls.add(source_url)
            candidates.append((ticker, item))

        # Backfill relevance rows for duplicate articles -----------------------
        backfill_relevances: list[NewsTickerRelevance] = []
        if duplicate_items and ticker_company_map:
            for searched_ticker, item in duplicate_items:
                source_url = item.get("url", "") or ""
                news_id = url_to_news_id.get(source_url)
                if not news_id:
                    continue
                api_related = item.get("related_tickers", [])
                headline = item.get("headline", "")
                summary = item.get("summary", "")

                for tk, company in ticker_company_map.items():
                    if (news_id, tk) in existing_relevance_pairs:
                        continue
                    score, source = compute_ticker_relevance(
                        tk, company, headline, summary,
                        api_related, was_searched_ticker=(tk == searched_ticker),
                    )
                    if score > 0:
                        backfill_relevances.append(
                            NewsTickerRelevance(
                                news_id=news_id, ticker=tk,
                                relevance_score=Decimal(str(score)),
                                relevance_source=source,
                            )
                        )
                        existing_relevance_pairs.add((news_id, tk))

        if backfill_relevances:
            self._session.add_all(backfill_relevances)
            logger.info("Backfilled %d relevance rows for existing articles", len(backfill_relevances))

        if not candidates:
            if backfill_relevances:
                await self._session.commit()
            return []

        # Batch score sentiment with FinBERT -----------------------------------
        texts = [
            f"{item.get('headline', '')} {item.get('summary', '')}"
            for _, item in candidates
        ]
        sentiment_scores = await loop.run_in_executor(None, score_batch, texts)

        # Build ORM objects ----------------------------------------------------
        new_items: list[MarketNews] = []

        for (searched_ticker, item), sentiment_score in zip(candidates, sentiment_scores):
            headline = item.get("headline", "")
            summary = item.get("summary", "")
            api_related = item.get("related_tickers", [])

            category = classify_news_category(headline)
            impact_level = assign_impact_level(category, sentiment_score)

            published_at = self._parse_datetime(item.get("published_at"))

            news_row = MarketNews(
                ticker=searched_ticker,
                headline=headline,
                summary=summary or None,
                source=item.get("source") or None,
                category=category,
                sentiment_score=Decimal(str(round(sentiment_score, 3))),
                impact_level=impact_level,
                published_at=published_at,
                source_url=item.get("url") or None,
            )

            # Compute relevance for all watchlist tickers
            if ticker_company_map:
                for tk, company in ticker_company_map.items():
                    score, source = compute_ticker_relevance(
                        tk, company, headline, summary,
                        api_related, was_searched_ticker=(tk == searched_ticker),
                    )
                    if score > 0:
                        news_row.ticker_relevances.append(
                            NewsTickerRelevance(
                                ticker=tk,
                                relevance_score=Decimal(str(score)),
                                relevance_source=source,
                            )
                        )

            new_items.append(news_row)

        # Persist --------------------------------------------------------------
        if new_items:
            self._session.add_all(new_items)
            await self._session.commit()
            logger.info("Inserted %d new news items", len(new_items))

        return new_items

    # ------------------------------------------------------------------
    # get_recent_news
    # ------------------------------------------------------------------

    async def get_recent_news(
        self,
        ticker: str | None = None,
        hours: int = 48,
        category: str | None = None,
        impact_level: str | None = None,
    ) -> list[MarketNews]:
        """Query recent news from the database with optional filters.

        When *ticker* is provided, queries through the ``NewsTickerRelevance``
        junction table to find all articles relevant to that ticker.
        Results are ordered by ``published_at DESC`` and limited to 100 rows.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

        if ticker is not None:
            # Query through junction table for multi-ticker relevance
            stmt = (
                select(MarketNews)
                .join(NewsTickerRelevance)
                .where(
                    NewsTickerRelevance.ticker == ticker,
                    MarketNews.published_at >= cutoff,
                )
                .options(selectinload(MarketNews.ticker_relevances))
            )
        else:
            stmt = select(MarketNews).where(MarketNews.published_at >= cutoff)

        if category is not None:
            stmt = stmt.where(MarketNews.category == category)
        if impact_level is not None:
            stmt = stmt.where(MarketNews.impact_level == impact_level)

        stmt = stmt.order_by(MarketNews.published_at.desc()).limit(100)

        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    # ------------------------------------------------------------------
    # get_sentiment_summary
    # ------------------------------------------------------------------

    async def get_sentiment_summary(
        self, ticker: str, hours: int = 48
    ) -> dict:
        """Return an aggregate sentiment summary for *ticker*.

        Keys: avg_sentiment, positive_count, negative_count, neutral_count,
        total_count, top_headline.
        """
        news = await self.get_recent_news(ticker=ticker, hours=hours)

        if not news:
            return {
                "avg_sentiment": 0.0,
                "positive_count": 0,
                "negative_count": 0,
                "neutral_count": 0,
                "total_count": 0,
                "top_headline": None,
            }

        positive_count = 0
        negative_count = 0
        neutral_count = 0
        sentiment_sum = 0.0

        for item in news:
            score = float(item.sentiment_score or 0)
            sentiment_sum += score
            if score > 0.1:
                positive_count += 1
            elif score < -0.1:
                negative_count += 1
            else:
                neutral_count += 1

        total_count = len(news)

        # Most impactful headline: prefer HIGH, then MEDIUM, then by abs sentiment
        impact_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        top_item = max(
            news,
            key=lambda n: (
                -impact_order.get(n.impact_level or "LOW", 2),
                abs(float(n.sentiment_score or 0)),
            ),
        )

        return {
            "avg_sentiment": round(sentiment_sum / total_count, 3),
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count,
            "total_count": total_count,
            "top_headline": top_item.headline,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(value) -> datetime | None:
        """Best-effort parse of a datetime value from the data source."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (OSError, ValueError):
                return None
        if isinstance(value, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None
