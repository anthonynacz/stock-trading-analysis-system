"""EdgeFlow API routes."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Date, Float, cast, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.connection import get_db


def _jsonable(obj):
    """Recursively convert Decimal/date/datetime for JSON columns."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj
from db.models import (
    DeepOptionsAnalysis,
    DiscoveryCandidate,
    EarningsCalendar,
    IndustryRecommendation,
    MarketNews,
    MultibaggerSnapshot,
    MultibaggerUniverse,
    NewsTickerRelevance,
    OptionsSnapshot,
    Position,
    Recommendation,
    ResearchResult,
    Sector,
    StrikeSnapshot,
    UniverseStock,
    Watchlist,
    WatchlistDailySnapshot,
)
from utils.data_sources import DataSourceClient
from utils.schemas import (
    CandidateApproveRequest,
    CatalystResponse,
    DeepOptionsAnalysisResponse,
    DiscoveryCandidateResponse,
    MarketNewsResponse,
    OptionsSnapshotResponse,
    PipelineDatesResponse,
    PositionCloseRequest,
    PositionCreateRequest,
    PositionResponse,
    PositionUpdateRequest,
    RecommendationResponse,
    ResearchResultResponse,
    ChartQueryRequest,
    ChartResponse,
    IndustryDetailResponse,
    IndustryHistoryPoint,
    IndustryRecommendationResponse,
    ScannerDatesResponse,
    ScannerResultResponse,
    ScannerRunResponse,
    ScannerUniverseAddRequest,
    ScannerUniverseItem,
    StrikeSnapshotSaveRequest,
    SuggestedOptionResponse,
    SystemStatusResponse,
    TrendDataPoint,
    TrendResponse,
    UniverseAddRequest,
    UniverseStockResponse,
    UniverseSectorGroup,
    UniverseSummaryResponse,
    WatchlistAddRequest,
    WatchlistChangeItem,
    WatchlistChangesResponse,
    WatchlistItemResponse,
)

router = APIRouter(prefix="/api")


# ── Watchlist ───────────────────────────────────────────────────────────────


async def _get_unusual_options_set(db: AsyncSession) -> set[str]:
    """Return tickers with ≥3x historical avg call volume (today vs 20-day)."""
    try:
        now = datetime.now()
        cutoff_20d = now - timedelta(days=20)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        hist_avg = (
            select(
                OptionsSnapshot.ticker,
                func.avg(cast(OptionsSnapshot.total_call_volume, Float)).label("avg_call"),
            )
            .where(OptionsSnapshot.snapshot_time >= cutoff_20d, OptionsSnapshot.snapshot_time < today_start)
            .group_by(OptionsSnapshot.ticker)
            .subquery()
        )
        today_snap = (
            select(
                OptionsSnapshot.ticker,
                func.max(OptionsSnapshot.total_call_volume).label("today_call"),
            )
            .where(OptionsSnapshot.snapshot_time >= today_start)
            .group_by(OptionsSnapshot.ticker)
            .subquery()
        )
        result = await db.execute(
            select(today_snap.c.ticker)
            .join(hist_avg, today_snap.c.ticker == hist_avg.c.ticker)
            .where(hist_avg.c.avg_call > 0, today_snap.c.today_call >= 3 * hist_avg.c.avg_call)
        )
        return {row[0] for row in result.all()}
    except Exception:
        return set()


def _apply_protection(item: WatchlistItemResponse, unusual_tickers: set[str]) -> WatchlistItemResponse:
    """Set rotation_protected flag and reasons on a watchlist response item."""
    reasons: list[str] = []
    if item.is_locked:
        reasons.append("Locked by user — protected from auto-rotation")
    if item.is_manual:
        reasons.append("Manually added — not subject to auto-rotation")
    if item.ticker in unusual_tickers:
        reasons.append("Unusual options activity — call volume ≥3x the 20-day average")
    if reasons:
        item.rotation_protected = True
        item.protection_reasons = reasons
    return item


@router.get("/watchlist", response_model=list[WatchlistItemResponse])
async def get_watchlist(
    sector: str | None = Query(None),
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    effective_date = target_date or date.today()
    is_today = effective_date == date.today()

    # Get snapshot statuses for the effective date
    snap_stmt = select(WatchlistDailySnapshot).where(
        WatchlistDailySnapshot.snapshot_date == effective_date,
    )
    if sector:
        snap_stmt = snap_stmt.where(WatchlistDailySnapshot.sector == sector)
    snap_result = await db.execute(snap_stmt)
    snapshots = snap_result.scalars().all()
    status_map: dict[str, str] = {s.ticker: s.status for s in snapshots}
    sector_map: dict[str, str | None] = {s.ticker: s.sector for s in snapshots}

    # Fetch unusual options tickers once for protection annotations
    unusual_tickers = await _get_unusual_options_set(db) if is_today else set()

    if is_today:
        # For today: query active watchlist, overlay snapshot statuses,
        # then also append REMOVED items from snapshot
        stmt = (
            select(Watchlist)
            .options(selectinload(Watchlist.sector))
            .where(Watchlist.is_active.is_(True))
        )
        if sector:
            stmt = stmt.join(Watchlist.sector).where(Sector.name == sector)

        result = await db.execute(stmt)
        items = result.scalars().all()

        response = []
        seen_tickers: set[str] = set()
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
                    is_manual=item.is_manual,
                    is_locked=item.is_locked,
                    entry_reason=item.entry_reason,
                    status=status,
                )
            )
            seen_tickers.add(item.ticker)

        # Append REMOVED items from snapshot that aren't in active watchlist
        for snap in snapshots:
            if snap.status == "REMOVED" and snap.ticker not in seen_tickers:
                # Look up watchlist metadata for removed ticker
                wl_result = await db.execute(
                    select(Watchlist)
                    .options(selectinload(Watchlist.sector))
                    .where(Watchlist.ticker == snap.ticker)
                    .order_by(Watchlist.added_date.desc())
                    .limit(1)
                )
                wl_row = wl_result.scalar_one_or_none()
                response.append(
                    WatchlistItemResponse(
                        id=wl_row.id if wl_row else 0,
                        ticker=snap.ticker,
                        company_name=wl_row.company_name or "" if wl_row else "",
                        sector=snap.sector,
                        added_date=wl_row.added_date if wl_row else effective_date,
                        is_active=False,
                        is_manual=wl_row.is_manual if wl_row else False,
                        is_locked=wl_row.is_locked if wl_row else False,
                        entry_reason=wl_row.entry_reason if wl_row else None,
                        status="REMOVED",
                    )
                )
        return [_apply_protection(r, unusual_tickers) for r in response]
    else:
        # Historical: drive entirely from snapshots, join Watchlist for metadata
        all_tickers = [s.ticker for s in snapshots]
        wl_result = await db.execute(
            select(Watchlist)
            .options(selectinload(Watchlist.sector))
            .where(Watchlist.ticker.in_(all_tickers))
        )
        wl_map: dict[str, Watchlist] = {}
        for wl in wl_result.scalars().all():
            # Keep the most recent entry per ticker
            if wl.ticker not in wl_map or (wl.added_date and wl.added_date > wl_map[wl.ticker].added_date):
                wl_map[wl.ticker] = wl

        response = []
        for snap in snapshots:
            wl = wl_map.get(snap.ticker)
            response.append(
                WatchlistItemResponse(
                    id=wl.id if wl else 0,
                    ticker=snap.ticker,
                    company_name=wl.company_name or "" if wl else "",
                    sector=snap.sector or (wl.sector.name if wl and wl.sector else None),
                    added_date=wl.added_date if wl else effective_date,
                    is_active=snap.status != "REMOVED",
                    is_manual=wl.is_manual if wl else False,
                    is_locked=wl.is_locked if wl else False,
                    entry_reason=wl.entry_reason if wl else None,
                    status=snap.status,
                )
            )
        return [_apply_protection(r, unusual_tickers) for r in response]


# ── Watchlist manual add/remove ─────────────────────────────────────────────


