"""Deep-news + bull/bear/watch enrichment for research results.

Runs after `RecommendationEngine.analyze_single` produces the standard scoring
output. Pulls 14 days of ticker-relevant news, clusters by category, builds a
daily sentiment timeline, picks the highest-impact verbatim headlines, then
makes two Claude calls — one for a narrative paragraph, one for a structured
bull / bear / watch trio. Both calls share a cached prefix, so the second
hits prompt cache for ~90% input cost reduction.

The whole flow is best-effort: any individual stage (extended news fetch,
clustering, narrative LLM call, thesis LLM call) can fail and the rest still
ships. The caller writes ``enrichment_status`` based on what succeeded:

- ``COMPLETE``  — clusters + narrative + thesis trio all present
- ``PARTIAL``   — clusters present, one or both LLM calls missing
- ``FAILED``    — clusters themselves failed (no news, DB error, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MarketNews, NewsTickerRelevance
from services.llm import LLMUnavailable, agenerate_structured, agenerate_text
from utils.data_sources import DataSourceClient
from utils.finbert import score_batch
from utils.scoring import classify_news_category

logger = logging.getLogger(__name__)


LOOKBACK_DAYS = 14
TOP_HEADLINE_COUNT = 8
MIN_RELEVANCE = 0.3

# Source-quality buckets. Primary press / regulatory > major financial press >
# analyst notes > aggregator rewrites. Driven by substring match against the
# `source` field on MarketNews.
_PRIMARY_SOURCES = (
    "businesswire", "globenewswire", "pr newswire", "prnewswire",
    "sec filing", "sec.gov", "edgar", "company press",
)
_MAJOR_PRESS = (
    "reuters", "bloomberg", "wall street journal", "wsj",
    "financial times", "ft.com", "cnbc", "barron",
    "the new york times", "nytimes",
)
_ANALYST_NOTES = (
    "seeking alpha", "zacks", "motley fool", "morningstar", "tipranks",
)


def _source_quality(source: str | None) -> str:
    if not source:
        return "OTHER"
    s = source.lower()
    if any(p in s for p in _PRIMARY_SOURCES):
        return "PRIMARY"
    if any(p in s for p in _MAJOR_PRESS):
        return "MAJOR_PRESS"
    if any(p in s for p in _ANALYST_NOTES):
        return "ANALYST"
    return "AGGREGATOR"


# ── Data shapes ────────────────────────────────────────────────────────────


@dataclass
class NewsClusters:
    """Output of the deduplication / categorization pass."""

    articles: list[dict[str, Any]] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)
    by_source_quality: dict[str, int] = field(default_factory=dict)
    article_count_14d: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "by_category": self.by_category,
            "by_source_quality": self.by_source_quality,
            "article_count_14d": self.article_count_14d,
        }


# ── News pass: fetch, score, cluster ───────────────────────────────────────


async def _fetch_relevant_news(
    db: AsyncSession, ticker: str, lookback_days: int
) -> list[MarketNews]:
    """Pull every news article tagged to *ticker* in the relevance junction
    table over the last *lookback_days* days. Falls back to direct
    ``MarketNews.ticker == ticker`` matches when no relevance rows exist
    (covers research runs on tickers that aren't in the universe yet)."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    stmt = (
        select(MarketNews)
        .join(NewsTickerRelevance, NewsTickerRelevance.news_id == MarketNews.id)
        .where(
            NewsTickerRelevance.ticker == ticker,
            NewsTickerRelevance.relevance_score >= Decimal(str(MIN_RELEVANCE)),
            MarketNews.published_at >= cutoff,
        )
        .order_by(MarketNews.published_at.desc())
        .limit(150)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().unique().all())
    if rows:
        return rows

    # Fallback: untagged articles where MarketNews.ticker == this ticker.
    fallback = await db.execute(
        select(MarketNews)
        .where(
            MarketNews.ticker == ticker,
            MarketNews.published_at >= cutoff,
        )
        .order_by(MarketNews.published_at.desc())
        .limit(150)
    )
    return list(fallback.scalars().unique().all())


def _build_clusters(rows: list[MarketNews]) -> NewsClusters:
    """Group articles by category and source-quality bucket. Picks the top-N
    headlines by ``impact_level`` then recency."""
    impact_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    sorted_rows = sorted(
        rows,
        key=lambda n: (
            impact_rank.get(n.impact_level or "LOW", 3),
            -(n.published_at.timestamp() if n.published_at else 0),
        ),
    )

    by_category: dict[str, int] = defaultdict(int)
    by_quality: dict[str, int] = defaultdict(int)
    articles: list[dict[str, Any]] = []
    for n in sorted_rows:
        cat = n.category or classify_news_category(n.headline or "")
        quality = _source_quality(n.source)
        by_category[cat] += 1
        by_quality[quality] += 1
        if len(articles) < TOP_HEADLINE_COUNT:
            articles.append({
                "headline": n.headline,
                "summary": (n.summary or "")[:400],
                "source": n.source,
                "source_quality": quality,
                "category": cat,
                "sentiment_score": float(n.sentiment_score) if n.sentiment_score is not None else None,
                "impact_level": n.impact_level,
                "published_at": n.published_at.isoformat() if n.published_at else None,
                "url": n.source_url,
            })

    return NewsClusters(
        articles=articles,
        by_category=dict(by_category),
        by_source_quality=dict(by_quality),
        article_count_14d=len(rows),
    )


