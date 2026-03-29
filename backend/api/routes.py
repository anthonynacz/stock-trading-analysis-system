"""EdgeFlow API routes."""

from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.connection import get_db
from db.models import (
    EarningsCalendar,
    MarketNews,
    OptionsSnapshot,
    Recommendation,
    Sector,
    Watchlist,
    WatchlistDailySnapshot,
)
from utils.schemas import (
    CatalystResponse,
    MarketNewsResponse,
    OptionsSnapshotResponse,
    RecommendationResponse,
    SystemStatusResponse,
    WatchlistItemResponse,
)

router = APIRouter(prefix="/api")


# ── Watchlist ───────────────────────────────────────────────────────────────


@router.get("/watchlist", response_model=list[WatchlistItemResponse])
async def get_watchlist(
    sector: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Watchlist)
        .join(Sector, Watchlist.sector_id == Sector.id, isouter=True)
        .where(Watchlist.is_active.is_(True))
    )
    if sector:
        stmt = stmt.where(Sector.name == sector)

    result = await db.execute(stmt)
    items = result.scalars().all()

    # Look up today's snapshot statuses
    today = date.today()
    snap_stmt = select(WatchlistDailySnapshot).where(
        WatchlistDailySnapshot.snapshot_date == today,
    )
    snap_result = await db.execute(snap_stmt)
    status_map: dict[str, str] = {
        s.ticker: s.status for s in snap_result.scalars().all()
    }

    response = []
    for item in items:
        sector_name = item.sector.name if item.sector else None
        status = status_map.get(item.ticker, "EXISTING")
        response.append(
            WatchlistItemResponse(
                id=item.id,
                ticker=item.ticker,
                company_name=item.company_name or "",
                sector=sector_name,
                added_date=item.added_date,
                is_active=item.is_active,
                entry_reason=item.entry_reason,
                status=status,
            )
        )

    return response


# ── Watchlist history ───────────────────────────────────────────────────────


@router.get("/watchlist/history")
async def get_watchlist_history(
    ticker: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(WatchlistDailySnapshot)
        .where(WatchlistDailySnapshot.snapshot_date >= cutoff)
        .order_by(WatchlistDailySnapshot.snapshot_date.desc())
    )
    if ticker:
        stmt = stmt.where(WatchlistDailySnapshot.ticker == ticker.upper())

    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    return [
        {
            "snapshot_date": s.snapshot_date,
            "ticker": s.ticker,
            "status": s.status,
            "sector": s.sector,
        }
        for s in snapshots
    ]


# ── Recommendations ─────────────────────────────────────────────────────────


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    action: str | None = Query(None),
    min_conviction: float | None = Query(None, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    stmt = (
        select(Recommendation)
        .options(selectinload(Recommendation.suggested_options))
        .where(Recommendation.recommendation_date == today)
        .order_by(Recommendation.conviction_score.desc())
    )
    if action:
        stmt = stmt.where(Recommendation.action == action.upper())
    if min_conviction is not None:
        stmt = stmt.where(Recommendation.conviction_score >= min_conviction)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/recommendations/{ticker}", response_model=list[RecommendationResponse])
async def get_recommendations_by_ticker(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    cutoff = date.today() - timedelta(days=30)
    stmt = (
        select(Recommendation)
        .options(selectinload(Recommendation.suggested_options))
        .where(
            Recommendation.ticker == ticker.upper(),
            Recommendation.recommendation_date >= cutoff,
        )
        .order_by(Recommendation.recommendation_date.desc())
    )
    result = await db.execute(stmt)
    recs = result.scalars().all()
    if not recs:
        raise HTTPException(status_code=404, detail=f"No recommendations found for {ticker.upper()}")
    return recs


# ── News ────────────────────────────────────────────────────────────────────


@router.get("/news", response_model=list[MarketNewsResponse])
async def get_news(
    ticker: str | None = Query(None),
    category: str | None = Query(None),
    impact_level: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(MarketNews)
        .order_by(MarketNews.published_at.desc())
        .limit(limit)
    )
    if ticker:
        stmt = stmt.where(MarketNews.ticker == ticker.upper())
    if category:
        stmt = stmt.where(MarketNews.category == category.upper())
    if impact_level:
        stmt = stmt.where(MarketNews.impact_level == impact_level.upper())

    result = await db.execute(stmt)
    return result.scalars().all()


# ── Catalysts ───────────────────────────────────────────────────────────────


@router.get("/catalysts", response_model=list[CatalystResponse])
async def get_catalysts(db: AsyncSession = Depends(get_db)):
    today = date.today()
    horizon = today + timedelta(days=14)

    stmt = (
        select(EarningsCalendar)
        .where(EarningsCalendar.earnings_date.between(today, horizon))
        .order_by(EarningsCalendar.earnings_date.asc())
    )
    result = await db.execute(stmt)
    earnings = result.scalars().all()

    response = []
    for e in earnings:
        days_until = (e.earnings_date - today).days

        # Determine window status
        if e.catalyst_window_start and e.catalyst_window_end:
            if today < e.catalyst_window_start:
                window_status = "APPROACHING"
            elif today <= e.catalyst_window_end:
                window_status = "ACTIVE"
            else:
                window_status = "POST"
        else:
            # No explicit window: derive from days_until
            if days_until > 5:
                window_status = "APPROACHING"
            elif days_until >= 0:
                window_status = "ACTIVE"
            else:
                window_status = "POST"

        response.append(
            CatalystResponse(
                ticker=e.ticker,
                earnings_date=e.earnings_date,
                earnings_time=e.earnings_time,
                days_until=days_until,
                window_status=window_status,
            )
        )

    return response


# ── Options ─────────────────────────────────────────────────────────────────


@router.get("/options/{ticker}", response_model=OptionsSnapshotResponse)
async def get_options_snapshot(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(OptionsSnapshot)
        .where(OptionsSnapshot.ticker == ticker.upper())
        .order_by(OptionsSnapshot.snapshot_time.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    snapshot = result.scalars().first()
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"No options snapshot found for {ticker.upper()}")
    return snapshot


# ── Status ──────────────────────────────────────────────────────────────────


@router.get("/status", response_model=SystemStatusResponse)
async def get_status(db: AsyncSession = Depends(get_db)):
    from services.scheduler import get_scheduler_status, last_refresh

    # Check DB connectivity
    db_connected = True
    try:
        await db.execute(select(func.count()).select_from(Watchlist))
    except Exception:
        db_connected = False

    # Scheduler status
    sched_status = get_scheduler_status()

    # Active watchlist count
    count_result = await db.execute(
        select(func.count()).select_from(Watchlist).where(Watchlist.is_active.is_(True))
    )
    active_count = count_result.scalar() or 0

    return SystemStatusResponse(
        db_connected=db_connected,
        scheduler_running=sched_status.get("running", False),
        active_watchlist_count=active_count,
        last_refresh=last_refresh,
        version="1.0.0",
    )


# ── Refresh ─────────────────────────────────────────────────────────────────


@router.post("/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    from services.scheduler import run_full_pipeline

    background_tasks.add_task(run_full_pipeline)
    return {"status": "refresh_started"}