@router.post("/watchlist", response_model=WatchlistItemResponse, status_code=201)
async def add_to_watchlist(
    body: WatchlistAddRequest,
    db: AsyncSession = Depends(get_db),
):
    import asyncio

    ticker = body.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    # Check if already active
    existing = await db.execute(
        select(Watchlist).where(Watchlist.ticker == ticker, Watchlist.is_active.is_(True))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"{ticker} is already on the watchlist")

    # Fetch company name + sector via yfinance
    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, ticker)
        company_name = stock_data.get("company_name", ticker) if stock_data else ticker
    finally:
        client.close()

    # Auto-detect EdgeFlow sector from yfinance sector/industry
    from services.universe_discoverer import YFINANCE_SECTOR_MAP

    sector_id = None
    sector_name = None
    if stock_data:
        yf_sector = stock_data.get("sector") or ""
        yf_industry = stock_data.get("industry") or ""
        edgeflow_sector = YFINANCE_SECTOR_MAP.get(yf_sector)
        # Refine: semiconductor industry overrides to AI/Semiconductors
        if "semiconductor" in yf_industry.lower() or "chip" in yf_industry.lower():
            edgeflow_sector = "AI/Semiconductors"
        if edgeflow_sector:
            row = await db.execute(
                select(Sector).where(Sector.name == edgeflow_sector)
            )
            sector_obj = row.scalar_one_or_none()
            if sector_obj:
                sector_id = sector_obj.id
                sector_name = sector_obj.name

    wl = Watchlist(
        ticker=ticker,
        company_name=company_name,
        sector_id=sector_id,
        added_date=date.today(),
        is_active=True,
        is_manual=True,
        entry_reason="manually added",
    )
    db.add(wl)
    await db.commit()
    await db.refresh(wl)

    return WatchlistItemResponse(
        id=wl.id,
        ticker=wl.ticker,
        company_name=wl.company_name or "",
        sector=sector_name,
        added_date=wl.added_date,
        is_active=True,
        is_manual=True,
        is_locked=wl.is_locked,
        entry_reason=wl.entry_reason,
        status="EXISTING",
    )


@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Watchlist).where(
            Watchlist.ticker == ticker.upper(),
            Watchlist.is_active.is_(True),
            Watchlist.is_manual.is_(True),
        )
    )
    wl = result.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail=f"No manual entry found for {ticker.upper()}")

    wl.is_active = False
    wl.removed_date = date.today()
    wl.exit_reason = "manually removed"
    await db.commit()
    return {"status": "removed", "ticker": ticker.upper()}


@router.put("/watchlist/{ticker}/lock")
async def toggle_lock(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Watchlist).where(
            Watchlist.ticker == ticker.upper(),
            Watchlist.is_active.is_(True),
        )
    )
    wl = result.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail=f"No active entry for {ticker.upper()}")

    wl.is_locked = not wl.is_locked
    await db.commit()
    return {"ticker": ticker.upper(), "is_locked": wl.is_locked}


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


# ── Watchlist changes (entrants / exiters) ─────────────────────────────────


@router.get("/watchlist/changes", response_model=WatchlistChangesResponse)
async def get_watchlist_changes(
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    effective_date = target_date or date.today()
    stmt = select(WatchlistDailySnapshot).where(
        WatchlistDailySnapshot.snapshot_date == effective_date,
        WatchlistDailySnapshot.status.in_(["NEW_ENTRANT", "REMOVED"]),
    )
    result = await db.execute(stmt)
    changes = result.scalars().all()

    # Look up entry_reason / exit_reason from the Watchlist table
    change_tickers = [c.ticker for c in changes]
    reason_map: dict[str, str | None] = {}
    if change_tickers:
        wl_result = await db.execute(
            select(Watchlist).where(Watchlist.ticker.in_(change_tickers))
        )
        for wl in wl_result.scalars().all():
            # Entrants: added_date matches effective_date
            if wl.added_date == effective_date and wl.entry_reason:
                reason_map.setdefault(f"entry:{wl.ticker}", wl.entry_reason)
            # Exiters: removed_date matches effective_date
            if wl.removed_date == effective_date and wl.exit_reason:
                reason_map.setdefault(f"exit:{wl.ticker}", wl.exit_reason)

    return WatchlistChangesResponse(
        date=effective_date,
        entrants=[
            WatchlistChangeItem(
                ticker=c.ticker,
                sector=c.sector,
                reason=reason_map.get(f"entry:{c.ticker}"),
            )
            for c in changes if c.status == "NEW_ENTRANT"
        ],
        exiters=[
            WatchlistChangeItem(
                ticker=c.ticker,
                sector=c.sector,
                reason=reason_map.get(f"exit:{c.ticker}"),
            )
            for c in changes if c.status == "REMOVED"
        ],
    )


# ── Pipeline dates ─────────────────────────────────────────────────────────


@router.get("/pipeline-dates", response_model=PipelineDatesResponse)
async def get_pipeline_dates(db: AsyncSession = Depends(get_db)):
    cutoff = date.today() - timedelta(days=90)
    stmt = (
        select(func.distinct(WatchlistDailySnapshot.snapshot_date))
        .where(WatchlistDailySnapshot.snapshot_date >= cutoff)
        .order_by(WatchlistDailySnapshot.snapshot_date.desc())
    )
    result = await db.execute(stmt)
    dates = [row[0] for row in result.all()]
    return PipelineDatesResponse(dates=dates)


# ── Recommendations ─────────────────────────────────────────────────────────


async def _ticker_sector_map(
    db: AsyncSession, tickers: list[str]
) -> dict[str, str]:
    """Build ticker → sector name map for the given tickers."""
    if not tickers:
        return {}
    result = await db.execute(
        select(UniverseStock.ticker, Sector.name)
        .join(Sector, UniverseStock.sector_id == Sector.id)
        .where(UniverseStock.ticker.in_(tickers))
    )
    return {ticker: sector for ticker, sector in result.all()}


def _attach_sectors(
    recs: list[Recommendation], sector_map: dict[str, str]
) -> list[RecommendationResponse]:
    out: list[RecommendationResponse] = []
    for rec in recs:
        resp = RecommendationResponse.model_validate(rec)
        resp.sector = sector_map.get(rec.ticker)
        out.append(resp)
    return out


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    action: str | None = Query(None),
    min_conviction: float | None = Query(None, ge=0, le=100),
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    effective_date = target_date or date.today()
    stmt = (
        select(Recommendation)
        .options(selectinload(Recommendation.suggested_options))
        .where(Recommendation.recommendation_date == effective_date)
        .order_by(Recommendation.conviction_score.desc())
    )
    if action:
        stmt = stmt.where(Recommendation.action == action.upper())
    if min_conviction is not None:
        stmt = stmt.where(Recommendation.conviction_score >= min_conviction)

    result = await db.execute(stmt)
    recs = result.scalars().all()
    sector_map = await _ticker_sector_map(db, [r.ticker for r in recs])
    return _attach_sectors(recs, sector_map)


@router.get("/recommendations/{ticker}", response_model=list[RecommendationResponse])
async def get_recommendations_by_ticker(
    ticker: str,
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Recommendation)
        .options(selectinload(Recommendation.suggested_options))
        .where(Recommendation.ticker == ticker.upper())
    )
    if target_date:
        stmt = stmt.where(Recommendation.recommendation_date == target_date)
    else:
        cutoff = date.today() - timedelta(days=30)
        stmt = stmt.where(Recommendation.recommendation_date >= cutoff)
    stmt = stmt.order_by(Recommendation.recommendation_date.desc())
    result = await db.execute(stmt)
    recs = result.scalars().all()
    if not recs:
        raise HTTPException(status_code=404, detail=f"No recommendations found for {ticker.upper()}")
    sector_map = await _ticker_sector_map(db, [ticker.upper()])
    return _attach_sectors(recs, sector_map)


# ── News ────────────────────────────────────────────────────────────────────


