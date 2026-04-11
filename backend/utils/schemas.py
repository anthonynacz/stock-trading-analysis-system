from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: str
    sector: Optional[str] = None
    added_date: date
    is_active: bool
    is_manual: bool = False
    is_locked: bool = False
    entry_reason: Optional[str] = None
    status: str  # NEW_ENTRANT / EXISTING / REMOVED


class WatchlistAddRequest(BaseModel):
    ticker: str


class WatchlistChangeItem(BaseModel):
    ticker: str
    sector: Optional[str] = None
    reason: Optional[str] = None


class WatchlistChangesResponse(BaseModel):
    date: date
    entrants: list[WatchlistChangeItem] = []
    exiters: list[WatchlistChangeItem] = []


class PipelineDatesResponse(BaseModel):
    dates: list[date] = []


class AnalystRatingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    analyst_firm: str
    analyst_name: Optional[str] = None
    rating_type: Optional[str] = None
    previous_rating: Optional[str] = None
    new_rating: Optional[str] = None
    previous_pt: Optional[Decimal] = None
    new_pt: Optional[Decimal] = None
    firm_tier: Optional[int] = None
    published_at: Optional[datetime] = None
    impact_score: Optional[Decimal] = None
    source_url: Optional[str] = None


class EarningsCalendarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    earnings_date: date
    earnings_time: Optional[str] = None
    fiscal_quarter: Optional[str] = None
    consensus_eps: Optional[Decimal] = None
    actual_eps: Optional[Decimal] = None
    surprise_pct: Optional[Decimal] = None
    catalyst_window_start: Optional[date] = None
    catalyst_window_end: Optional[date] = None
    window_status: str  # APPROACHING / ACTIVE / POST / NONE


class NewsTickerRelevanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    relevance_score: float
    relevance_source: str


class MarketNewsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: Optional[str] = None
    headline: str
    summary: Optional[str] = None
    source: Optional[str] = None
    category: Optional[str] = None
    sentiment_score: Optional[Decimal] = None
    impact_level: Optional[str] = None
    published_at: Optional[datetime] = None
    source_url: Optional[str] = None
    related_tickers: list[NewsTickerRelevanceResponse] = []

    @classmethod
    def from_news(cls, news) -> "MarketNewsResponse":
        """Build response from a MarketNews ORM instance."""
        return cls(
            id=news.id,
            ticker=news.ticker,
            headline=news.headline,
            summary=news.summary,
            source=news.source,
            category=news.category,
            sentiment_score=news.sentiment_score,
            impact_level=news.impact_level,
            published_at=news.published_at,
            source_url=news.source_url,
            related_tickers=[
                NewsTickerRelevanceResponse.model_validate(r)
                for r in news.ticker_relevances
            ] if hasattr(news, "ticker_relevances") and news.ticker_relevances else [],
        )


class OptionsSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    snapshot_time: datetime
    stock_price: Optional[Decimal] = None
    iv_rank: Optional[Decimal] = None
    iv_percentile: Optional[Decimal] = None
    put_call_ratio: Optional[Decimal] = None
    total_call_volume: Optional[int] = None
    total_put_volume: Optional[int] = None
    unusual_activity: Optional[bool] = None
    unusual_activity_detail: Optional[str] = None


class SuggestedOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contract_type: Optional[str] = None
    strike: Optional[Decimal] = None
    expiry: Optional[date] = None
    premium_estimate: Optional[Decimal] = None
    delta_estimate: Optional[Decimal] = None
    strategy: Optional[str] = None
    strategy_rationale: Optional[str] = None
    days_to_expiry: Optional[int] = None
    breakeven_price: Optional[Decimal] = None


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recommendation_date: date
    ticker: str
    action: str
    conviction_score: Optional[Decimal] = None
    signal_count: Optional[int] = None
    signals: Optional[list[dict[str, Any]]] = None
    rationale: Optional[str] = None
    catalyst_type: Optional[str] = None
    entry_strategy: Optional[str] = None
    exit_rules: Optional[str] = None
    risk_level: Optional[str] = None
    current_price: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    suggested_options: list[SuggestedOptionResponse] = []


class CatalystResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    earnings_date: Optional[date] = None
    earnings_time: Optional[str] = None
    fiscal_quarter: Optional[str] = None
    consensus_eps: Optional[Decimal] = None
    days_until: Optional[int] = None
    window_status: str  # APPROACHING / ACTIVE / POST


class SystemStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    db_connected: bool
    scheduler_running: bool
    active_watchlist_count: int
    last_refresh: dict[str, Optional[datetime]]
    version: str


class StrikeRecommendation(BaseModel):
    strike: Decimal
    expiry: date
    premium_estimate: Decimal
    delta_estimate: Decimal
    breakeven: Decimal
    days_to_expiry: int
    open_interest: int
    explanation: str


class StrikeRecommenderResponse(BaseModel):
    ticker: str
    current_price: Optional[Decimal] = None
    risk_level: str
    max_budget: Optional[Decimal] = None
    recommended_call: Optional[StrikeRecommendation] = None
    recommended_put: Optional[StrikeRecommendation] = None


class StrikeSnapshotSaveRequest(BaseModel):
    budget: Optional[float] = None
    results: dict[str, Any]  # ticker -> StrikeAllResult


# ── Trend data ──────────────────────────────────────────────────────────────


class TrendDataPoint(BaseModel):
    date: date
    price: Optional[float] = None
    target_price: Optional[float] = None
    conviction: Optional[float] = None
    signal_count: Optional[int] = None
    sentiment: Optional[float] = None
    article_count: Optional[int] = None
    price_sma: Optional[float] = None
    conviction_sma: Optional[float] = None
    signal_count_sma: Optional[float] = None
    sentiment_sma: Optional[float] = None


class TrendResponse(BaseModel):
    ticker: str
    days: int
    sma_window: int
    data: list[TrendDataPoint]


# ── Research results ───────────────────────────────────────────────────────


class ResearchResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: Optional[str] = None
    analyzed_at: datetime
    action: str
    conviction_score: Optional[Decimal] = None
    signal_count: Optional[int] = None
    signals: Optional[list[dict[str, Any]]] = None
    rationale: Optional[str] = None
    catalyst_type: Optional[str] = None
    entry_strategy: Optional[str] = None
    exit_rules: Optional[str] = None
    risk_level: Optional[str] = None
    current_price: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    options_data: Optional[dict[str, Any]] = None
    suggested_options: Optional[list[dict[str, Any]]] = None


# ── Universe management ──────────────────────────────────────────────────


class UniverseStockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: Optional[str] = None
    sector: Optional[str] = None
    source: str
    is_active: bool
    added_at: datetime


class UniverseAddRequest(BaseModel):
    ticker: str
    sector: str


class UniverseSectorGroup(BaseModel):
    name: str
    stock_count: int
    stocks: list[UniverseStockResponse]


class UniverseSummaryResponse(BaseModel):
    sectors: list[UniverseSectorGroup]
    total_stocks: int
    pending_candidates: int


class DiscoveryCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: Optional[str] = None
    suggested_sector: Optional[str] = None
    discovered_at: datetime
    source: str
    score: Optional[float] = None
    market_cap: Optional[float] = None
    avg_volume: Optional[float] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    rationale: Optional[str] = None
    status: str


class CandidateApproveRequest(BaseModel):
    sector: str


# ── Positions ────────────────────────────────────────────────────────────


class PositionCreateRequest(BaseModel):
    ticker: str
    position_type: str  # CALL, PUT, STOCK
    quantity: int = 1
    entry_price: Decimal
    strike_price: Optional[Decimal] = None
    premium_paid: Optional[Decimal] = None
    expiry: Optional[date] = None
    stop_loss: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    notes: Optional[str] = None


class PositionUpdateRequest(BaseModel):
    stop_loss: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    notes: Optional[str] = None
    current_price: Optional[Decimal] = None


class PositionCloseRequest(BaseModel):
    close_price: Decimal
    notes: Optional[str] = None


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: Optional[str] = None
    position_type: str
    quantity: int
    entry_price: Decimal
    current_price: Optional[Decimal] = None
    strike_price: Optional[Decimal] = None
    premium_paid: Optional[Decimal] = None
    expiry: Optional[date] = None
    stop_loss: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_price: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    notes: Optional[str] = None
    # Computed fields (not in DB)
    unrealized_pnl: Optional[Decimal] = None
    unrealized_pnl_pct: Optional[float] = None
    days_to_expiry: Optional[int] = None
    is_on_watchlist: bool = False
    recommendation: Optional[RecommendationResponse] = None