def _sentiment_timeline(rows: list[MarketNews]) -> list[dict[str, Any]]:
    """Daily mean FinBERT sentiment + article count across the lookback
    window. Returns a list ordered by date ascending."""
    buckets: dict[date, list[float]] = defaultdict(list)
    for n in rows:
        if not n.published_at or n.sentiment_score is None:
            continue
        d = n.published_at.date()
        buckets[d].append(float(n.sentiment_score))
    out: list[dict[str, Any]] = []
    for d in sorted(buckets):
        scores = buckets[d]
        out.append({
            "date": d.isoformat(),
            "mean_sentiment": round(statistics.fmean(scores), 3),
            "article_count": len(scores),
        })
    return out


# ── LLM prompts ────────────────────────────────────────────────────────────


_ANALYST_PERSONA = (
    "You are a senior buy-side equity analyst writing for a sophisticated "
    "trader. Use precise financial language. Reference concrete signals, "
    "numbers, and headlines from the provided context — never invent figures. "
    "Do not include disclaimers. Do not start responses with phrases like "
    "'Here is...' or 'Based on...'."
)


def _build_context(
    ticker: str,
    company_name: str | None,
    research: dict[str, Any],
    clusters: NewsClusters,
    timeline: list[dict[str, Any]],
) -> str:
    """Render the structured market data as plain text for the LLM. Stable
    layout so prompt caching keeps the prefix intact across the two calls."""
    signals_summary = "\n".join(
        f"  - {s.get('signal', '')} ({s.get('points', 0):+d})"
        + (f": {s.get('detail')}" if s.get("detail") else "")
        for s in (research.get("signals") or [])[:25]
    ) or "  (no signals fired)"

    options_summary = "  (no options snapshot)"
    od = research.get("options_data") or {}
    if od:
        options_summary = (
            f"  - Stock price: {od.get('stock_price')}\n"
            f"  - IV rank: {od.get('iv_rank')}\n"
            f"  - IV percentile: {od.get('iv_percentile')}\n"
            f"  - Put/call ratio: {od.get('put_call_ratio')}\n"
            f"  - Call volume: {od.get('total_call_volume')}\n"
            f"  - Put volume: {od.get('total_put_volume')}\n"
            f"  - Unusual activity: {od.get('unusual_activity')}"
        )

    headlines_block = "\n".join(
        f"  [{a['published_at'][:10] if a['published_at'] else '?'}] "
        f"({a['category']}, {a['source_quality']}, sent={a['sentiment_score']:+0.2f}) "
        f"{a['headline']}"
        + (f"\n    → {a['summary'][:200]}" if a["summary"] else "")
        for a in clusters.articles
        if a["sentiment_score"] is not None
    ) or "  (no recent headlines)"

    timeline_block = ", ".join(
        f"{p['date'][5:]}={p['mean_sentiment']:+0.2f}(n={p['article_count']})"
        for p in timeline[-14:]
    ) or "(no daily timeline)"

    return f"""TICKER: {ticker}
COMPANY: {company_name or 'Unknown'}
CURRENT PRICE: {research.get('current_price')}
ANALYST PT: {research.get('target_price')}
STOP-LOSS: {research.get('stop_loss_price')}
ACTION: {research.get('action')} (conviction {research.get('conviction_score')})
CATALYST TYPE: {research.get('catalyst_type')}
ENTRY STRATEGY: {research.get('entry_strategy')}
RISK LEVEL: {research.get('risk_level')}

SCORING ENGINE RATIONALE:
{research.get('rationale') or '(none)'}

SIGNALS FIRED ({len(research.get('signals') or [])} total):
{signals_summary}

OPTIONS SNAPSHOT:
{options_summary}

NEWS COUNTS (last 14 days):
  By category: {dict(clusters.by_category)}
  By source quality: {dict(clusters.by_source_quality)}
  Total articles: {clusters.article_count_14d}

DAILY SENTIMENT TIMELINE (FinBERT, -1 to +1):
  {timeline_block}

TOP HEADLINES (most impactful, last 14 days):
{headlines_block}
"""


_NARRATIVE_QUESTION = (
    "Write a single-paragraph narrative summary (180-220 words) of what is "
    "happening with this stock right now. Synthesize the news flow into a "
    "coherent story — what changed, why it matters, who's positioning, what "
    "the market is reacting to. Cite specific headlines or signals when you "
    "reference them. Avoid generic finance-speak; be concrete and opinionated."
)


_THESIS_QUESTION = (
    "Produce three concise paragraphs as JSON: a bull case, a bear case, and "
    "a 'what to watch' list of upcoming catalysts and signals to monitor. "
    "Each paragraph should be 80-120 words, dense with specifics from the "
    "context above. The bull and bear cases should be genuinely opposing — "
    "don't hedge. 'What to watch' should name 3-5 concrete events or "
    "indicators (earnings dates, technical levels, catalyst windows, options "
    "thresholds), not vague guidance."
)

_THESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "watch_text": {"type": "string"},
    },
    "required": ["bull_case", "bear_case", "watch_text"],
    "additionalProperties": False,
}


# ── Public entrypoint ──────────────────────────────────────────────────────


@dataclass
class EnrichmentResult:
    status: str  # COMPLETE / PARTIAL / FAILED
    error: str | None
    news_summary: str | None
    news_clusters: dict[str, Any] | None
    sentiment_timeline: list[dict[str, Any]] | None
    top_headlines: list[dict[str, Any]] | None
    bull_case: str | None
    bear_case: str | None
    watch_text: str | None


async def enrich(
    db: AsyncSession,
    ticker: str,
    company_name: str | None,
    research: dict[str, Any],
    data_client: DataSourceClient,
) -> EnrichmentResult:
    """Full enrichment pass. Idempotent; safe to call concurrently."""
    # 1. Refresh ticker news beyond the daily 1d window. The standard pipeline
    # already called scan_news for today; this extends the view to 14 days
    # by pulling whatever is already in the DB. We could also re-fetch from
    # FMP/NewsAPI here, but the per-source rate budget is tight and the live
    # data path already runs in the route's pre-enrichment step.
    try:
        rows = await _fetch_relevant_news(db, ticker, LOOKBACK_DAYS)
    except Exception as e:
        logger.exception("Enrichment news fetch failed for %s", ticker)
        return EnrichmentResult(
            status="FAILED",
            error=f"news_fetch_failed: {e}",
            news_summary=None,
            news_clusters=None,
            sentiment_timeline=None,
            top_headlines=None,
            bull_case=None,
            bear_case=None,
            watch_text=None,
        )

    clusters = _build_clusters(rows)
    timeline = _sentiment_timeline(rows)

    # No news at all → still return cluster shape (empty) so the UI renders
    # the section with a "no recent news" state instead of nothing.
    if not rows:
        return EnrichmentResult(
            status="PARTIAL",
            error="no_news_in_window",
            news_summary=None,
            news_clusters=clusters.to_json(),
            sentiment_timeline=[],
            top_headlines=[],
            bull_case=None,
            bear_case=None,
            watch_text=None,
        )

    context = _build_context(ticker, company_name, research, clusters, timeline)

    # 2. Run the two LLM calls concurrently. The second hits the cached prefix
    # the first one wrote on the same context.
    narrative_text: str | None = None
    bull = bear = watch = None
    partial_errors: list[str] = []

    try:
        narrative_task = agenerate_text(
            persona=_ANALYST_PERSONA,
            context=context,
            question=_NARRATIVE_QUESTION,
            max_tokens=600,
        )
        thesis_task = agenerate_structured(
            persona=_ANALYST_PERSONA,
            context=context,
            question=_THESIS_QUESTION,
            schema=_THESIS_SCHEMA,
            max_tokens=1500,
        )
        results = await asyncio.gather(narrative_task, thesis_task, return_exceptions=True)
    except LLMUnavailable as e:
        # No API key — entire LLM path is skipped, news cluster still ships.
        return EnrichmentResult(
            status="PARTIAL",
            error=f"llm_unavailable: {e}",
            news_summary=None,
            news_clusters=clusters.to_json(),
            sentiment_timeline=timeline,
            top_headlines=clusters.articles,
            bull_case=None,
            bear_case=None,
            watch_text=None,
        )

    narrative_result, thesis_result = results

    if isinstance(narrative_result, Exception):
        logger.warning("Narrative LLM call failed for %s: %s", ticker, narrative_result)
        partial_errors.append(f"narrative: {narrative_result}")
    elif isinstance(narrative_result, tuple):
        text, usage = narrative_result
        narrative_text = text
        logger.info(
            "Narrative for %s: in=%d out=%d cache_create=%d cache_read=%d",
            ticker,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_creation_input_tokens"],
            usage["cache_read_input_tokens"],
        )

    if isinstance(thesis_result, Exception):
        logger.warning("Thesis LLM call failed for %s: %s", ticker, thesis_result)
        partial_errors.append(f"thesis: {thesis_result}")
    elif isinstance(thesis_result, tuple):
        parsed, usage = thesis_result
        bull = parsed.get("bull_case")
        bear = parsed.get("bear_case")
        watch = parsed.get("watch_text")
        logger.info(
            "Thesis for %s: in=%d out=%d cache_create=%d cache_read=%d",
            ticker,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_creation_input_tokens"],
            usage["cache_read_input_tokens"],
        )

    if narrative_text and bull and bear and watch:
        status = "COMPLETE"
        err = None
    else:
        status = "PARTIAL"
        err = "; ".join(partial_errors) if partial_errors else None

    return EnrichmentResult(
        status=status,
        error=err,
        news_summary=narrative_text,
        news_clusters=clusters.to_json(),
        sentiment_timeline=timeline,
        top_headlines=clusters.articles,
        bull_case=bull,
        bear_case=bear,
        watch_text=watch,
    )