@router.get("/news")
async def get_news(
    ticker: str | None = Query(None),
    mode: str = Query("general"),
    category: str | None = Query(None),
    impact_level: str | None = Query(None),
    industry: str | None = Query(None),
    min_relevance: float = Query(0.3, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    # Resolve industry → list of universe tickers in that sector
    industry_tickers: list[str] | None = None
    if industry:
        ind_result = await db.execute(
            select(UniverseStock.ticker)
            .join(Sector, UniverseStock.sector_id == Sector.id)
            .where(Sector.name == industry, UniverseStock.is_active.is_(True))
        )
        industry_tickers = [row[0] for row in ind_result.all()]
        if not industry_tickers:
            return []

    if mode == "ticker" and ticker:
        # Join through relevance table — find all news relevant to this ticker
        stmt = (
            select(MarketNews)
            .join(NewsTickerRelevance)
            .where(
                NewsTickerRelevance.ticker == ticker.upper(),
                NewsTickerRelevance.relevance_score >= min_relevance,
            )
            .options(selectinload(MarketNews.ticker_relevances))
            .order_by(MarketNews.published_at.desc())
            .limit(limit)
        )
        if industry_tickers is not None:
            stmt = stmt.where(NewsTickerRelevance.ticker.in_(industry_tickers))
    elif mode == "watchlist":
        # Find news relevant to any active watchlist ticker
        active_result = await db.execute(
            select(Watchlist.ticker).where(Watchlist.is_active.is_(True))
        )
        watchlist_tickers = [row[0] for row in active_result.all()]
        if not watchlist_tickers:
            return []
        scope_tickers = watchlist_tickers
        if industry_tickers is not None:
            scope_tickers = [t for t in watchlist_tickers if t in set(industry_tickers)]
            if not scope_tickers:
                return []
        stmt = (
            select(MarketNews)
            .join(NewsTickerRelevance)
            .where(
                NewsTickerRelevance.ticker.in_(scope_tickers),
                NewsTickerRelevance.relevance_score >= min_relevance,
            )
            .options(selectinload(MarketNews.ticker_relevances))
            .order_by(MarketNews.published_at.desc())
            .limit(limit)
        )
    else:
        # General mode — original behavior. Industry filter still routes through
        # the relevance table to scope articles to tickers in that sector.
        if industry_tickers is not None:
            stmt = (
                select(MarketNews)
                .join(NewsTickerRelevance)
                .where(
                    NewsTickerRelevance.ticker.in_(industry_tickers),
                    NewsTickerRelevance.relevance_score >= min_relevance,
                )
                .options(selectinload(MarketNews.ticker_relevances))
                .order_by(MarketNews.published_at.desc())
                .limit(limit)
            )
            if ticker:
                stmt = stmt.where(MarketNews.ticker == ticker.upper())
        else:
            stmt = (
                select(MarketNews)
                .options(selectinload(MarketNews.ticker_relevances))
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
    news_items = result.scalars().unique().all()
    return [MarketNewsResponse.from_news(n) for n in news_items]


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
                fiscal_quarter=e.fiscal_quarter,
                consensus_eps=e.consensus_eps,
                days_until=days_until,
                window_status=window_status,
            )
        )

    return response


# ── Reports ──────────────────────────────────────────────────────────────


@router.get("/reports/daily")
async def get_daily_report(
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """Generate and download a PDF daily analysis report."""
    from services.report_generator import generate_daily_report

    effective_date = target_date or date.today()
    pdf_bytes = await generate_daily_report(effective_date, db)
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=edgeflow-{effective_date}.pdf",
        },
    )


# ── Strike snapshots ──────────────────────────────────────────────────────


