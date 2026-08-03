"""APScheduler-based daily workflow scheduler for EdgeFlow.

Orchestrates all service phases on a market-hours cron schedule
(US/Eastern timezone, Monday-Friday only).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from db.connection import async_session
from db.models import PipelineRunLog, UniverseStock
from services.analyst_tracker import AnalystTracker
from services.earnings_calendar import EarningsCalendarService
from services.news_scanner import NewsScanner
from services.options_analyzer import OptionsAnalyzer
from services.recommendation_engine import RecommendationEngine
from services.universe_discoverer import UniverseDiscoverer
from services.watchlist_manager import WatchlistManager
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

scheduler: AsyncIOScheduler | None = None

last_refresh: dict[str, datetime | None] = {
    "discovery": None,
    "watchlist": None,
    "ratings": None,
    "earnings": None,
    "news": None,
    "options": None,
    "recommendations": None,
    "industries": None,
    "intraday_news": None,
}

EASTERN = pytz.timezone("US/Eastern")

ALL_PHASES = ["discovery", "watchlist", "ratings", "earnings", "news", "options", "recommendations", "industries"]

# Phases that are safe to run concurrently (independent data-fetchers,
# separate tables, no cross-reads).
PARALLEL_PHASES = {"ratings", "earnings", "news", "options"}

# Pipeline run tracking for frontend progress bar
pipeline_run: dict = {
    "status": "idle",        # idle | running | completed | failed
    "phases": [],            # phases requested
    "completed": [],         # phases finished so far
    "current": None,         # phase currently executing (or "ratings+earnings+..." for parallel)
    "started_at": None,
    "finished_at": None,
    "error": None,
}


# ---------------------------------------------------------------------------
# Custom universe injection
# ---------------------------------------------------------------------------

async def _inject_custom_universe(session) -> set[str]:
    """Read every user's `custom_universe` preference, dedupe globally, and
    ensure each ticker is present in today's active watchlist with
    `is_manual=True` (so it survives rotation).

    Returns the set of tickers that were inserted (existing entries are
    only flagged is_manual=True; the count of newly-added tickers is the
    "injected" cardinality).

    Architecture notes:
      - Watchlist is currently a globally shared table. So a custom-universe
        ticker added by user A becomes visible to user B in the watchlist UI.
        This matches the strategy doc's "globally cached / globally deduped"
        design — the cost is one pipeline run regardless of who added it.
        Per-user *visibility* of the watchlist is a separate (deferred)
        concern documented in CLAUDE.md.
      - Re-runs the same day are safe: existing rows are detected via
        is_active=True, so we don't violate uq_watchlist_ticker_date.
    """
    from datetime import date as _date
    from sqlalchemy import select
    from db.models import UserPreferences, Watchlist

    pref_result = await session.execute(select(UserPreferences))
    custom: set[str] = set()
    for pref in pref_result.scalars():
        for t in (pref.custom_universe or []):
            if isinstance(t, str) and t.strip():
                custom.add(t.strip().upper())

    if not custom:
        return set()

    existing_q = await session.execute(
        select(Watchlist).where(
            Watchlist.is_active.is_(True),
            Watchlist.ticker.in_(custom),
        )
    )
    existing = {w.ticker: w for w in existing_q.scalars()}

    today = _date.today()
    added: set[str] = set()
    for ticker in custom:
        if ticker in existing:
            # Re-affirm is_manual so the ticker is rotation-protected
            wl = existing[ticker]
            if not wl.is_manual:
                wl.is_manual = True
            continue
        session.add(Watchlist(
            ticker=ticker,
            added_date=today,
            is_active=True,
            is_manual=True,
            entry_reason="Custom universe slot",
        ))
        added.add(ticker)

    await session.commit()
    return added


# ---------------------------------------------------------------------------
# Pipeline phase runner
# ---------------------------------------------------------------------------

async def run_pipeline_phase(phase: str) -> None:
    """Create a fresh DB session and data client, run the named phase, then clean up."""
    from services.run_log import (
        STATUS_FAILED,
        STATUS_SUCCESS,
        record_run_finish,
        record_run_start,
    )

    logger.info("Starting pipeline phase: %s", phase)
    run_id = await record_run_start(phase=phase, user_id=None)
    phase_error: str | None = None
    session = async_session()
    data_client = DataSourceClient()
    try:
        # Instantiate services needed for this phase
        watchlist_mgr = WatchlistManager(session, data_client)

        if phase == "discovery":
            discoverer = UniverseDiscoverer(session, data_client)
            candidates = await discoverer.discover()
            logger.info("Discovery complete: %d new candidates", len(candidates))

        elif phase == "watchlist":
            result = await watchlist_mgr.rotate_watchlist()
            logger.info("Watchlist rotation complete: %s", result)
            injected = await _inject_custom_universe(session)
            if injected:
                logger.info("Custom universe injection: %d tickers added (%s)",
                            len(injected), sorted(injected))

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
            # Per-ticker news fetches stay scoped to the watchlist (rate-limit
            # bound on Finnhub /company-news), but relevance scoring expands to
            # every active universe member so that a headline mentioning a
            # non-watchlist ticker (e.g. AKAM in a "movers" article) still
            # produces a NewsTickerRelevance row for that ticker.
            universe_result = await session.execute(
                select(UniverseStock.ticker, UniverseStock.company_name)
                .where(UniverseStock.is_active.is_(True))
            )
            ticker_company_map: dict[str, str | None] = {
                row[0]: row[1] for row in universe_result.all()
            }
            for w in active:
                ticker_company_map.setdefault(w.ticker, w.company_name)
            scanner = NewsScanner(session, data_client)
            new_items = await scanner.scan_news(active_tickers, ticker_company_map)
            logger.info(
                "News scan complete: %d new items, scored against %d tickers",
                len(new_items), len(ticker_company_map),
            )

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

        elif phase == "industries":
            from services.industry_analyzer import IndustryAnalyzer
            industry_svc = IndustryAnalyzer(session, data_client)
            result = await industry_svc.run_and_persist()
            logger.info("Industry analysis complete: %s", result)

        else:
            logger.error("Unknown pipeline phase: %s", phase)
            return

        await session.commit()
        last_refresh[phase] = datetime.now(tz=EASTERN)
        logger.info("Pipeline phase '%s' completed successfully", phase)

    except Exception as exc:
        logger.exception("Pipeline phase '%s' failed", phase)
        phase_error = f"{type(exc).__name__}: {exc}"
        await session.rollback()
    finally:
        data_client.close()
        await session.close()
        await record_run_finish(
            run_id,
            status=STATUS_FAILED if phase_error else STATUS_SUCCESS,
            error_message=phase_error,
        )


# ---------------------------------------------------------------------------
# Full pipeline (manual refresh)
# ---------------------------------------------------------------------------

async def run_full_pipeline() -> None:
    """Run every phase in sequence. Used by the manual refresh endpoint."""
    await run_tracked_pipeline(ALL_PHASES)


async def run_tracked_pipeline(phases: list[str]) -> None:
    """Run selected phases with progress tracking.

    Consecutive parallelizable phases (ratings, earnings, news, options)
    are grouped and run concurrently via ``asyncio.gather``, each with
    its own DB session and DataSourceClient.  All other phases run
    sequentially in order.
    """
    global pipeline_run
    import asyncio

    pipeline_run = {
        "status": "running",
        "phases": phases,
        "completed": [],
        "current": None,
        "started_at": datetime.now(tz=EASTERN).isoformat(),
        "finished_at": None,
        "error": None,
    }

    start = datetime.now(tz=EASTERN)

    # Group consecutive parallelizable phases into batches.
    # E.g. [discovery, watchlist, ratings, earnings, news, options, recommendations]
    # becomes: [[discovery], [watchlist], [ratings, earnings, news, options], [recommendations]]
    batches: list[list[str]] = []
    for phase in phases:
        if phase in PARALLEL_PHASES:
            # Append to current parallel batch if the last batch is also parallel
            if batches and all(p in PARALLEL_PHASES for p in batches[-1]):
                batches[-1].append(phase)
            else:
                batches.append([phase])
        else:
            batches.append([phase])

    logger.info(
        "Tracked pipeline started: %s (%d batches, parallel: %s)",
        phases, len(batches),
        [b for b in batches if len(b) > 1],
    )

    try:
        for batch in batches:
            if len(batch) == 1:
                # Sequential phase
                phase = batch[0]
                pipeline_run["current"] = phase
                await run_pipeline_phase(phase)
                pipeline_run["completed"].append(phase)
            else:
                # Parallel batch
                pipeline_run["current"] = "+".join(batch)
                logger.info("Running parallel batch: %s", batch)

                async def _run_and_track(p: str) -> None:
                    """Wrapper that marks a phase complete as soon as it finishes."""
                    await run_pipeline_phase(p)
                    pipeline_run["completed"].append(p)
                    # Update current to only show still-running phases
                    remaining = [
                        x for x in batch
                        if x not in pipeline_run["completed"]
                    ]
                    pipeline_run["current"] = "+".join(remaining) if remaining else None

                results = await asyncio.gather(
                    *[_run_and_track(p) for p in batch],
                    return_exceptions=True,
                )

                # Log any failures but don't abort the whole pipeline
                for phase, result in zip(batch, results):
                    if isinstance(result, Exception):
                        logger.error("Parallel phase '%s' failed: %s", phase, result)
                        if phase not in pipeline_run["completed"]:
                            pipeline_run["completed"].append(phase)

        pipeline_run["status"] = "completed"
        pipeline_run["current"] = None
        pipeline_run["finished_at"] = datetime.now(tz=EASTERN).isoformat()
    except Exception as exc:
        pipeline_run["status"] = "failed"
        pipeline_run["error"] = str(exc)
        pipeline_run["finished_at"] = datetime.now(tz=EASTERN).isoformat()
        logger.exception("Tracked pipeline failed")

    end = datetime.now(tz=EASTERN)
    logger.info("Tracked pipeline finished at %s (elapsed %s)", end.isoformat(), end - start)


def get_pipeline_run_status() -> dict:
    """Return the current pipeline run state for the frontend."""
    return {**pipeline_run}


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
        run_pipeline_phase, "cron", args=["discovery"],
        hour=5, minute=0, day_of_week=weekdays, id="premarket_discovery",
    )
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
    # Industries reads today's recommendations — run after the 10:30 rec
    # generation has had time to finish.
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["industries"],
        hour=11, minute=15, day_of_week=weekdays, id="midday_industries",
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
    scheduler.add_job(
        run_pipeline_phase, "cron", args=["industries"],
        hour=17, minute=15, day_of_week=weekdays, id="postmarket_industries",
    )

    # -- Personalization workers ---------------------------------------------
    # Retention sweeper at 04:00 ET. Hard-deletes per-user research +
    # deep-options older than the user's tier retention_days. ADMIN/PREMIUM
    # are skipped (UNLIMITED).
    from services.retention import run_retention_sweep
    scheduler.add_job(
        run_retention_sweep, "cron",
        hour=4, minute=0, id="retention_sweep",
    )

    # AM digest dispatcher every 30 min between 06:00–13:00 ET. Iterates
    # users whose digest_config.send_time_utc falls in the past slot,
    # composes content, and dispatches to delivery (currently log stub).
    from services.digest import run_digest_dispatch
    scheduler.add_job(
        run_digest_dispatch, "cron",
        hour="6-13", minute="0,30", day_of_week=weekdays, id="digest_dispatch",
    )

    # Alerts trigger scan every 30 min during market hours. Scans for
    # rec changes / conviction breaches / unusual flow / earnings T-N
    # / news spikes / insider filings; dedup against alert_log.
    from services.alerts import run_alerts_scan
    scheduler.add_job(
        run_alerts_scan, "cron",
        hour="6-17", minute="*/30", day_of_week=weekdays, id="alerts_scan",
    )

    # Nightly portfolio P&L snapshot at 16:45 ET, Mon-Fri (after post-close
    # options refresh + recommendations regen, so the most recent prices are
    # already cached by yfinance). Writes one row per user per day.
    from services.pnl import run_pnl_snapshot
    scheduler.add_job(
        run_pnl_snapshot, "cron",
        hour=16, minute=45, day_of_week=weekdays, id="pnl_snapshot",
    )

    # Intraday news polling — hourly 08:15–16:15 ET, Mon–Fri (9 ticks/day).
    # Fetches new headlines, filters to material (impact/sentiment/category),
    # and reruns analyze_single() + persist_revision() only for tickers
    # whose news moved the calculus. Skips when the main pipeline is running.
    from services.intraday_news import run_intraday_news_scan
    scheduler.add_job(
        run_intraday_news_scan, "cron",
        hour="8-16", minute=15, day_of_week=weekdays, id="intraday_news_scan",
    )

    # Nightly recommendation outcome scoring at 18:00 ET — marks T+1/5/20
    # trading-day forward returns for every rec so per-signal hit rates can
    # be aggregated (GET /api/outcomes/summary).
    from services.outcome_tracker import run_outcome_scoring
    scheduler.add_job(
        run_outcome_scoring, "cron",
        hour=18, minute=0, day_of_week=weekdays, id="outcome_scoring",
    )

    # Weekly multibagger scan — Friday 17:30 ET, after the post-market chain,
    # so the week's closing data is fresh (weekend runs get stale yfinance
    # quotes). Shares its single-flight guard with POST /api/scanner/run.
    from services.multibagger_scanner import run_multibagger_scan
    scheduler.add_job(
        run_multibagger_scan, "cron",
        day_of_week="fri", hour=17, minute=30, id="weekly_multibagger_scan",
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
