from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Sectors ──────────────────────────────────────────────────────────────────

class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    max_stocks: Mapped[int] = mapped_column(Integer, default=6)

    watchlist_items: Mapped[list["Watchlist"]] = relationship(back_populates="sector")


# ── Watchlist ────────────────────────────────────────────────────────────────

class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("ticker", "added_date", name="uq_watchlist_ticker_date"),
        Index("ix_watchlist_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    sector_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sectors.id"))
    added_date: Mapped[date] = mapped_column(Date, default=date.today)
    removed_date: Mapped[Optional[date]] = mapped_column(Date)
    entry_reason: Mapped[Optional[str]] = mapped_column(Text)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    sector: Mapped[Optional["Sector"]] = relationship(back_populates="watchlist_items")


# ── Watchlist daily snapshot ─────────────────────────────────────────────────

class WatchlistDailySnapshot(Base):
    __tablename__ = "watchlist_daily_snapshot"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "ticker", name="uq_snapshot_date_ticker"),
        Index("ix_snapshot_date", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, default=date.today)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # EXISTING, NEW_ENTRANT, REMOVED
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Analyst ratings ──────────────────────────────────────────────────────────

class AnalystRating(Base):
    __tablename__ = "analyst_ratings"
    __table_args__ = (
        Index("ix_rating_ticker_date", "ticker", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    analyst_firm: Mapped[str] = mapped_column(String(255), nullable=False)
    analyst_name: Mapped[Optional[str]] = mapped_column(String(255))
    rating_type: Mapped[str] = mapped_column(String(50), nullable=False)  # TIER_CHANGE, PT_CHANGE, INITIATION, REITERATION
    previous_rating: Mapped[Optional[str]] = mapped_column(String(50))
    new_rating: Mapped[Optional[str]] = mapped_column(String(50))
    previous_pt: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    new_pt: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    firm_tier: Mapped[Optional[int]] = mapped_column(Integer)  # 1, 2, 3
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    impact_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    source_url: Mapped[Optional[str]] = mapped_column(Text)


# ── Earnings calendar ────────────────────────────────────────────────────────

class EarningsCalendar(Base):
    __tablename__ = "earnings_calendar"
    __table_args__ = (
        UniqueConstraint("ticker", "earnings_date", name="uq_earnings_ticker_date"),
        Index("ix_earnings_date", "earnings_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    earnings_date: Mapped[date] = mapped_column(Date, nullable=False)
    earnings_time: Mapped[Optional[str]] = mapped_column(String(50))  # BMO, AMC, or EPS estimate
    fiscal_quarter: Mapped[Optional[str]] = mapped_column(String(50))  # Quarter or revenue estimate
    consensus_eps: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    actual_eps: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    consensus_revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    actual_revenue: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2))
    surprise_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    catalyst_window_start: Mapped[Optional[date]] = mapped_column(Date)
    catalyst_window_end: Mapped[Optional[date]] = mapped_column(Date)


# ── Market news ──────────────────────────────────────────────────────────────

class MarketNews(Base):
    __tablename__ = "market_news"
    __table_args__ = (
        Index("ix_news_ticker", "ticker"),
        Index("ix_news_published", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[Optional[str]] = mapped_column(String(10))
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    category: Mapped[Optional[str]] = mapped_column(String(50))  # EARNINGS, ANALYST, MACRO, SECTOR, INSIDER, PRODUCT
    sentiment_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 3))
    impact_level: Mapped[Optional[str]] = mapped_column(String(20))  # HIGH, MEDIUM, LOW
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_url: Mapped[Optional[str]] = mapped_column(Text)

    ticker_relevances: Mapped[list["NewsTickerRelevance"]] = relationship(
        back_populates="news", cascade="all, delete-orphan"
    )


class NewsTickerRelevance(Base):
    """Junction table linking news articles to tickers with relevance scores."""

    __tablename__ = "news_ticker_relevance"
    __table_args__ = (
        UniqueConstraint("news_id", "ticker", name="uq_news_ticker_rel"),
        Index("ix_ntr_ticker", "ticker"),
        Index("ix_ntr_news_id", "news_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    news_id: Mapped[int] = mapped_column(
        ForeignKey("market_news.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    relevance_score: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    relevance_source: Mapped[str] = mapped_column(String(20), nullable=False)

    news: Mapped["MarketNews"] = relationship(back_populates="ticker_relevances")


# ── Options snapshots ────────────────────────────────────────────────────────

class OptionsSnapshot(Base):
    __tablename__ = "options_snapshots"
    __table_args__ = (
        Index("ix_options_ticker_time", "ticker", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stock_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    iv_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    iv_percentile: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    put_call_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 3))
    total_call_volume: Mapped[Optional[int]] = mapped_column(Integer)
    total_put_volume: Mapped[Optional[int]] = mapped_column(Integer)
    avg_call_volume: Mapped[Optional[int]] = mapped_column(Integer)
    total_call_oi: Mapped[Optional[int]] = mapped_column(Integer)
    total_put_oi: Mapped[Optional[int]] = mapped_column(Integer)
    unusual_activity: Mapped[bool] = mapped_column(Boolean, default=False)
    unusual_activity_detail: Mapped[Optional[str]] = mapped_column(Text)


# ── Recommendations ──────────────────────────────────────────────────────────

class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("recommendation_date", "ticker", name="uq_rec_date_ticker"),
        Index("ix_rec_date_conviction", "recommendation_date", "conviction_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_date: Mapped[date] = mapped_column(Date, default=date.today)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
    conviction_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    signal_count: Mapped[Optional[int]] = mapped_column(Integer)
    signals: Mapped[Optional[dict]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    catalyst_type: Mapped[Optional[str]] = mapped_column(String(50))
    entry_strategy: Mapped[Optional[str]] = mapped_column(Text)  # PRE_POSITION, REACTIVE, WAIT
    exit_rules: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))  # LOW, MEDIUM, HIGH
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    stop_loss_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    suggested_options: Mapped[list["SuggestedOption"]] = relationship(back_populates="recommendation")


# ── Suggested options ────────────────────────────────────────────────────────

class SuggestedOption(Base):
    __tablename__ = "suggested_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(4), nullable=False)  # CALL, PUT
    strike: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    premium_estimate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    delta_estimate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 3))
    strategy: Mapped[Optional[str]] = mapped_column(String(50))  # NAKED_CALL, CALL_DEBIT_SPREAD, etc.
    strategy_rationale: Mapped[Optional[str]] = mapped_column(Text)
    days_to_expiry: Mapped[Optional[int]] = mapped_column(Integer)
    breakeven_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    recommendation: Mapped["Recommendation"] = relationship(back_populates="suggested_options")


# ── Strike snapshots ─────────────────────────────────────────────────────────

class StrikeSnapshot(Base):
    __tablename__ = "strike_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    results: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("snapshot_date", "ticker", name="uq_strike_snap_date_ticker"),
        Index("ix_strike_snap_date", "snapshot_date"),
    )


# ── Research results ────────────────────────────────────────────────────────

class ResearchResult(Base):
    __tablename__ = "research_results"
    __table_args__ = (
        Index("ix_research_ticker", "ticker"),
        Index("ix_research_analyzed", "analyzed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    conviction_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    signal_count: Mapped[Optional[int]] = mapped_column(Integer)
    signals: Mapped[Optional[dict]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    catalyst_type: Mapped[Optional[str]] = mapped_column(String(50))
    entry_strategy: Mapped[Optional[str]] = mapped_column(Text)
    exit_rules: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    stop_loss_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    options_data: Mapped[Optional[dict]] = mapped_column(JSON)
    suggested_options: Mapped[Optional[list]] = mapped_column(JSON)