@router.post("/strikes/snapshots", status_code=201)
async def save_strike_snapshot(
    body: StrikeSnapshotSaveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save current strike scan results for historical review."""
    today = date.today()
    saved = 0

    for ticker, ticker_data in body.results.items():
        current_price = ticker_data.get("current_price")
        # Extract the 3 risk-level pairs as the stored JSON
        results_json = {
            level: ticker_data.get(level)
            for level in ("conservative", "moderate", "aggressive")
            if ticker_data.get(level) is not None
        }

        # Upsert: check for existing row
        existing = await db.execute(
            select(StrikeSnapshot).where(
                StrikeSnapshot.snapshot_date == today,
                StrikeSnapshot.ticker == ticker.upper(),
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.current_price = Decimal(str(round(current_price, 2))) if current_price else None
            row.budget = Decimal(str(body.budget)) if body.budget else None
            row.results = results_json
        else:
            row = StrikeSnapshot(
                snapshot_date=today,
                ticker=ticker.upper(),
                current_price=Decimal(str(round(current_price, 2))) if current_price else None,
                budget=Decimal(str(body.budget)) if body.budget else None,
                results=results_json,
            )
            db.add(row)
        saved += 1

    await db.commit()
    return {"status": "saved", "snapshot_date": str(today), "tickers_saved": saved}


@router.get("/strikes/snapshots/dates")
async def get_strike_snapshot_dates(db: AsyncSession = Depends(get_db)):
    """Return dates that have saved strike snapshots."""
    stmt = (
        select(func.distinct(StrikeSnapshot.snapshot_date))
        .order_by(StrikeSnapshot.snapshot_date.desc())
    )
    result = await db.execute(stmt)
    dates = [row[0] for row in result.all()]
    return {"dates": dates}


@router.get("/strikes/snapshots")
async def get_strike_snapshot(
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """Load a saved strike snapshot for a given date."""
    effective_date = target_date or date.today()
    stmt = select(StrikeSnapshot).where(
        StrikeSnapshot.snapshot_date == effective_date,
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No snapshot found for {effective_date}")

    results: dict[str, dict] = {}
    budget = None
    for row in rows:
        budget = float(row.budget) if row.budget else budget
        results[row.ticker] = {
            "ticker": row.ticker,
            "current_price": float(row.current_price) if row.current_price else None,
            "max_budget": float(row.budget) if row.budget else None,
            **(row.results or {}),
        }

    return {
        "results": results,
        "scanned": len(results),
        "with_results": len(results),
        "snapshot_date": str(effective_date),
        "budget": budget,
    }


# ── Options ─────────────────────────────────────────────────────────────────


@router.get("/options/watchlist/strikes")
async def recommend_strikes_watchlist(
    budget: float | None = Query(None, ge=50, le=100000),
    db: AsyncSession = Depends(get_db),
):
    """Scan all active watchlist tickers for strike recommendations."""
    from services.options_analyzer import OptionsAnalyzer

    # Get active watchlist tickers
    result = await db.execute(
        select(Watchlist).where(Watchlist.is_active.is_(True))
    )
    active = result.scalars().all()
    tickers = [w.ticker for w in active]

    if not tickers:
        return {"results": {}, "scanned": 0, "with_results": 0}

    client = DataSourceClient()
    try:
        analyzer = OptionsAnalyzer(db, client)
        results: dict[str, dict] = {}
        for ticker in tickers:
            try:
                data = await analyzer.recommend_strikes_all(ticker, budget)
                # Only include if at least one risk level has results
                has_any = any(
                    data.get(level, {}).get("recommended_call") is not None
                    or data.get(level, {}).get("recommended_put") is not None
                    for level in ("conservative", "moderate", "aggressive")
                )
                if has_any:
                    results[ticker] = data
            except Exception:
                pass  # skip tickers with no options data
        return {
            "results": results,
            "scanned": len(tickers),
            "with_results": len(results),
        }
    finally:
        client.close()


# ── Deep options analysis — declared BEFORE /options/{ticker} to avoid path shadowing ──


def _bias_from_action(action: str | None) -> tuple[str, float]:
    """Map a recommendation action to (directional_bias, conviction magnitude)."""
    if not action:
        return "NEUTRAL", 0.0
    a = action.upper()
    if a in ("STRONG_BUY", "BUY"):
        return "BULLISH", 1.0
    if a in ("STRONG_SELL", "SELL"):
        return "BEARISH", 1.0
    return "NEUTRAL", 0.0


@router.post("/options/deep/{ticker}", response_model=DeepOptionsAnalysisResponse)
async def analyze_options_deep(ticker: str, db: AsyncSession = Depends(get_db)):
    """Run an expert-level options analysis for a single ticker."""
    import asyncio
    import re

    from services.deep_options_analyzer import DeepOptionsAnalyzer

    ticker = ticker.upper().strip()
    if not re.match(r"^[A-Z]{1,5}$", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, ticker)
        if not stock_data or not stock_data.get("price"):
            raise HTTPException(status_code=404, detail=f"No price data for {ticker}")
        company_name = stock_data.get("company_name")

        # Directional bias: prefer latest Recommendation, else latest ResearchResult.
        bias = "NEUTRAL"
        conviction = 0.0
        rec_stmt = (
            select(Recommendation)
            .where(Recommendation.ticker == ticker)
            .order_by(Recommendation.recommendation_date.desc())
            .limit(1)
        )
        rec = (await db.execute(rec_stmt)).scalar_one_or_none()
        if rec:
            bias, _ = _bias_from_action(rec.action)
            conviction = float(rec.conviction_score) if rec.conviction_score is not None else 0.0
        else:
            research_stmt = (
                select(ResearchResult)
                .where(ResearchResult.ticker == ticker)
                .order_by(ResearchResult.analyzed_at.desc())
                .limit(1)
            )
            research = (await db.execute(research_stmt)).scalar_one_or_none()
            if research:
                bias, _ = _bias_from_action(research.action)
                conviction = (
                    float(research.conviction_score)
                    if research.conviction_score is not None else 0.0
                )

        earnings_stmt = (
            select(EarningsCalendar)
            .where(
                EarningsCalendar.ticker == ticker,
                EarningsCalendar.earnings_date >= date.today(),
            )
            .order_by(EarningsCalendar.earnings_date.asc())
            .limit(1)
        )
        ec = (await db.execute(earnings_stmt)).scalar_one_or_none()
        earnings_date = ec.earnings_date if ec else None

        analyzer = DeepOptionsAnalyzer(db, client)
        report = await analyzer.analyze(
            ticker,
            directional_bias=bias,
            conviction_score=conviction,
            earnings_date=earnings_date,
        )

        row = DeepOptionsAnalysis(
            ticker=ticker,
            company_name=company_name,
            stock_price=(
                Decimal(str(report["stock_price"]))
                if report.get("stock_price") is not None else None
            ),
            iv_rank=(
                Decimal(str(report["iv_rank"]))
                if report.get("iv_rank") is not None else None
            ),
            iv_percentile=(
                Decimal(str(report["iv_percentile"]))
                if report.get("iv_percentile") is not None else None
            ),
            directional_bias=report.get("directional_bias"),
            conviction_score=(
                Decimal(str(report["conviction_score"]))
                if report.get("conviction_score") is not None else None
            ),
            verdict=report.get("verdict"),
            greeks_detail=_jsonable(report.get("greeks_detail")),
            vol_structure=_jsonable(report.get("vol_structure")),
            positioning=_jsonable(report.get("positioning")),
            liquidity=_jsonable(report.get("liquidity")),
            strategy=_jsonable(report.get("strategy")),
            hidden_risks=_jsonable(report.get("hidden_risks")),
            rationale=report.get("rationale"),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        # Cap history at 5 runs per ticker — drop the oldest beyond that.
        keep_stmt = (
            select(DeepOptionsAnalysis.id)
            .where(DeepOptionsAnalysis.ticker == ticker)
            .order_by(DeepOptionsAnalysis.analyzed_at.desc())
            .limit(5)
        )
        keep_ids = [i for (i,) in (await db.execute(keep_stmt)).all()]
        if keep_ids:
            stale = (
                await db.execute(
                    select(DeepOptionsAnalysis)
                    .where(DeepOptionsAnalysis.ticker == ticker)
                    .where(DeepOptionsAnalysis.id.notin_(keep_ids))
                )
            ).scalars().all()
            for old in stale:
                await db.delete(old)
            if stale:
                await db.commit()

        return DeepOptionsAnalysisResponse.model_validate(row)
    finally:
        client.close()


@router.get("/options/deep", response_model=list[DeepOptionsAnalysisResponse])
async def list_deep_options(
    ticker: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    # Order by execution date (newest day first), then ticker alphabetically,
    # then analyzed_at desc to break ties within the same day + ticker.
    stmt = select(DeepOptionsAnalysis).order_by(
        cast(DeepOptionsAnalysis.analyzed_at, Date).desc(),
        DeepOptionsAnalysis.ticker.asc(),
        DeepOptionsAnalysis.analyzed_at.desc(),
    )
    if ticker:
        stmt = stmt.where(DeepOptionsAnalysis.ticker == ticker.upper())
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return [DeepOptionsAnalysisResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/options/deep/{id_}", response_model=DeepOptionsAnalysisResponse)
async def get_deep_options(id_: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(DeepOptionsAnalysis, id_)
    if not row:
        raise HTTPException(status_code=404, detail="Deep options analysis not found")
    return DeepOptionsAnalysisResponse.model_validate(row)


@router.delete("/options/deep/{id_}")
async def delete_deep_options(id_: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(DeepOptionsAnalysis, id_)
    if not row:
        raise HTTPException(status_code=404, detail="Deep options analysis not found")
    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}


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


@router.get("/options/{ticker}/strikes")
async def recommend_strikes(
    ticker: str,
    risk: str = Query("moderate", pattern="^(conservative|moderate|aggressive)$"),
    budget: float | None = Query(None, ge=50, le=100000),
    db: AsyncSession = Depends(get_db),
):
    from services.options_analyzer import OptionsAnalyzer

    client = DataSourceClient()
    try:
        analyzer = OptionsAnalyzer(db, client)
        result = await analyzer.recommend_strikes(ticker.upper(), risk, budget)
        return result
    finally:
        client.close()


@router.get("/options/{ticker}/strikes/all")
async def recommend_strikes_all(
    ticker: str,
    budget: float | None = Query(None, ge=50, le=100000),
    db: AsyncSession = Depends(get_db),
):
    from services.options_analyzer import OptionsAnalyzer

    client = DataSourceClient()
    try:
        analyzer = OptionsAnalyzer(db, client)
        result = await analyzer.recommend_strikes_all(ticker.upper(), budget)
        return result
    finally:
        client.close()


# ── Trends ─────────────────────────────────────────────────────────────────


def _compute_sma(values: list[float | None], window: int) -> list[float | None]:
    """Compute simple moving average, returning None where window is incomplete."""
    result: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
            continue
        w = [v for v in values[i - window + 1 : i + 1] if v is not None]
        result.append(round(sum(w) / len(w), 4) if w else None)
    return result


@router.get("/trends/{ticker}", response_model=TrendResponse)
async def get_ticker_trends(
    ticker: str,
    days: int = Query(20, ge=5, le=90),
    sma: int = Query(5, ge=2, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Return daily trend data for a ticker with SMA overlays."""
    t = ticker.upper()
    lookback = days + sma  # extra rows so SMA is populated from first requested day

    # 1. Business-day spine from recommendation dates
    date_stmt = (
        select(Recommendation.recommendation_date)
        .where(Recommendation.ticker == t)
        .distinct()
        .order_by(Recommendation.recommendation_date.desc())
        .limit(lookback)
    )
    date_result = await db.execute(date_stmt)
    all_dates = sorted([row[0] for row in date_result.all()])
    if not all_dates:
        return TrendResponse(ticker=t, days=days, sma_window=sma, data=[])

    start_date = all_dates[0]

    # 2. Recommendations: price, target, conviction, signal_count
    rec_stmt = (
        select(
            Recommendation.recommendation_date,
            Recommendation.current_price,
            Recommendation.target_price,
            Recommendation.conviction_score,
            Recommendation.signal_count,
        )
        .where(Recommendation.ticker == t, Recommendation.recommendation_date >= start_date)
        .order_by(Recommendation.recommendation_date.asc())
    )
    rec_result = await db.execute(rec_stmt)
    rec_rows = {row[0]: row for row in rec_result.all()}

    # 3. News sentiment + article count: daily aggregates via relevance junction
    news_date = cast(MarketNews.published_at, Date)
    news_stmt = (
        select(
            news_date.label("news_date"),
            func.avg(MarketNews.sentiment_score).label("avg_sent"),
            func.count(MarketNews.id).label("article_count"),
        )
        .join(NewsTickerRelevance)
        .where(
            NewsTickerRelevance.ticker == t,
            NewsTickerRelevance.relevance_score >= 0.3,
            MarketNews.published_at >= start_date,
        )
        .group_by(news_date)
    )
    news_result = await db.execute(news_stmt)
    news_rows = {
        row[0]: {"sentiment": float(row[1]) if row[1] is not None else None, "count": int(row[2])}
        for row in news_result.all()
    }

    # 4. Assemble raw data points on the date spine
    raw: list[dict] = []
    for d in all_dates:
        rec = rec_rows.get(d)
        price = float(rec[1]) if rec and rec[1] is not None else None
        target = float(rec[2]) if rec and rec[2] is not None else None
        conviction = float(rec[3]) if rec and rec[3] is not None else None
        sig_count = int(rec[4]) if rec and rec[4] is not None else None
        news = news_rows.get(d, {})
        raw_sent = news.get("sentiment")
        sentiment = round((raw_sent + 1) * 50, 2) if raw_sent is not None else None
        article_count = news.get("count", 0)
        raw.append({
            "date": d, "price": price, "target_price": target,
            "conviction": conviction, "signal_count": sig_count,
            "sentiment": sentiment, "article_count": article_count,
        })

    # 5. Compute SMAs
    prices = [r["price"] for r in raw]
    convictions = [r["conviction"] for r in raw]
    sig_counts = [float(r["signal_count"]) if r["signal_count"] is not None else None for r in raw]
    sentiments = [r["sentiment"] for r in raw]

    price_sma = _compute_sma(prices, sma)
    conviction_sma = _compute_sma(convictions, sma)
    signal_count_sma = _compute_sma(sig_counts, sma)
    sentiment_sma = _compute_sma(sentiments, sma)

    # 6. Trim to requested days and build response
    output_start = max(0, len(raw) - days)
    data = []
    for i in range(output_start, len(raw)):
        r = raw[i]
        data.append(TrendDataPoint(
            date=r["date"], price=r["price"], target_price=r["target_price"],
            conviction=r["conviction"], signal_count=r["signal_count"],
            sentiment=r["sentiment"], article_count=r["article_count"],
            price_sma=price_sma[i], conviction_sma=conviction_sma[i],
            signal_count_sma=signal_count_sma[i], sentiment_sma=sentiment_sma[i],
        ))

    return TrendResponse(ticker=t, days=days, sma_window=sma, data=data)


# ── Universe management ─────────────────────────────────────────────────────


@router.get("/universe", response_model=UniverseSummaryResponse)
async def get_universe(db: AsyncSession = Depends(get_db)):
    """List the full universe grouped by sector, plus pending candidate count."""
    stmt = (
        select(Sector)
        .options(selectinload(Sector.universe_stocks))
        .order_by(Sector.name)
    )
    result = await db.execute(stmt)
    sectors = result.scalars().all()

    groups = []
    total = 0
    for sector in sectors:
        active = [s for s in sector.universe_stocks if s.is_active]
        active.sort(key=lambda s: s.ticker)
        total += len(active)
        groups.append(UniverseSectorGroup(
            name=sector.name,
            stock_count=len(active),
            stocks=[
                UniverseStockResponse(
                    id=s.id,
                    ticker=s.ticker,
                    company_name=s.company_name,
                    sector=sector.name,
                    source=s.source,
                    is_active=s.is_active,
                    added_at=s.added_at,
                )
                for s in active
            ],
        ))

    pending_result = await db.execute(
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.status == "PENDING"
        )
    )
    pending_count = pending_result.scalar() or 0

    return UniverseSummaryResponse(
        sectors=groups,
        total_stocks=total,
        pending_candidates=pending_count,
    )


