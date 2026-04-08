"""EdgeFlow API routes."""

from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import Date, cast, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.connection import get_db
from db.models import (
    EarningsCalendar,
    MarketNews,
    NewsTickerRelevance,
    OptionsSnapshot,
    Recommendation,
    ResearchResult,
    Sector,
    StrikeSnapshot,
    Watchlist,
    WatchlistDailySnapshot,
)
from utils.data_sources import DataSourceClient
from utils.schemas import (
    CatalystResponse,
    MarketNewsResponse,
    OptionsSnapshotResponse,
    PipelineDatesResponse,
    RecommendationResponse,
    ResearchResultResponse,
    StrikeSnapshotSaveRequest,
    SystemStatusResponse,
    TrendDataPoint,
    TrendResponse,
    WatchlistAddRequest,
    WatchlistChangeItem,
    WatchlistChangesResponse,
    WatchlistItemResponse,
)

router = APIRouter(prefix="/api")


# ── Watchlist ───────────────────────────────────────────────────────────────


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
        return response
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
        return response


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

    # Fetch company name via yfinance
    client = DataSourceClient()
    try:
        loop = asyncio.get_event_loop()
        stock_data = await loop.run_in_executor(None, client.get_stock_data, ticker)
        company_name = stock_data.get("company_name", ticker) if stock_data else ticker
    finally:
        client.close()

    wl = Watchlist(
        ticker=ticker,
        company_name=company_name,
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
        sector=None,
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

    return WatchlistChangesResponse(
        date=effective_date,
        entrants=[
            WatchlistChangeItem(ticker=c.ticker, sector=c.sector)
            for c in changes if c.status == "NEW_ENTRANT"
        ],
        exiters=[
            WatchlistChangeItem(ticker=c.ticker, sector=c.sector)
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
    return result.scalars().all()


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
    return recs


# ── News ────────────────────────────────────────────────────────────────────


@router.get("/news")
async def get_news(
    ticker: str | None = Query(None),
    mode: str = Query("general"),
    category: str | None = Query(None),
    impact_level: str | None = Query(None),
    min_relevance: float = Query(0.3, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
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
    elif mode == "watchlist":
        # Find news relevant to any active watchlist ticker
        active_result = await db.execute(
            select(Watchlist.ticker).where(Watchlist.is_active.is_(True))
        )
        watchlist_tickers = [row[0] for row in active_result.all()]
        if not watchlist_tickers:
            return []
        stmt = (
            select(MarketNews)
            .join(NewsTickerRelevance)
            .where(
                NewsTickerRelevance.ticker.in_(watchlist_tickers),
                NewsTickerRelevance.relevance_score >= min_relevance,
            )
            .options(selectinload(MarketNews.ticker_relevances))
            .order_by(MarketNews.published_at.desc())
            .limit(limit)
        )
    else:
        # General mode — original behavior
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


# ── Strike snapshots ──────────────────────────────────────────────────────


@router.post("/strikes/snapshots", status_code=201)
async def save_strike_snapshot(
    body: StrikeSnapshotSaveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save current strike scan results for historical review."""
    from decimal import Decimal

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


# ── Research ───────────────────────────────────────────────────────────────


@router.post("/research/{ticker}", response_model=ResearchResultResponse)
async def analyze_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    """Run a full on-demand analysis for any ticker."""
    import asyncio
    import re
    from decimal import Decimal

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
        signals=result["signals"],
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
        options_data=result["options_data"],
        suggested_options=result["suggested_contracts"],
    )
    db.add(research)
    await db.commit()
    await db.refresh(research)

    return ResearchResultResponse.model_validate(research)


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
    return [ResearchResultResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/research/{id_}", response_model=ResearchResultResponse)
async def get_research(id_: int, db: AsyncSession = Depends(get_db)):
    """Get a single research result by ID."""
    result = await db.get(ResearchResult, id_)
    if not result:
        raise HTTPException(status_code=404, detail="Research result not found")
    return ResearchResultResponse.model_validate(result)


@router.delete("/research/{id_}")
async def delete_research(id_: int, db: AsyncSession = Depends(get_db)):
    """Delete a research result."""
    result = await db.get(ResearchResult, id_)
    if not result:
        raise HTTPException(status_code=404, detail="Research result not found")
    await db.delete(result)
    await db.commit()
    return {"status": "deleted"}
