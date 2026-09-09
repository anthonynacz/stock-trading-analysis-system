"""Breaking-news feed: material headlines for the tickers a user cares about.

Reuses the same materiality rule as the intraday rescore
(`intraday_news.filter_material_news`) so the Dashboard strip shows exactly
the headlines that can move a recommendation, and attaches today's
recommendation (with its revision diff when that headline triggered a
rescore) plus whether the user holds an open position in the ticker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    MarketNews,
    NewsTickerRelevance,
    Position,
    Recommendation,
    Watchlist,
)
from services.intraday_news import filter_material_news

# Must match the prefix written by intraday_news.run_intraday_news_scan.
REVISION_PREFIX = "intraday_news["
HEADLINE_REASON_CHARS = 160


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def rec_summary(rec: Recommendation | None, headline: str) -> dict[str, Any] | None:
    """Today's recommendation for a ticker, flagged when `headline` is the
    story that produced its latest revision."""
    if rec is None:
        return None
    reason = rec.revision_reason or ""
    triggered = (
        reason.startswith(REVISION_PREFIX)
        and bool(headline)
        and headline[:HEADLINE_REASON_CHARS] in reason
    )
    return {
        "id": rec.id,
        "action": rec.action,
        "conviction_score": _num(rec.conviction_score),
        "prior_action": rec.prior_action if triggered else None,
        "prior_conviction_score": _num(rec.prior_conviction_score) if triggered else None,
        "revised_at": rec.revised_at.isoformat() if (triggered and rec.revised_at) else None,
        "triggered_revision": triggered,
    }


def build_breaking_items(
    news_items: list[MarketNews],
    relevance_rows: list[NewsTickerRelevance],
    recs_by_ticker: dict[str, Recommendation],
    held_tickers: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Pure assembly step (unit-tested): one entry per material headline,
    newest first, with the tickers it is material for."""
    triggers = filter_material_news(news_items, relevance_rows)
    by_news: dict[int, list] = {}
    for t in triggers:
        by_news.setdefault(t.news_id, []).append(t)

    rel_score = {(r.ticker, r.news_id): float(r.relevance_score or 0) for r in relevance_rows}
    news_by_id = {n.id: n for n in news_items}
    items: list[dict[str, Any]] = []
    for news_id, ts in by_news.items():
        news = news_by_id[news_id]
        tickers = sorted({t.ticker for t in ts}, key=lambda tk: -rel_score.get((tk, news_id), 0))
        items.append({
            "id": news.id,
            "headline": news.headline,
            "source": news.source,
            "source_url": news.source_url,
            "category": news.category,
            "impact_level": news.impact_level,
            "sentiment_score": _num(news.sentiment_score),
            "published_at": news.published_at.isoformat() if news.published_at else None,
            "tickers": [
                {
                    "ticker": tk,
                    "relevance_score": rel_score.get((tk, news_id), 0.0),
                    "held": tk in held_tickers,
                    "recommendation": rec_summary(recs_by_ticker.get(tk), news.headline or ""),
                }
                for tk in tickers
            ],
        })

    def sort_key(it: dict[str, Any]):
        held = any(t["held"] for t in it["tickers"])
        return (it["published_at"] or "", held)

    items.sort(key=sort_key, reverse=True)
    return items[:limit]


async def get_breaking_news(
    session: AsyncSession, user_id: int | None, hours: int, limit: int
) -> list[dict[str, Any]]:
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(hours=hours)

    wl = await session.execute(select(Watchlist.ticker).where(Watchlist.is_active.is_(True)))
    watched = {r[0] for r in wl.all()}
    held: set[str] = set()
    if user_id is not None:
        pos = await session.execute(
            select(Position.ticker).where(Position.user_id == user_id, Position.status == "OPEN")
        )
        held = {r[0] for r in pos.all()}
    scope = watched | held
    if not scope:
        return []

    res = await session.execute(
        select(MarketNews)
        .join(NewsTickerRelevance, NewsTickerRelevance.news_id == MarketNews.id)
        .where(
            NewsTickerRelevance.ticker.in_(scope),
            MarketNews.published_at >= cutoff,
        )
        .options(selectinload(MarketNews.ticker_relevances))
        .order_by(MarketNews.published_at.desc())
    )
    news_items = list(res.scalars().unique().all())
    if not news_items:
        return []
    relevance_rows = [
        r for n in news_items for r in (n.ticker_relevances or []) if r.ticker in scope
    ]

    tickers = {r.ticker for r in relevance_rows}
    rec_res = await session.execute(
        select(Recommendation).where(
            Recommendation.recommendation_date == now.date(),
            Recommendation.ticker.in_(tickers),
        )
    )
    recs_by_ticker = {r.ticker: r for r in rec_res.scalars().all()}

    return build_breaking_items(news_items, relevance_rows, recs_by_ticker, held, limit)