@router.post("/universe", response_model=UniverseStockResponse, status_code=201)
async def add_to_universe(
    body: UniverseAddRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manually add a stock to the universe."""
    import asyncio

    ticker = body.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    # Check if already exists
    existing = await db.execute(
        select(UniverseStock).where(UniverseStock.ticker == ticker, UniverseStock.is_active.is_(True))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"{ticker} is already in the universe")

    # Find sector
    sector_result = await db.execute(
        select(Sector).where(Sector.name == body.sector)
    )
    sector = sector_result.scalar_one_or_none()
    if not sector:
        raise HTTPException(status_code=400, detail=f"Unknown sector: {body.sector}")

    # Fetch company name
    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, ticker)
        company_name = stock_data.get("company_name") if stock_data else None
    finally:
        client.close()

    stock = UniverseStock(
        ticker=ticker,
        company_name=company_name,
        sector_id=sector.id,
        source="MANUAL",
        is_active=True,
    )
    db.add(stock)
    await db.commit()
    await db.refresh(stock)

    return UniverseStockResponse(
        id=stock.id,
        ticker=stock.ticker,
        company_name=stock.company_name,
        sector=sector.name,
        source=stock.source,
        is_active=stock.is_active,
        added_at=stock.added_at,
    )


@router.delete("/universe/{ticker}")
async def remove_from_universe(
    ticker: str,
    db: AsyncSession = Depends(get_db),
):
    """Soft-remove a stock from the universe."""
    result = await db.execute(
        select(UniverseStock).where(
            UniverseStock.ticker == ticker.upper(),
            UniverseStock.is_active.is_(True),
        )
    )
    stock = result.scalar_one_or_none()
    if not stock:
        raise HTTPException(status_code=404, detail=f"No active universe entry for {ticker.upper()}")

    stock.is_active = False
    await db.commit()
    return {"status": "removed", "ticker": ticker.upper()}


@router.get("/universe/candidates", response_model=list[DiscoveryCandidateResponse])
async def get_candidates(db: AsyncSession = Depends(get_db)):
    """List pending discovery candidates."""
    stmt = (
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.status == "PENDING")
        .order_by(DiscoveryCandidate.discovered_at.desc())
    )
    result = await db.execute(stmt)
    return [DiscoveryCandidateResponse.model_validate(c) for c in result.scalars().all()]


@router.post("/universe/candidates/{candidate_id}/approve", response_model=UniverseStockResponse)
async def approve_candidate(
    candidate_id: int,
    body: CandidateApproveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Approve a discovery candidate into the universe."""
    candidate = await db.get(DiscoveryCandidate, candidate_id)
    if not candidate or candidate.status != "PENDING":
        raise HTTPException(status_code=404, detail="Pending candidate not found")

    # Find sector
    sector_result = await db.execute(
        select(Sector).where(Sector.name == body.sector)
    )
    sector = sector_result.scalar_one_or_none()
    if not sector:
        raise HTTPException(status_code=400, detail=f"Unknown sector: {body.sector}")

    # Check if ticker already in universe
    existing = await db.execute(
        select(UniverseStock).where(
            UniverseStock.ticker == candidate.ticker,
            UniverseStock.is_active.is_(True),
        )
    )
    if existing.scalar_one_or_none():
        # Mark candidate as approved anyway
        candidate.status = "APPROVED"
        candidate.resolved_at = datetime.utcnow()
        await db.commit()
        raise HTTPException(status_code=409, detail=f"{candidate.ticker} is already in the universe")

    # Create universe stock
    stock = UniverseStock(
        ticker=candidate.ticker,
        company_name=candidate.company_name,
        sector_id=sector.id,
        source="DISCOVERED",
        is_active=True,
    )
    db.add(stock)

    candidate.status = "APPROVED"
    candidate.resolved_at = datetime.utcnow()
    await db.commit()
    await db.refresh(stock)

    return UniverseStockResponse(
        id=stock.id,
        ticker=stock.ticker,
        company_name=stock.company_name,
        sector=sector.name,
        source=stock.source,
        is_active=stock.is_active,
        added_at=stock.added_at,
    )


@router.post("/universe/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Dismiss a discovery candidate."""
    candidate = await db.get(DiscoveryCandidate, candidate_id)
    if not candidate or candidate.status != "PENDING":
        raise HTTPException(status_code=404, detail="Pending candidate not found")

    candidate.status = "DISMISSED"
    candidate.resolved_at = datetime.utcnow()
    await db.commit()
    return {"status": "dismissed", "ticker": candidate.ticker}


@router.post("/universe/discover")
async def trigger_discovery(background_tasks: BackgroundTasks):
    """Trigger universe discovery manually."""
    from services.scheduler import run_pipeline_phase

    background_tasks.add_task(run_pipeline_phase, "discovery")
    return {"status": "discovery_started"}


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


# ── Pipeline ───────────────────────────────────────────────────────────────


@router.post("/refresh")
async def trigger_refresh(background_tasks: BackgroundTasks):
    """Legacy endpoint — runs full pipeline."""
    from services.scheduler import run_tracked_pipeline, ALL_PHASES, pipeline_run

    if pipeline_run.get("status") == "running":
        raise HTTPException(status_code=409, detail="Pipeline already running")

    background_tasks.add_task(run_tracked_pipeline, ALL_PHASES)
    return {"status": "refresh_started"}


@router.post("/pipeline/run")
async def run_pipeline(
    background_tasks: BackgroundTasks,
    body: dict | None = None,
):
    """Start pipeline with optional phase selection.

    Body: { "phases": ["watchlist", "ratings", ...] }
    Omit or pass empty phases for full pipeline.
    """
    from services.scheduler import run_tracked_pipeline, ALL_PHASES, pipeline_run

    if pipeline_run.get("status") == "running":
        raise HTTPException(status_code=409, detail="Pipeline already running")

    requested = (body or {}).get("phases") or ALL_PHASES
    # Validate phase names
    invalid = [p for p in requested if p not in ALL_PHASES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown phases: {invalid}")

    background_tasks.add_task(run_tracked_pipeline, requested)
    return {"status": "started", "phases": requested}


@router.get("/pipeline/status")
async def get_pipeline_status():
    """Poll pipeline progress."""
    from services.scheduler import get_pipeline_run_status

    return get_pipeline_run_status()


# ── Research ───────────────────────────────────────────────────────────────


async def _research_to_response(
    db: AsyncSession, research: ResearchResult
) -> ResearchResultResponse:
    """Convert a ResearchResult to its response, attaching sector via universe lookup."""
    sector_map = await _ticker_sector_map(db, [research.ticker])
    resp = ResearchResultResponse.model_validate(research)
    resp.sector = sector_map.get(research.ticker)
    return resp


@router.post("/research/{ticker}", response_model=ResearchResultResponse)
async def analyze_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    """Run a full on-demand analysis for any ticker."""
    import asyncio
    import re

    from services.analyst_tracker import AnalystTracker
    from services.earnings_calendar import EarningsCalendarService
    from services.news_scanner import NewsScanner
    from services.options_analyzer import OptionsAnalyzer
    from services.recommendation_engine import RecommendationEngine

    ticker = ticker.upper().strip()
    if not re.match(r"^[A-Z]{1,5}$", ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    data_client = DataSourceClient()

    # Fetch stock data to get company name + validate ticker exists
    loop = asyncio.get_event_loop()
    stock_data = await loop.run_in_executor(
        None, data_client.get_stock_data, ticker
    )
    if not stock_data or not stock_data.get("price"):
        raise HTTPException(status_code=404, detail=f"No price data found for {ticker}")

    company_name = stock_data.get("company_name")

    # Instantiate all services
    tracker = AnalystTracker(db, data_client)
    earnings_svc = EarningsCalendarService(db, data_client)
    scanner = NewsScanner(db, data_client)
    analyzer = OptionsAnalyzer(db, data_client)

    # Refresh data from APIs for this ticker
    try:
        await tracker.scan_ratings([ticker])
    except Exception:
        pass  # Non-fatal — analysis can proceed with existing data
    try:
        await earnings_svc.refresh_calendar([ticker])
    except Exception:
        pass
    try:
        ticker_company_map = {ticker: company_name} if company_name else {}
        await scanner.scan_news([ticker], ticker_company_map)
    except Exception:
        pass
    try:
        await analyzer.analyze_ticker(ticker)
    except Exception:
        pass

    await db.commit()

    # Run scoring engine (does NOT write to recommendations table)
    engine = RecommendationEngine(
        db, data_client, tracker, earnings_svc, scanner, analyzer,
    )
    result = await engine.analyze_single(ticker)

    # Persist to research_results
    research = ResearchResult(
        ticker=ticker,
        company_name=company_name,
        action=result["action"],
        conviction_score=Decimal(str(result["conviction_score"])),
        signal_count=result["signal_count"],
        signals=_jsonable(result["signals"]),
        rationale=result["rationale"],
        catalyst_type=result["catalyst_type"],
        entry_strategy=result["entry_strategy"],
        exit_rules=result["exit_rules"],
        risk_level=result["risk_level"],
        current_price=(
            Decimal(str(result["current_price"]))
            if result["current_price"] is not None
            else None
        ),
        target_price=(
            Decimal(str(result["target_price"]))
            if result["target_price"] is not None
            else None
        ),
        stop_loss_price=(
            Decimal(str(result["stop_loss_price"]))
            if result["stop_loss_price"] is not None
            else None
        ),
        options_data=_jsonable(result["options_data"]),
        suggested_options=_jsonable(result["suggested_contracts"]),
    )
    db.add(research)
    await db.commit()
    await db.refresh(research)

    return await _research_to_response(db, research)


@router.get("/research", response_model=list[ResearchResultResponse])
async def list_research(
    ticker: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List research results, optionally filtered by ticker."""
    stmt = select(ResearchResult).order_by(ResearchResult.analyzed_at.desc())
    if ticker:
        stmt = stmt.where(ResearchResult.ticker == ticker.upper())
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    sector_map = await _ticker_sector_map(db, [r.ticker for r in rows])
    out: list[ResearchResultResponse] = []
    for r in rows:
        resp = ResearchResultResponse.model_validate(r)
        resp.sector = sector_map.get(r.ticker)
        out.append(resp)
    return out


@router.get("/research/{id_}", response_model=ResearchResultResponse)
async def get_research(id_: int, db: AsyncSession = Depends(get_db)):
    """Get a single research result by ID."""
    result = await db.get(ResearchResult, id_)
    if not result:
        raise HTTPException(status_code=404, detail="Research result not found")
    return await _research_to_response(db, result)


@router.delete("/research/{id_}")
async def delete_research(id_: int, db: AsyncSession = Depends(get_db)):
    """Delete a research result."""
    result = await db.get(ResearchResult, id_)
    if not result:
        raise HTTPException(status_code=404, detail="Research result not found")
    await db.delete(result)
    await db.commit()
    return {"status": "deleted"}


# ── Positions ────────────────────────────────────────────────────────────────


def _compute_position_fields(
    pos: Position,
    watchlist_tickers: set[str],
    rec_map: dict[str, RecommendationResponse],
) -> PositionResponse:
    """Build a PositionResponse with computed P&L and recommendation overlay."""
    current = float(pos.current_price) if pos.current_price is not None else None
    entry = float(pos.entry_price)
    premium = float(pos.premium_paid) if pos.premium_paid is not None else None

    unrealized_pnl = None
    unrealized_pnl_pct = None
    if pos.status == "OPEN" and current is not None:
        if pos.position_type == "STOCK":
            unrealized_pnl = (current - entry) * pos.quantity
            cost_basis = entry * pos.quantity
        else:
            # CALL/PUT: current_price = current option premium
            basis = premium if premium is not None else entry
            unrealized_pnl = (current - basis) * pos.quantity * 100
            cost_basis = basis * pos.quantity * 100
        if cost_basis and cost_basis != 0:
            unrealized_pnl_pct = round(unrealized_pnl / cost_basis * 100, 2)
        unrealized_pnl = round(unrealized_pnl, 2)

    days_to_expiry = None
    if pos.expiry:
        days_to_expiry = (pos.expiry - date.today()).days

    is_on_watchlist = pos.ticker in watchlist_tickers
    rec_response = rec_map.get(pos.ticker)

    return PositionResponse(
        id=pos.id,
        ticker=pos.ticker,
        company_name=pos.company_name,
        position_type=pos.position_type,
        quantity=pos.quantity,
        entry_price=pos.entry_price,
        current_price=pos.current_price,
        strike_price=pos.strike_price,
        premium_paid=pos.premium_paid,
        expiry=pos.expiry,
        stop_loss=pos.stop_loss,
        target_price=pos.target_price,
        status=pos.status,
        opened_at=pos.opened_at,
        closed_at=pos.closed_at,
        close_price=pos.close_price,
        realized_pnl=pos.realized_pnl,
        notes=pos.notes,
        unrealized_pnl=Decimal(str(unrealized_pnl)) if unrealized_pnl is not None else None,
        unrealized_pnl_pct=unrealized_pnl_pct,
        days_to_expiry=days_to_expiry,
        is_on_watchlist=is_on_watchlist,
        recommendation=rec_response,
    )


async def _get_watchlist_context(
    db: AsyncSession,
    position_tickers: set[str] | None = None,
) -> tuple[set[str], dict[str, RecommendationResponse]]:
    """Load active watchlist tickers and latest recommendations for each.

    Also queries recommendations for *position_tickers* not on the watchlist,
    and falls back to research_results when no recommendation exists.
    """
    wl_result = await db.execute(
        select(Watchlist.ticker).where(Watchlist.is_active.is_(True))
    )
    watchlist_tickers = {row[0] for row in wl_result.all()}

    # All tickers we need recommendations for
    all_tickers = watchlist_tickers | (position_tickers or set())

    rec_map: dict[str, RecommendationResponse] = {}
    if all_tickers:
        rec_result = await db.execute(
            select(Recommendation)
            .where(Recommendation.ticker.in_(all_tickers))
            .order_by(Recommendation.recommendation_date.desc())
            .options(selectinload(Recommendation.suggested_options))
        )
        for rec in rec_result.scalars():
            if rec.ticker not in rec_map:
                rec_map[rec.ticker] = RecommendationResponse.model_validate(rec)

    # For tickers still missing, fall back to research_results
    missing = all_tickers - set(rec_map.keys())
    if missing:
        from db.models import ResearchResult as RR
        rr_result = await db.execute(
            select(RR)
            .where(RR.ticker.in_(missing))
            .order_by(RR.analyzed_at.desc())
        )
        for rr in rr_result.scalars():
            if rr.ticker not in rec_map:
                # Build a RecommendationResponse from research result
                opts = []
                if rr.suggested_options:
                    for o in rr.suggested_options:
                        opts.append(SuggestedOptionResponse(
                            id=o.get("id", 0),
                            contract_type=o.get("contract_type", "CALL"),
                            strike=o.get("strike"),
                            expiry=o.get("expiry"),
                            premium_estimate=o.get("premium_estimate"),
                            delta_estimate=o.get("delta_estimate"),
                            strategy=o.get("strategy"),
                            strategy_rationale=o.get("strategy_rationale"),
                            days_to_expiry=o.get("days_to_expiry"),
                            breakeven_price=o.get("breakeven_price"),
                        ))
                rec_map[rr.ticker] = RecommendationResponse(
                    id=rr.id,
                    recommendation_date=rr.analyzed_at.date() if rr.analyzed_at else date.today(),
                    ticker=rr.ticker,
                    action=rr.action,
                    conviction_score=rr.conviction_score,
                    signal_count=rr.signal_count,
                    signals=rr.signals,
                    rationale=rr.rationale,
                    catalyst_type=rr.catalyst_type,
                    entry_strategy=rr.entry_strategy,
                    exit_rules=rr.exit_rules,
                    risk_level=rr.risk_level,
                    current_price=rr.current_price,
                    target_price=rr.target_price,
                    stop_loss_price=rr.stop_loss_price,
                    suggested_options=opts,
                )

    return watchlist_tickers, rec_map


@router.post("/positions", response_model=PositionResponse, status_code=201)
async def create_position(
    body: PositionCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new tracked position."""
    import asyncio

    ticker = body.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")
    if body.position_type not in ("CALL", "PUT", "STOCK"):
        raise HTTPException(status_code=400, detail="position_type must be CALL, PUT, or STOCK")

    # Fetch company name + current stock price
    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, ticker)
        company_name = stock_data.get("company_name", ticker) if stock_data else ticker
        stock_price = stock_data.get("price") if stock_data else None
    finally:
        client.close()

    # For STOCK positions, current_price = stock price. For options, user provides it.
    current_price = None
    if body.position_type == "STOCK" and stock_price is not None:
        current_price = Decimal(str(stock_price))

    pos = Position(
        ticker=ticker,
        company_name=company_name,
        position_type=body.position_type,
        quantity=body.quantity,
        entry_price=body.entry_price,
        current_price=current_price,
        strike_price=body.strike_price,
        premium_paid=body.premium_paid,
        expiry=body.expiry,
        stop_loss=body.stop_loss,
        target_price=body.target_price,
        status="OPEN",
        notes=body.notes,
    )
    db.add(pos)
    await db.commit()
    await db.refresh(pos)

    watchlist_tickers, rec_map = await _get_watchlist_context(db, {pos.ticker})
    return _compute_position_fields(pos, watchlist_tickers, rec_map)


@router.get("/positions", response_model=list[PositionResponse])
async def list_positions(
    status: str | None = Query(None),
    ticker: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List positions with computed P&L and recommendation overlay."""
    query = select(Position).order_by(Position.opened_at.desc())
    if status:
        query = query.where(Position.status == status.upper())
    if ticker:
        query = query.where(Position.ticker == ticker.upper())

    result = await db.execute(query)
    positions = result.scalars().all()

    pos_tickers = {p.ticker for p in positions}
    watchlist_tickers, rec_map = await _get_watchlist_context(db, pos_tickers)
    return [_compute_position_fields(p, watchlist_tickers, rec_map) for p in positions]


@router.get("/positions/{id_}", response_model=PositionResponse)
async def get_position(id_: int, db: AsyncSession = Depends(get_db)):
    """Get a single position with computed fields."""
    pos = await db.get(Position, id_)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    watchlist_tickers, rec_map = await _get_watchlist_context(db, {pos.ticker})
    return _compute_position_fields(pos, watchlist_tickers, rec_map)


@router.put("/positions/{id_}", response_model=PositionResponse)
async def update_position(
    id_: int,
    body: PositionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update mutable position fields."""
    pos = await db.get(Position, id_)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    if body.stop_loss is not None:
        pos.stop_loss = body.stop_loss
    if body.target_price is not None:
        pos.target_price = body.target_price
    if body.notes is not None:
        pos.notes = body.notes
    if body.current_price is not None:
        pos.current_price = body.current_price

    await db.commit()
    await db.refresh(pos)

    watchlist_tickers, rec_map = await _get_watchlist_context(db, {pos.ticker})
    return _compute_position_fields(pos, watchlist_tickers, rec_map)


@router.post("/positions/{id_}/close", response_model=PositionResponse)
async def close_position(
    id_: int,
    body: PositionCloseRequest,
    db: AsyncSession = Depends(get_db),
):
    """Close an open position and compute realized P&L."""
    pos = await db.get(Position, id_)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    if pos.status != "OPEN":
        raise HTTPException(status_code=409, detail="Position is already closed")

    pos.status = "CLOSED"
    pos.closed_at = datetime.utcnow()
    pos.close_price = body.close_price
    if body.notes:
        pos.notes = (pos.notes or "") + ("\n" if pos.notes else "") + body.notes

    # Compute realized P&L
    close = float(body.close_price)
    entry = float(pos.entry_price)
    premium = float(pos.premium_paid) if pos.premium_paid is not None else None

    if pos.position_type == "STOCK":
        pos.realized_pnl = Decimal(str(round((close - entry) * pos.quantity, 2)))
    else:
        basis = premium if premium is not None else entry
        pos.realized_pnl = Decimal(str(round((close - basis) * pos.quantity * 100, 2)))

    await db.commit()
    await db.refresh(pos)

    watchlist_tickers, rec_map = await _get_watchlist_context(db, {pos.ticker})
    return _compute_position_fields(pos, watchlist_tickers, rec_map)


@router.delete("/positions/{id_}")
async def delete_position(id_: int, db: AsyncSession = Depends(get_db)):
    """Delete a position."""
    pos = await db.get(Position, id_)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    await db.delete(pos)
    await db.commit()
    return {"status": "deleted"}


@router.post("/positions/{id_}/refresh-price", response_model=PositionResponse)
async def refresh_position_price(id_: int, db: AsyncSession = Depends(get_db)):
    """Refresh the current stock price for a position via yfinance."""
    import asyncio

    pos = await db.get(Position, id_)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, pos.ticker)
        price = stock_data.get("price") if stock_data else None
    finally:
        client.close()

    if price is not None and pos.position_type == "STOCK":
        pos.current_price = Decimal(str(price))
        await db.commit()
        await db.refresh(pos)

    watchlist_tickers, rec_map = await _get_watchlist_context(db, {pos.ticker})
    return _compute_position_fields(pos, watchlist_tickers, rec_map)


# ── Multi-bagger scanner ────────────────────────────────────────────────────

@router.get("/scanner/universe", response_model=list[ScannerUniverseItem])
async def scanner_universe_list(db: AsyncSession = Depends(get_db)):
    """List all active tickers in the scanner universe."""
    result = await db.execute(
        select(MultibaggerUniverse)
        .where(MultibaggerUniverse.is_active.is_(True))
        .order_by(MultibaggerUniverse.theme, MultibaggerUniverse.ticker)
    )
    return result.scalars().all()


@router.post("/scanner/universe", response_model=ScannerUniverseItem, status_code=201)
async def scanner_universe_add(
    payload: ScannerUniverseAddRequest, db: AsyncSession = Depends(get_db)
):
    """Add a ticker to the scanner universe."""
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker required")

    existing = await db.execute(
        select(MultibaggerUniverse).where(MultibaggerUniverse.ticker == ticker)
    )
    row = existing.scalars().first()
    if row:
        if not row.is_active:
            row.is_active = True
            row.theme = payload.theme or row.theme
            await db.commit()
            await db.refresh(row)
        return row

    row = MultibaggerUniverse(
        ticker=ticker,
        theme=payload.theme,
        source="MANUAL",
        is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/scanner/universe/{ticker}")
async def scanner_universe_remove(ticker: str, db: AsyncSession = Depends(get_db)):
    """Soft-remove a ticker from the scanner universe (sets is_active=False)."""
    ticker = ticker.strip().upper()
    result = await db.execute(
        select(MultibaggerUniverse).where(MultibaggerUniverse.ticker == ticker)
    )
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Ticker not in scanner universe")
    row.is_active = False
    await db.commit()
    return {"status": "removed", "ticker": ticker}


@router.get("/scanner/dates", response_model=ScannerDatesResponse)
async def scanner_dates(db: AsyncSession = Depends(get_db)):
    """Distinct scanner run dates, most recent first (last 180 days)."""
    cutoff = date.today() - timedelta(days=180)
    result = await db.execute(
        select(func.distinct(MultibaggerSnapshot.run_date))
        .where(MultibaggerSnapshot.run_date >= cutoff)
        .order_by(MultibaggerSnapshot.run_date.desc())
    )
    return ScannerDatesResponse(dates=[r[0] for r in result.all()])


@router.get("/scanner/results", response_model=list[ScannerResultResponse])
async def scanner_results(
    target_date: date | None = Query(None, alias="date"),
    tier: str | None = Query(None),
    theme: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Fetch scanner results for a date (default: latest available)."""
    if target_date is None:
        latest = await db.execute(
            select(func.max(MultibaggerSnapshot.run_date))
        )
        target_date = latest.scalar()
        if target_date is None:
            return []

    stmt = (
        select(MultibaggerSnapshot)
        .where(MultibaggerSnapshot.run_date == target_date)
        .order_by(MultibaggerSnapshot.composite_score.desc())
    )
    if tier:
        stmt = stmt.where(MultibaggerSnapshot.tier == tier.upper())
    if theme:
        stmt = stmt.where(MultibaggerSnapshot.theme == theme.upper())
    result = await db.execute(stmt)
    return result.scalars().all()


_scanner_run_state: dict = {"running": False, "started_at": None, "last_result": None}


async def _run_scanner_background():
    """Background worker that runs the scanner and updates shared state."""
    from services.multibagger_scanner import MultibaggerScanner
    from db.connection import async_session

    _scanner_run_state["running"] = True
    _scanner_run_state["started_at"] = datetime.utcnow().isoformat()
    client = DataSourceClient()
    try:
        async with async_session() as session:
            scanner = MultibaggerScanner(session, client)
            summary = await scanner.run_and_persist()
            _scanner_run_state["last_result"] = summary
    except Exception as e:
        _scanner_run_state["last_result"] = {"status": "error", "error": str(e)}
    finally:
        client.close()
        _scanner_run_state["running"] = False


@router.post("/scanner/run", response_model=ScannerRunResponse)
async def scanner_run(background_tasks: BackgroundTasks):
    """Trigger a scanner run. Runs in the background — poll /scanner/status."""
    if _scanner_run_state["running"]:
        return ScannerRunResponse(status="already_running")
    background_tasks.add_task(_run_scanner_background)
    return ScannerRunResponse(status="started")


@router.get("/scanner/status")
async def scanner_status():
    """Poll scanner run state."""
    return {
        "running": _scanner_run_state["running"],
        "started_at": _scanner_run_state["started_at"],
        "last_result": _scanner_run_state["last_result"],
    }


# ── Industry recommendations (sector-level) ────────────────────────────────

@router.get("/industries", response_model=list[IndustryRecommendationResponse])
async def industries_list(
    target_date: date | None = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    """Latest (or given-date) industry recommendations — one row per industry."""
    if target_date is None:
        latest = await db.execute(select(func.max(IndustryRecommendation.rec_date)))
        target_date = latest.scalar()
        if target_date is None:
            return []
    stmt = (
        select(IndustryRecommendation)
        .where(IndustryRecommendation.rec_date == target_date)
        .order_by(IndustryRecommendation.conviction_score.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/industries/{industry:path}", response_model=IndustryDetailResponse)
async def industry_detail(industry: str, db: AsyncSession = Depends(get_db)):
    """Detail for one industry: latest rec, 30d history, and today's member recs.

    Note: ``:path`` converter lets values like "AI/Semiconductors" match.
    """
    latest_q = await db.execute(
        select(IndustryRecommendation)
        .where(IndustryRecommendation.industry == industry)
        .order_by(IndustryRecommendation.rec_date.desc())
        .limit(1)
    )
    latest = latest_q.scalars().first()
    if not latest:
        raise HTTPException(status_code=404, detail=f"No recommendations for industry '{industry}'")

    # 30-day history
    cutoff = date.today() - timedelta(days=30)
    hist_q = await db.execute(
        select(IndustryRecommendation)
        .where(
            IndustryRecommendation.industry == industry,
            IndustryRecommendation.rec_date >= cutoff,
        )
        .order_by(IndustryRecommendation.rec_date.asc())
    )
    history_rows = hist_q.scalars().all()
    history = [
        IndustryHistoryPoint(
            rec_date=h.rec_date,
            action=h.action,
            conviction_score=h.conviction_score,
            signal_count=h.signal_count,
        )
        for h in history_rows
    ]

    # Today's member recommendations — eager-load suggested_options to avoid
    # lazy-load errors when Pydantic serializes the relationship.
    from config import SECTORS
    cfg = SECTORS.get(industry)
    members: list = []
    if cfg:
        members_tickers = cfg["universe"]
        mem_q = await db.execute(
            select(Recommendation)
            .where(
                Recommendation.recommendation_date == latest.rec_date,
                Recommendation.ticker.in_(members_tickers),
            )
            .options(selectinload(Recommendation.suggested_options))
            .order_by(Recommendation.conviction_score.desc())
        )
        members = list(mem_q.scalars().all())

    return IndustryDetailResponse(
        industry=industry,
        latest=latest,
        history=history,
        members=members,
    )


# ── Chart builder (dynamic visualizations) ─────────────────────────────────

@router.post("/charts/query", response_model=ChartResponse)
async def charts_query(payload: ChartQueryRequest, db: AsyncSession = Depends(get_db)):
    """Run a chart query for the given dataset and spec."""
    from services.chart_builder import ChartBuilder

    builder = ChartBuilder(db)
    try:
        result = await builder.query(payload.dataset, payload.spec)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/charts/datasets")
async def charts_datasets():
    """List available datasets and their metric/aggregation options.

    Frontend uses this to render the dataset picker + dataset-specific forms
    without hard-coding the metric keys, so backend additions show up
    automatically (within the curated dataset shape).
    """
    from services.chart_builder import INDUSTRY_METRICS, TICKER_METRICS

    return {
        "datasets": [
            {
                "key": "ticker_time_series",
                "label": "Ticker Time Series",
                "description": "One ticker, multiple metrics over time. Line chart.",
                "chart_type": "line",
                "metrics": [
                    {"key": k, "label": v["label"], "source": v["source"]}
                    for k, v in TICKER_METRICS.items()
                ],
            },
            {
                "key": "signal_breakdown",
                "label": "Signal Breakdown",
                "description": "Recommendation signals grouped by name. Bar chart.",
                "chart_type": "bar",
                "aggregations": ["count", "sum", "avg", "min", "max"],
                "actions": ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"],
            },
            {
                "key": "industry_comparison",
                "label": "Industry Comparison",
                "description": "Industry-level metric across industries (snapshot) or over time (trend).",
                "chart_type": "line | bar",
                "views": ["trend", "snapshot"],
                "metrics": [
                    {"key": k, "label": v["label"]} for k, v in INDUSTRY_METRICS.items()
                ],
            },
        ],
    }
