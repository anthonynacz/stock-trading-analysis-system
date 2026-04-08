"""APScheduler-based daily workflow scheduler for EdgeFlow.

Orchestrates all service phases on a market-hours cron schedule
(US/Eastern timezone, Monday-Friday only).
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db.connection import async_session
from services.analyst_tracker import AnalystTracker
from services.earnings_calendar import EarningsCalendarService
from services.news_scanner import NewsScanner
from services.options_analyzer import OptionsAnalyzer
from services.recommendation_engine import RecommendationEngine
from services.watchlist_manager import WatchlistManager
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

scheduler: AsyncIOScheduler | None = None

last_refresh: dict[str, datetime | None] = {
    "watchlist": None,
    "ratings": None,
    "earnings": None,
    "news": None,
    "options": None,
    "recommendations": None,
}

EASTERN = pytz.timezone("US/Eastern")


# ---------------------------------------------------------------------------
# Pipeline phase runner
# ---------------------------------------------------------------------------

async def run_pipeline_phase(phase: str) -> None:
    """Create a fresh DB session and data client, run the named phase, then clean up."""
    logger.info("Starting pipeline phase: %s", phase)
    session = async_session()
    data_client = DataSourceClient()
    try:
        # Instantiate services needed for this phase
        watchlist_mgr = WatchlistManager(session, data_client)

        if phase == "watchlist":
            result = await watchlist_mgr.rotate_watchlist()
            logger.info("Watchlist rotation complete: %s", result)

        elif phase == "ratings":
            active = await watchlist_mgr.get_active_watchlist()
            active_tickers = [w.ticker for w in active]
            tracker = AnalystTracker(session, data_client)
            new_ratings = await tracker.scan_ratings(active_tickers)
            logger.info("Ratings scan complete: %d new ratings", len(new_ratings))

        elif phase == "earnings":
            active = await watchlist_mgr.get_active_watchlist()
            active_tickers = [w.ticker for w in active]
            earnings_svc = EarningsCalendarService(session, data_client)
            count = await earnings_svc.refresh_calendar(active_tickers)
            logger.info("Earnings calendar refresh complete: %d records upserted", count)

        elif phase == "news":
            active = await watchlist_mgr.get_active_watchlist()
            active_tickers = [w.ticker for w in active]
            ticker_company_map = {w.ticker: w.company_name for w in active}
            scanner = NewsScanner(session, data_client)
            new_items = await scanner.scan_news(active_tickers, ticker_company_map)
            logger.info("News scan complete: %d new items", len(new_items))

        elif phase == "options":
            active = await watchlist_mgr.get_active_watchlist()
            active_tickers = [w.ticker for w in active]
            analyzer = OptionsAnalyzer(session, data_client)
            snapshots = await analyzer.analyze_all(active_tickers)
            logger.info("Options analysis complete: %d snapshots", len(snapshots))

        elif phase == "recommendations":
            active = await watchlist_mgr.get_active_watchlist()
            active_tickers = [w.ticker for w in active]
            tracker = AnalystTracker(session, data_client)
            earnings_svc = EarningsCalendarService(session, data_client)
            scanner = NewsScanner(session, data_client)
            analyzer = OptionsAnalyzer(session, data_client)
            engine = RecommendationEngine(
                session, data_client, tracker, earnings_svc, scanner, analyzer,
            )
            recs = await engine.generate_recommendations(active_tickers)
            logger.info("Recommendations generated: %d", len(recs))

        else:
            logger.error("Unknown pipeline phase: %s", phase)
            return

        await session.commit()
        last_refresh[phase] = datetime.now(tz=EASTERN)
        logger.info("Pipeline phase '%s' completed successfully", phase)

    except Exception:
        logger.exception("Pipeline phase '%s' failed", phase)
        await session.rollback()
    finally:
        data_client.close()
        await session.close()


# ---------------------------------------------------------------------------
# Full pipeline (manual refresh)
# ---------------------------------------------------------------------------

async def run_full_pipeline() -> None:
    """Run every phase in sequence. Used by the manual refresh endpoint."""
    start = datetime.now(tz=EASTERN)
    logger.info("Full pipeline started at %s", start.isoformat())

    phases = ["watchlist", "ratings", "earnings", "news", "options", "recommendations"]
    for phase in phases:
        await run_pipeline_phase(phase)

    end = datetime.now(tz=EASTERN)
    logger.info("Full pipeline finished at %s (elapsed %s)", end.isoformat(), end - start)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Create, configure, and start the AsyncIOScheduler."""
    global scheduler

    scheduler = AsyncIOScheduler(timezone=EASTERN)

    weekdays = "mon-fri"

    # -- Pre-market ----------------------------------------------------------
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["watchlist"],
        hour=5, minute=30, day_of_week=weekdays, id="premarket_watchlist",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["ratings"],
        hour=6, minute=0, day_of_week=weekdays, id="premarket_ratings",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["news"],
        hour=6, minute=30, day_of_week=weekdays, id="premarket_news",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["earnings"],
        hour=7, minute=0, day_of_week=weekdays, id="premarket_earnings",
    )

    # -- Market open ---------------------------------------------------------
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["options"],
        hour=9, minute=35, day_of_week=weekdays, id="open_options",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["news"],
        hour=10, minute=0, day_of_week=weekdays, id="open_news",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["recommendations"],
        hour=10, minute=30, day_of_week=weekdays, id="open_recommendations",
    )

    # -- Intraday ------------------------------------------------------------
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["ratings"],
        hour=11, minute=0, day_of_week=weekdays, id="intraday_ratings_midday",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["ratings"],
        hour=14, minute=0, day_of_week=weekdays, id="intraday_ratings_afternoon",
    )

    # -- Post-market ---------------------------------------------------------
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["options"],
        hour=16, minute=15, day_of_week=weekdays, id="postmarket_options",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["recommendations"],
        hour=16, minute=30, day_of_week=weekdays, id="postmarket_recommendations",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["news"],
        hour=16, minute=45, day_of_week=weekdays, id="postmarket_news",
    )
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["earnings"],
        hour=17, minute=0, day_of_week=weekdays, id="postmarket_earnings",
    )

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def shutdown_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
        scheduler = None


def get_scheduler_status() -> dict:
    """Return scheduler status for the health/status endpoint."""
    if scheduler is None:
        return {
            "running": False,
            "job_count": 0,
            "next_run_time": None,
            "last_refresh": last_refresh,
        }

    jobs = scheduler.get_jobs()
    next_run_times = [
        job.next_run_time for job in jobs if job.next_run_time is not None
    ]
    earliest = min(next_run_times).isoformat() if next_run_times else None

    return {
        "running": scheduler.running,
        "job_count": len(jobs),
        "next_run_time": earliest,
        "last_refresh": last_refresh,
    }
