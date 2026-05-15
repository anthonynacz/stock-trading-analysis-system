"""Dynamic universe discovery service.

Scans FMP market lists (most active, gainers, losers) and news-trending
tickers to surface candidates for the user to approve into the universe.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MIN_AVG_VOLUME, MIN_MARKET_CAP
from db.models import DiscoveryCandidate, MarketNews, NewsTickerRelevance, UniverseStock
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)

MAX_CANDIDATES_PER_RUN = 20

# Min distinct articles in the lookback window before a headline-mentioned
# token is considered a discovery candidate.
HEADLINE_MENTION_MIN_ARTICLES = 2
HEADLINE_MENTION_LOOKBACK_HOURS = 24

_HEADLINE_TOKEN_RE = re.compile(r"\b[A-Z]{2,5}\b")

# Tokens that look like tickers but never are. Validation via get_stock_data
# catches the long tail; this list just trims obvious noise so we don't burn
# yfinance calls on words like "NEWS" or "CEO".
_HEADLINE_STOP_TOKENS: frozenset[str] = frozenset({
    # Common English words that get capitalized in headlines
    "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN", "IS", "IT",
    "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "AM", "PM", "AN", "OK",
    "AND", "ANY", "ARE", "BIG", "BUT", "CAN", "DAY", "FAR", "FEW",
    "FOR", "GET", "GOT", "HAD", "HAS", "HER", "HIS", "HOW", "ITS",
    "MAY", "NEW", "NOT", "NOW", "OLD", "ONE", "OUR", "OUT", "OWN",
    "SAW", "SAY", "SEE", "SHE", "TOO", "TOP", "TWO", "USE", "WAS",
    "WAY", "WHO", "WHY", "WIN", "YES", "YET", "YOU", "ALL", "BAD",
    "GAS", "OFF", "EVE",
    "FROM", "HAVE", "INTO", "JUST", "LIKE", "LOOK", "MORE", "NEXT", "OVER",
    "PLAN", "READ", "SOME", "TALK", "TELL", "THAN", "THAT", "THE", "THEY",
    "THIS", "WHAT", "WHEN", "WILL", "WITH", "YEAR", "YOUR",
    "AFTER", "AGAIN", "ABOUT", "BELOW", "BEFORE",
    "BEST", "BUYS", "DEAL", "DEEP", "DIVE", "DOWN", "EACH", "EARN",
    "EARNS", "EVERY", "FALL", "FALLS", "FIRST", "GAIN", "GAINS",
    "GOOD", "HIGH", "HUGE", "JUMP", "LAST", "LATE", "LESS", "LIVE",
    "LOSS", "LOSES", "MAJOR", "MAKE", "MANY", "MISS", "MOST", "MOVE",
    "MUCH", "NEED", "NEWS", "NEVER", "ONLY", "OPEN", "PEAK", "POPS",
    "POST", "RIGHT", "RISE", "ROSE", "SAME", "SELL", "SETS", "SHOW",
    "SIGN", "SOAR", "STAR", "STEP", "STOP", "TAKE", "TIME", "TOPS",
    "TOTAL", "TURN", "WANT", "WEEK", "WELL", "WENT", "WHILE",
    "WILD", "WISE", "WORK", "FILE", "HOLD", "PICK", "PICKS",
    "BEAT", "BEATS", "MISS", "MISSES", "RAISE", "RAISES", "CUT",
    "CUTS", "BULL", "BEAR", "BULLS", "BEARS",
    # Financial / economic acronyms (not a single-stock ticker)
    "AI", "APR", "BPS", "CEO", "CFO", "CIO", "CMO", "COO", "CPI",
    "CTO", "ECB", "EPS", "ETF", "ETFS", "EU", "EUR", "EV", "EVP",
    "FAQ", "FBI", "FDA", "FED", "FOMC", "FOMO", "FX", "FY", "GDP",
    "GOP", "HQ", "IMF", "IPO", "IPOS", "IT", "JPY", "KPI",
    "LP", "LPS", "MOU", "NEO", "NFA", "NYSE", "OECD", "OPEC",
    "PMI", "PR", "PT", "ROE", "ROI", "ROIC", "RSI", "SEC", "SPAC",
    "SPACS", "SUV", "TBA", "TBD", "TLDR", "UAE", "UK", "UN", "USA",
    "USD", "VC", "VIX", "VP", "WTI", "YOY",
})

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

        # 2b. Headline-mentioned uppercase tokens (last 24h, ≥2 articles).
        # Catches tickers that aren't yet in the universe and therefore can't
        # show up via NEWS_TRENDING (which depends on NewsTickerRelevance rows).
        headline_mentions = await self._get_headline_mention_tickers()
        for ticker in headline_mentions:
            raw.append(({"symbol": ticker, "name": "", "price": None, "change": None, "change_pct": None}, "HEADLINE_MENTION"))

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

    async def _get_headline_mention_tickers(self) -> list[str]:
        """Extract uppercase tokens from recent headlines that look like tickers.

        Scans MarketNews from the last HEADLINE_MENTION_LOOKBACK_HOURS hours,
        regex-matches 2-5 char uppercase tokens, drops common English / financial
        stop-tokens, and returns tokens that appear in at least
        HEADLINE_MENTION_MIN_ARTICLES distinct articles.

        Tokens already in `universe_stocks` (active) or already PENDING in
        `discovery_candidates` are filtered out here to avoid wasting yfinance
        validation calls downstream — the canonical exclusion still runs in
        `discover()` via `_get_existing_tickers`.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=HEADLINE_MENTION_LOOKBACK_HOURS)
        try:
            result = await self._session.execute(
                select(MarketNews.id, MarketNews.headline).where(
                    MarketNews.published_at >= cutoff,
                    MarketNews.headline.isnot(None),
                )
            )
            rows = result.all()
        except Exception:
            logger.exception("Failed to query recent headlines for mention extraction")
            return []

        if not rows:
            return []

        existing = await self._get_existing_tickers()
        # token -> set of news_ids it appears in
        token_articles: dict[str, set[int]] = {}
        for news_id, headline in rows:
            if not headline:
                continue
            for tok in _HEADLINE_TOKEN_RE.findall(headline):
                if tok in _HEADLINE_STOP_TOKENS:
                    continue
                if tok in existing:
                    continue
                token_articles.setdefault(tok, set()).add(news_id)

        mentioned = [
            tok for tok, ids in token_articles.items()
            if len(ids) >= HEADLINE_MENTION_MIN_ARTICLES
        ]
        # Sort by descending article count so the most-mentioned tokens
        # get priority within MAX_CANDIDATES_PER_RUN.
        mentioned.sort(key=lambda t: len(token_articles[t]), reverse=True)
        if mentioned:
            counts = Counter({t: len(token_articles[t]) for t in mentioned})
            logger.info(
                "Headline-mention discovery: %d candidate tokens — top: %s",
                len(mentioned), counts.most_common(10),
            )
        return mentioned
