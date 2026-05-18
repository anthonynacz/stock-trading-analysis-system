"""Intraday news polling + targeted recommendation rescoring.

Runs hourly during market hours (08:15–16:15 ET, Mon–Fri). For each tick:

  1. Fetches news from the last ~75 minutes for the active watchlist.
     (NewsScanner dedup keys on source_url so the 15-min overlap with the
     prior tick is free.)
  2. Filters NewsTickerRelevance rows to a "material" subset using a
     symmetric sentiment threshold (negative news triggers rescore equally).
  3. For each ticker that has a Recommendation row today and a material
     headline since the last run, calls engine.analyze_single() and
     persist_revision() with a +5 conviction-delta guard so cosmetic
     refreshes don't churn `revised_at`.

Skipped if the main pipeline is currently running, to avoid colliding
with the post-market `recommendations` phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import (
    MarketNews,
    NewsTickerRelevance,
    Recommendation,
    UniverseStock,
    Watchlist,
)
from services.analyst_tracker import AnalystTracker
from services.earnings_calendar import EarningsCalendarService
from services.news_scanner import NewsScanner
from services.options_analyzer import OptionsAnalyzer
from services.recommendation_engine import RecommendationEngine
from services.run_log import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    record_run_finish,
    record_run_start,
)
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)


# Materiality thresholds. Symmetric: |sentiment_score| is used everywhere so
# negative news triggers a rescore with the same magnitude as positive news.
SENTIMENT_THRESHOLD_DEFAULT = 0.4
SENTIMENT_THRESHOLD_CATEGORY = 0.3
RELEVANCE_THRESHOLD = 0.7
MATERIAL_CATEGORIES = {"EARNINGS", "ANALYST", "GEOPOLITICAL"}

# Lookback for the news scan. 75 min > 60 min so the 15-min overlap with the
# prior tick absorbs upstream lag; NewsScanner dedups on source_url.
NEWS_LOOKBACK_MINUTES = 75

# Persist guard: only write a revision if action changes or |Δconviction| ≥ 5.
CONVICTION_DELTA_THRESHOLD = 5.0


@dataclass(frozen=True)
class _NewsTrigger:
    """One material (ticker, news) pair, carried through to the rescore step."""
    ticker: str
    news_id: int
    headline: str
    impact_level: str | None
    sentiment_score: float
    category: str | None


def filter_material_news(
    news_items: list[MarketNews],
    relevance_rows: list[NewsTickerRelevance],
) -> list[_NewsTrigger]:
    """Pure-function materiality filter — easy to unit test.

    A (ticker, article) pair is material when ANY of:
      - impact_level == HIGH
      - relevance_score ≥ RELEVANCE_THRESHOLD AND |sentiment| ≥ SENTIMENT_THRESHOLD_DEFAULT
      - category ∈ MATERIAL_CATEGORIES AND |sentiment| ≥ SENTIMENT_THRESHOLD_CATEGORY

    The sentiment check is symmetric on |score| so positive surprise and
    negative shock both fire.
    """
    by_id: dict[int, MarketNews] = {n.id: n for n in news_items}
    triggers: dict[tuple[str, int], _NewsTrigger] = {}

    for rel in relevance_rows:
        news = by_id.get(rel.news_id)
        if news is None:
            continue
        sentiment = float(news.sentiment_score or 0)
        abs_sent = abs(sentiment)
        relevance = float(rel.relevance_score or 0)
        impact = news.impact_level
        category = news.category

        is_material = (
            impact == "HIGH"
            or (relevance >= RELEVANCE_THRESHOLD and abs_sent >= SENTIMENT_THRESHOLD_DEFAULT)
            or (category in MATERIAL_CATEGORIES and abs_sent >= SENTIMENT_THRESHOLD_CATEGORY)
        )
        if not is_material:
            continue

        key = (rel.ticker, rel.news_id)
        if key in triggers:
            continue
        triggers[key] = _NewsTrigger(
            ticker=rel.ticker,
            news_id=rel.news_id,
            headline=news.headline or "",
            impact_level=impact,
            sentiment_score=sentiment,
            category=category,
        )

    return list(triggers.values())


def _pick_top_trigger(triggers: list[_NewsTrigger]) -> _NewsTrigger:
    """Pick the most-impactful trigger for a ticker to name in revision_reason.

    Preference: HIGH impact > category match > raw |sentiment|.
    """
    impact_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, None: 3}
    return max(
        triggers,
        key=lambda t: (
            -impact_rank.get(t.impact_level, 3),
            t.category in MATERIAL_CATEGORIES,
            abs(t.sentiment_score),
        ),
    )


async def _has_today_recommendation(session: AsyncSession, ticker: str) -> bool:
    from datetime import date as _date
    result = await session.execute(
        select(Recommendation.id).where(
            Recommendation.recommendation_date == _date.today(),
            Recommendation.ticker == ticker,
        ).limit(1)
    )
    return result.scalar() is not None


async def _active_watchlist_tickers(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(Watchlist.ticker).where(Watchlist.is_active.is_(True))
    )
    return [r[0] for r in result.all()]


async def _ticker_company_map(
    session: AsyncSession, tickers: list[str]
) -> dict[str, str | None]:
    """Build the ticker→company name map NewsScanner needs for relevance scoring."""
    result = await session.execute(
        select(UniverseStock.ticker, UniverseStock.company_name)
        .where(UniverseStock.is_active.is_(True))
    )
    mapping: dict[str, str | None] = {row[0]: row[1] for row in result.all()}
    # Watchlist tickers fall back to their watchlist company_name if absent.
    wl_result = await session.execute(
        select(Watchlist.ticker, Watchlist.company_name).where(
            Watchlist.is_active.is_(True)
        )
    )
    for tk, name in wl_result.all():
        mapping.setdefault(tk, name)
    for tk in tickers:
        mapping.setdefault(tk, None)
    return mapping


async def run_intraday_news_scan() -> dict:
    """APScheduler entry point. Returns a stats dict for logging/tests."""
    # Avoid colliding with the post-market `recommendations` phase. The
    # daily pipeline owns the canonical rewrite; intraday only revises
    # between pipeline runs.
    from services.scheduler import last_refresh, pipeline_run
    if pipeline_run.get("status") == "running":
        logger.info("Intraday news scan skipped: main pipeline is running")
        return {"skipped": True, "reason": "pipeline_running"}

    run_id = await record_run_start(phase="intraday_news")
    session = async_session()
    data_client = DataSourceClient()
    stats = {
        "fetched_news": 0,
        "material_triggers": 0,
        "tickers_rescored": 0,
        "persisted": 0,
        "skipped_no_rec": 0,
        "skipped_delta": 0,
    }
    error: str | None = None

    try:
        active_tickers = await _active_watchlist_tickers(session)
        if not active_tickers:
            logger.info("Intraday news scan: empty watchlist, nothing to do")
            return {**stats, "skipped": True, "reason": "empty_watchlist"}

        ticker_company_map = await _ticker_company_map(session, active_tickers)
        scanner = NewsScanner(session, data_client)

        # Step 1: fetch + persist new MarketNews + NewsTickerRelevance rows.
        # NewsScanner dedups against existing source_url, so the 15-min
        # overlap window is free.
        new_items = await scanner.scan_news(active_tickers, ticker_company_map)
        stats["fetched_news"] = len(new_items)

        if not new_items:
            logger.info("Intraday news scan: no new headlines in last %dmin", NEWS_LOOKBACK_MINUTES)
            return {**stats, "skipped": False}

        # Step 2: pull relevance rows for the just-inserted news and run the
        # materiality filter. We re-query rather than relying on the
        # session-cached `ticker_relevances` because new_items has a fresh
        # collection state and some may have been backfill-only (no new rows).
        news_ids = [n.id for n in new_items if n.id is not None]
        if not news_ids:
            return {**stats, "skipped": False}

        rel_result = await session.execute(
            select(NewsTickerRelevance).where(
                NewsTickerRelevance.news_id.in_(news_ids),
                NewsTickerRelevance.ticker.in_(active_tickers),
            )
        )
        relevance_rows = list(rel_result.scalars().all())
        triggers = filter_material_news(new_items, relevance_rows)
        stats["material_triggers"] = len(triggers)

        if not triggers:
            logger.info(
                "Intraday news scan: %d headlines, no material triggers",
                len(new_items),
            )
            return {**stats, "skipped": False}

        # Step 3: group triggers by ticker, filter to those with a rec today.
        by_ticker: dict[str, list[_NewsTrigger]] = {}
        for t in triggers:
            by_ticker.setdefault(t.ticker, []).append(t)

        # Step 4: rescore each affected ticker serially. analyze_single() is
        # async but does sync data-source calls under run_in_executor, so
        # serial dispatch keeps DB connection count + yfinance pressure bounded.
        analyst_tracker = AnalystTracker(session, data_client)
        earnings_svc = EarningsCalendarService(session, data_client)
        options_analyzer = OptionsAnalyzer(session, data_client)
        engine = RecommendationEngine(
            session, data_client, analyst_tracker, earnings_svc, scanner, options_analyzer,
        )

        for ticker, ticker_triggers in by_ticker.items():
            if not await _has_today_recommendation(session, ticker):
                stats["skipped_no_rec"] += 1
                continue
            try:
                result = await engine.analyze_single(ticker)
                top = _pick_top_trigger(ticker_triggers)
                reason = (
                    f"intraday_news[{top.category or 'NEWS'}/{top.impact_level or 'LOW'}]: "
                    f"{top.headline[:160]}"
                )
                rec = await engine.persist_revision(
                    ticker, result,
                    reason=reason,
                    min_conviction_delta=CONVICTION_DELTA_THRESHOLD,
                )
                stats["tickers_rescored"] += 1
                if rec is not None:
                    stats["persisted"] += 1
                else:
                    stats["skipped_delta"] += 1
            except Exception:
                logger.exception("Intraday rescore failed for %s", ticker)

        logger.info(
            "Intraday news scan complete: %d new news, %d triggers, "
            "%d rescored, %d persisted, %d skipped(no-rec), %d skipped(delta)",
            stats["fetched_news"], stats["material_triggers"],
            stats["tickers_rescored"], stats["persisted"],
            stats["skipped_no_rec"], stats["skipped_delta"],
        )
        return {**stats, "skipped": False}

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("Intraday news scan failed")
        try:
            await session.rollback()
        except Exception:
            pass
        return {**stats, "error": error, "skipped": False}
    finally:
        data_client.close()
        await session.close()
        # Bump the freshness indicator on every successful run, not just when
        # rows were persisted. The point of last_refresh.intraday_news is
        # "scan ran successfully" — an empty-result scan is still a success
        # (the upstream APIs answered, we just didn't find new material).
        if error is None:
            last_refresh["intraday_news"] = datetime.now(tz=timezone.utc)
        await record_run_finish(
            run_id,
            status=STATUS_FAILED if error else STATUS_SUCCESS,
            error_message=error,
            meta=stats,
        )
