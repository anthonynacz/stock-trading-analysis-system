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
    rotation_protected: bool = False
    protection_reasons: list[str] = []


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


# ── Watchlist rotate-out ─────────────────────────────────────────────────────

class RotationGate(BaseModel):
    blocked: bool = False
    reasons: list[str] = []


class RotationCandidate(BaseModel):
    ticker: str
    company_name: Optional[str] = None
    sector_name: Optional[str] = None
    composite_score: float
    reasons: list[str] = []
    cross_sector: bool = False


class RotationPreviewItem(BaseModel):
    ticker: str
    gate: RotationGate
    candidate: Optional[RotationCandidate] = None


class RotationPreviewRequest(BaseModel):
    tickers: list[str]


class RotationPreviewResponse(BaseModel):
    items: list[RotationPreviewItem] = []


class RotationCommitPair(BaseModel):
    remove: str
    add: str


class RotationCommitRequest(BaseModel):
    pairs: list[RotationCommitPair]


class RotationBlockedItem(BaseModel):
    ticker: str
    reasons: list[str] = []


class RotationCommitResponse(BaseModel):
    status: str  # committed / already_running / no_op
    committed: list[RotationCommitPair] = []
    blocked: list[RotationBlockedItem] = []
    analysis_started: bool = False


class RotationStatusResponse(BaseModel):
    running: bool = False
    started_at: Optional[str] = None
    tickers: dict[str, str] = {}  # ticker -> pending|analyzing|done|error
    errors: dict[str, str] = {}


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
    sector: Optional[str] = None
    conviction_score: Optional[Decimal] = None
    # User-personalized read-time fields. When the user's signal_group_weights
    # are all 1.0 (or the user has none), these mirror the raw values.
    weighted_conviction_score: Optional[Decimal] = None
    weighted_action: Optional[str] = None
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
    # Revision tracking — populated when this row was overwritten by a later
    # same-day run. revision_number=0 means first run (prior_* will be null).
    prior_action: Optional[str] = None
    prior_conviction_score: Optional[Decimal] = None
    revision_number: int = 0
    revised_at: Optional[datetime] = None
    revision_reason: Optional[str] = None
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
    gamma_estimate: Optional[Decimal] = None
    theta_estimate: Optional[Decimal] = None
    vega_estimate: Optional[Decimal] = None
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
    sector: Optional[str] = None
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
    # Deep-news enrichment (Tier 1)
    news_summary: Optional[str] = None
    news_clusters: Optional[dict[str, Any]] = None
    sentiment_timeline: Optional[list[dict[str, Any]]] = None
    top_headlines: Optional[list[dict[str, Any]]] = None
    # Bull / bear / watch synthesis (Tier 3)
    bull_case: Optional[str] = None
    bear_case: Optional[str] = None
    watch_text: Optional[str] = None
    # Status flags
    enrichment_status: Optional[str] = None
    enrichment_error: Optional[str] = None


# ── Deep options analysis ──────────────────────────────────────────────────


class DeepOptionsAnalysisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    company_name: Optional[str] = None
    analyzed_at: datetime
    stock_price: Optional[Decimal] = None
    iv_rank: Optional[Decimal] = None
    iv_percentile: Optional[Decimal] = None
    directional_bias: Optional[str] = None
    conviction_score: Optional[Decimal] = None
    verdict: Optional[str] = None
    greeks_detail: Optional[dict[str, Any]] = None
    vol_structure: Optional[dict[str, Any]] = None
    positioning: Optional[dict[str, Any]] = None
    liquidity: Optional[dict[str, Any]] = None
    strategy: Optional[dict[str, Any]] = None
    hidden_risks: Optional[list[dict[str, Any]]] = None
    rationale: Optional[str] = None


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
    recommendation_id: Optional[int] = None  # rec that prompted this position


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
    recommendation_id: Optional[int] = None
    # Computed fields (not in DB)
    unrealized_pnl: Optional[Decimal] = None
    unrealized_pnl_pct: Optional[float] = None
    days_to_expiry: Optional[int] = None
    is_on_watchlist: bool = False
    recommendation: Optional[RecommendationResponse] = None
    # Position-aware overlay flags. Each entry is a dict with:
    #   { code, severity (info|warn|critical), message }
    # Codes: SIGNAL_CONFLICT, DTE_WARNING, CONVICTION_DROP, STOP_BREACH,
    #        TARGET_HIT, EXPIRED.
    health_flags: list[dict] = []


# ── Multi-bagger scanner ────────────────────────────────────────────────────

class ScannerUniverseItem(BaseModel):
    id: int
    ticker: str
    company_name: Optional[str] = None
    theme: Optional[str] = None
    source: str
    is_active: bool
    added_at: datetime

    model_config = {"from_attributes": True}


