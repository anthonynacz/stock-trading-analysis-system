"""Dynamic universe discovery service.

Scans FMP market lists (most active, gainers, losers) and news-trending
tickers to surface candidates for the user to approve into the universe.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MIN_AVG_VOLUME, MIN_MARKET_CAP
from db.models import DiscoveryCandidate, NewsTickerRelevance, UniverseStock
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)

MAX_CANDIDATES_PER_RUN = 20

# Map yfinance sector names → EdgeFlow sector names
YFINANCE_SECTOR_MAP: dict[str, str | None] = {
    "Technology": "Consumer/Cloud/Enterprise",
    "Semiconductors": "AI/Semiconductors",
    "Financial Services": "Fintech/Payments",
    "Energy": "Energy/Commodities",
    "Basic Materials": "Energy/Commodities",
    "Healthcare": "Healthcare/Biotech",
    "Consumer Cyclical": "Consumer/Cloud/Enterprise",
    "Consumer Defensive": "Consumer/Cloud/Enterprise",
    "Communication Services": "Consumer/Cloud/Enterprise",
    "Industrials": None,
    "Real Estate": None,
    "Utilities": None,
}


class UniverseDiscoverer:
    """Discovers trending stocks and creates candidates for user approval."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    async def discover(self) -> list[DiscoveryCandidate]:
        """Run discovery from all sources. Returns newly created candidates."""
        loop = asyncio.get_event_loop()

        # 1. Fetch FMP market lists in parallel
        actives, gainers, losers = await asyncio.gather(
            loop.run_in_executor(None, self._data_client.get_most_active),
            loop.run_in_executor(None, self._data_client.get_biggest_gainers),
            loop.run_in_executor(None, self._data_client.get_biggest_losers),
        )

        # Tag each with source
        raw: list[tuple[dict, str]] = []
        for item in actives:
            raw.append((item, "MOST_ACTIVE"))
        for item in gainers:
            raw.append((item, "GAINER"))
        for item in losers:
            raw.append((item, "LOSER"))

        # 2. News-trending tickers (3+ articles in last 24h)
        news_trending = await self._get_news_trending_tickers()
        for ticker in news_trending:
            raw.append(({"symbol": ticker, "name": "", "price": None, "change": None, "change_pct": None}, "NEWS_TRENDING"))

        # 3. Deduplicate by symbol (keep first occurrence)
        seen: set[str] = set()
        deduped: list[tuple[dict, str]] = []
        for item, source in raw:
            symbol = item.get("symbol", "").upper().strip()
            if not symbol or symbol in seen:
                continue
            # Skip non-standard tickers (ETFs with numbers, warrants, etc.)
            if not symbol.isalpha() or len(symbol) > 5:
                continue
            seen.add(symbol)
            deduped.append((item, source))

        # 4. Exclude tickers already in universe or pending candidates
        existing_tickers = await self._get_existing_tickers()
        deduped = [(item, src) for item, src in deduped if item["symbol"] not in existing_tickers]

        logger.info("Discovery: %d candidates after dedup and exclusion", len(deduped))

        # 5. Enrich and filter (cap at MAX_CANDIDATES_PER_RUN)
        created: list[DiscoveryCandidate] = []
        for item, source in deduped[:MAX_CANDIDATES_PER_RUN * 2]:  # fetch extra in case some fail filters
            if len(created) >= MAX_CANDIDATES_PER_RUN:
                break

            symbol = item["symbol"]
            try:
                stock_data = await loop.run_in_executor(
                    None, self._data_client.get_stock_data, symbol
                )
            except Exception:
                logger.debug("Skipping %s — failed to fetch stock data", symbol)
                continue

            if not stock_data or not stock_data.get("price"):
                continue

            market_cap = stock_data.get("market_cap") or 0
            avg_volume = stock_data.get("avg_volume") or 0

            # Apply same filters as watchlist
            if market_cap < MIN_MARKET_CAP or avg_volume < MIN_AVG_VOLUME:
                continue

            # Map yfinance sector to EdgeFlow sector
            yf_sector = stock_data.get("sector") or ""
            yf_industry = stock_data.get("industry") or ""
            suggested_sector = YFINANCE_SECTOR_MAP.get(yf_sector)
            # Refine: if industry mentions "semiconductor", override to AI/Semi
            if "semiconductor" in yf_industry.lower() or "chip" in yf_industry.lower():
                suggested_sector = "AI/Semiconductors"

            # Build rationale
            change_pct = item.get("change_pct")
            parts = [f"Discovered via {source.replace('_', ' ').title()}."]
            if stock_data.get("company_name"):
                parts.append(f"{stock_data['company_name']}.")
            parts.append(f"Price: ${stock_data['price']:.2f}.")
            if change_pct is not None:
                try:
                    parts.append(f"Change: {float(change_pct):+.1f}%.")
                except (ValueError, TypeError):
                    pass
            parts.append(f"Market cap: ${market_cap / 1e9:.1f}B.")
            if yf_sector:
                parts.append(f"Sector: {yf_sector}.")

            candidate = DiscoveryCandidate(
                ticker=symbol,
                company_name=stock_data.get("company_name"),
                suggested_sector=suggested_sector,
                source=source,
                score=Decimal(str(abs(float(change_pct)))) if change_pct else None,
                market_cap=Decimal(str(market_cap)) if market_cap else None,
                avg_volume=Decimal(str(avg_volume)) if avg_volume else None,
                price=Decimal(str(stock_data["price"])),
                change_pct=Decimal(str(float(change_pct))) if change_pct else None,
                rationale=" ".join(parts),
                status="PENDING",
            )
            self._session.add(candidate)
            created.append(candidate)

        if created:
            await self._session.commit()
            logger.info("Discovery complete: %d new candidates created", len(created))
        else:
            logger.info("Discovery complete: no new candidates")

        return created

    async def _get_existing_tickers(self) -> set[str]:
        """Return tickers already in universe or pending as candidates."""
        universe_result = await self._session.execute(
            select(UniverseStock.ticker).where(UniverseStock.is_active.is_(True))
        )
        pending_result = await self._session.execute(
            select(DiscoveryCandidate.ticker).where(
                DiscoveryCandidate.status == "PENDING"
            )
        )
        tickers = {row[0] for row in universe_result.all()}
        tickers.update(row[0] for row in pending_result.all())
        return tickers

    async def _get_news_trending_tickers(self) -> list[str]:
        """Find tickers with 3+ news articles in the last 24 hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            result = await self._session.execute(
                select(
                    NewsTickerRelevance.ticker,
                    sa_func.count(sa_func.distinct(NewsTickerRelevance.news_id)).label("article_count"),
                )
                .where(NewsTickerRelevance.relevance_score >= 0.5)
                .group_by(NewsTickerRelevance.ticker)
                .having(sa_func.count(sa_func.distinct(NewsTickerRelevance.news_id)) >= 3)
            )
            return [row.ticker for row in result.all()]
        except Exception:
            logger.exception("Failed to query news-trending tickers")
            return []