class ScannerUniverseAddRequest(BaseModel):
    ticker: str
    theme: Optional[str] = None


class ScannerResultResponse(BaseModel):
    id: int
    run_date: date
    ticker: str
    company_name: Optional[str] = None
    theme: Optional[str] = None
    composite_score: Decimal
    tier: str
    signals_fired: int
    price: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None
    stock_age_months: Optional[Decimal] = None
    return_12m: Optional[Decimal] = None
    return_6m: Optional[Decimal] = None
    momentum_percentile: Optional[Decimal] = None
    rev_growth_latest: Optional[Decimal] = None
    rev_growth_prior: Optional[Decimal] = None
    rev_accel_pp: Optional[Decimal] = None
    gross_margin_latest: Optional[Decimal] = None
    gross_margin_prior: Optional[Decimal] = None
    margin_delta_pp: Optional[Decimal] = None
    avg_pt: Optional[Decimal] = None
    pt_chase_ratio: Optional[Decimal] = None
    revisions_90d: Optional[int] = None
    signals: Optional[list[dict]] = None
    rationale: Optional[str] = None

    model_config = {"from_attributes": True}


class ScannerDatesResponse(BaseModel):
    dates: list[date]


class ScannerRunResponse(BaseModel):
    status: str
    run_date: Optional[str] = None
    scored: Optional[int] = None
    hot: Optional[int] = None
    watch: Optional[int] = None


# ── Industry recommendations ────────────────────────────────────────────────

class IndustryRepresentativeTicker(BaseModel):
    ticker: str
    action: str
    conviction: float


class IndustryRecommendationResponse(BaseModel):
    id: int
    rec_date: date
    industry: str
    action: str
    conviction_score: Decimal
    # User-personalized: raw conviction × industry_weight from prefs.
    # Equals conviction_score when no weighting is applied.
    weighted_conviction_score: Optional[Decimal] = None
    industry_weight: Optional[Decimal] = None
    signal_count: int
    member_count: Optional[int] = None
    bullish_count: Optional[int] = None
    bearish_count: Optional[int] = None
    breadth_positive_pct: Optional[Decimal] = None
    breadth_above_50d_pct: Optional[Decimal] = None
    cap_weighted_conviction: Optional[Decimal] = None
    etf_symbol: Optional[str] = None
    etf_rsi_14: Optional[Decimal] = None
    etf_momentum_20d: Optional[Decimal] = None
    avg_news_sentiment: Optional[Decimal] = None
    news_article_count: Optional[int] = None
    geopolitical_points: Optional[Decimal] = None
    active_catalyst_count: Optional[int] = None
    representative_tickers: Optional[list[dict]] = None
    signals: Optional[list[dict]] = None
    rationale: Optional[str] = None

    model_config = {"from_attributes": True}


class IndustryHistoryPoint(BaseModel):
    rec_date: date
    action: str
    conviction_score: Decimal
    signal_count: int


class IndustryForwardPoint(BaseModel):
    """One day of the 5-day forward conviction projection."""

    forecast_date: date
    day_offset: int  # +1..+5, calendar days from today
    conviction_score: Decimal
    action: str  # mapped from band same as live recs


class IndustryTopComponent(BaseModel):
    """Snapshot of a top member ticker's recommendation + financials."""

    ticker: str
    company_name: Optional[str] = None
    action: str
    conviction: Decimal
    price: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None
    pe_ratio: Optional[Decimal] = None
    pct_from_52w_high: Optional[Decimal] = None
    sector_industry: Optional[str] = None  # yfinance fine-grained industry


class IndustryDetailResponse(BaseModel):
    industry: str
    latest: IndustryRecommendationResponse
    history: list[IndustryHistoryPoint]
    members: list[RecommendationResponse]  # today's recs for sector members
    executive_summary: Optional[str] = None
    top_components: list[IndustryTopComponent] = []
    forward_outlook: list[IndustryForwardPoint] = []


# ── Charts (dynamic visualization builder) ─────────────────────────────────

class ChartQueryRequest(BaseModel):
    """Untyped spec — each dataset handler validates its own keys."""

    dataset: str  # ticker_time_series | signal_breakdown | industry_comparison
    spec: dict[str, Any]


class ChartSeriesPoint(BaseModel):
    x: Any  # str (date / category) or number
    y: Optional[float] = None
    count: Optional[int] = None


class ChartSeries(BaseModel):
    name: str
    data: list[ChartSeriesPoint]
    metric_key: Optional[str] = None


class ChartResponse(BaseModel):
    dataset: str
    x_label: str
    y_label: str
    chart_type: str  # line | bar | scatter
    series: list[ChartSeries]
    meta: dict[str, Any] = {}
