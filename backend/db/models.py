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


# ── Users (multi-tenancy root) ──────────────────────────────────────────────

class User(Base):
    """Tenant root. Mirror of an external auth provider's user identity.

    `provider` + `provider_user_id` are the source-of-truth identifier; `email`
    is mirrored from the provider for human-readable display and for linking
    pre-provider rows (the legacy admin user) to a real auth identity later.

    The retrofit creates a single LEGACY_USER row and backfills every owned
    row to point at it; once frontend auth integration ships, real users
    upsert here on first JWT validation.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_provider_sub", "provider", "provider_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(50))  # supabase, clerk, auth0, legacy, dev
    provider_user_id: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")  # USER, ADMIN
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Subscriptions / entitlements / credits ──────────────────────────────────

class Subscription(Base):
    """One row per user representing their current paid (or free) tier.

    Stripe / Paddle webhooks update this row in place — `external_provider` +
    `external_subscription_id` are the link back to the billing system. A
    user with no subscription is implicitly on the FREE tier; the entitlements
    service treats absence as FREE.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_subscription_user"),
        Index("ix_subscription_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="FREE")
    # ACTIVE | TRIAL | PAST_DUE | CANCELED | EXPIRED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    external_provider: Mapped[Optional[str]] = mapped_column(String(20))  # stripe, paddle, manual
    external_subscription_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditBalance(Base):
    """Per-(user, feature) credit ledger summary.

    `balance` is the live remaining count. `last_grant_at` tracks when the
    monthly subscription quota was last applied so the entitlements service
    can lazily refresh on consume rather than via a cron job.
    """

    __tablename__ = "credit_balances"
    __table_args__ = (
        UniqueConstraint("user_id", "feature", name="uq_credit_user_feature"),
        Index("ix_credit_balance_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_grant_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CreditLedger(Base):
    """Append-only audit log of every credit grant / consumption / expiry.

    Every change to CreditBalance.balance is paired with one row here so we
    can answer "where did my credits go?" and reconcile against Stripe for
    purchased packs. balance_after is the post-change balance for at-a-glance
    inspection without re-summing the ledger.
    """

    __tablename__ = "credit_ledger"
    __table_args__ = (
        Index("ix_credit_ledger_user_feature", "user_id", "feature"),
        Index("ix_credit_ledger_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # signed
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    # monthly_grant | feature_use | credit_pack | tier_change | manual_adjust | expiry
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    ref_id: Mapped[Optional[str]] = mapped_column(String(255))  # stripe event id, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Alert log (dedup ledger for the alerts worker) ──────────────────────────

class AlertLog(Base):
    """Append-only log of alerts fired per user.

    The `key` column is a deterministic dedup key composed by the alerts
    worker — e.g. `rec_change:NVDA:2026-05-07:HOLD->BUY`. Re-runs of the
    scanner check this table before re-firing a duplicate.
    """

    __tablename__ = "alert_log"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_alert_log_user_key"),
        Index("ix_alert_log_user_fired", "user_id", "fired_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    ticker: Mapped[Optional[str]] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_channel: Mapped[Optional[str]] = mapped_column(String(20))


# ── User preferences (personalization) ─────────────────────────────────────

class UserPreferences(Base):
    """Per-user personalization config — single row per user.

    Each JSON column is a structured blob the preferences service validates
    and merges. Storing as JSON (not normalized child tables) is a deliberate
    trade — these are read together, written together, and the schema
    evolves quickly as we add personalization features. Validation lives in
    `services/preferences.py`, not at the DB layer.
    """

    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_pref_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # conservative | moderate | aggressive — drives strike recommender + risk-leaning weights
    risk_profile: Mapped[str] = mapped_column(String(20), nullable=False, default="moderate")
    # signal_group → multiplier in [0.0, 2.0]; 1.0 = neutral, 0 = mute, 2 = double
    signal_group_weights: Mapped[Optional[dict]] = mapped_column(JSON)
    # sector_name → multiplier in [0.0, 2.0]; same semantics, applied at view-time
    industry_weights: Mapped[Optional[dict]] = mapped_column(JSON)
    # list of tickers the user wants injected into the shared pipeline (capped by tier)
    custom_universe: Mapped[Optional[list]] = mapped_column(JSON)
    # structured alert toggles + thresholds (rec_change, conviction_breach, earnings, ...)
    alerts_config: Mapped[Optional[dict]] = mapped_column(JSON)
    # AM digest: enabled, send_time_utc, included sections
    digest_config: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Saved chart configs (per user) ──────────────────────────────────────────

class ChartConfig(Base):
    __tablename__ = "chart_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_chart_user_name"),
        Index("ix_chart_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Sectors ──────────────────────────────────────────────────────────────────

class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    max_stocks: Mapped[int] = mapped_column(Integer, default=6)

    watchlist_items: Mapped[list["Watchlist"]] = relationship(back_populates="sector")
    universe_stocks: Mapped[list["UniverseStock"]] = relationship(back_populates="sector")


# ── Universe stocks ─────────────────────────────────────────────────────────

class UniverseStock(Base):
    __tablename__ = "universe_stocks"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_universe_ticker"),
        Index("ix_universe_sector", "sector_id"),
        Index("ix_universe_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # SEED, DISCOVERED, MANUAL
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sector: Mapped["Sector"] = relationship(back_populates="universe_stocks")


# ── Discovery candidates ───────────────────────────────────────────────────

class DiscoveryCandidate(Base):
    __tablename__ = "discovery_candidates"
    __table_args__ = (
        Index("ix_discovery_status", "status"),
        Index("ix_discovery_discovered", "discovered_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    suggested_sector: Mapped[Optional[str]] = mapped_column(String(100))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # MOST_ACTIVE, GAINER, LOSER, NEWS_TRENDING
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    market_cap: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    avg_volume: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    change_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING, APPROVED, DISMISSED
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ── Watchlist ────────────────────────────────────────────────────────────────

class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("ticker", "added_date", name="uq_watchlist_ticker_date"),
        Index("ix_watchlist_active", "is_active"),
        Index("ix_watchlist_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    sector_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sectors.id"))
    # user_id nullable during retrofit; backfilled to legacy user by init_db.
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
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
    atm_iv: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))
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

    # Revision tracking — preserves the prior values on overwrite so the UI can
    # render a "revised" badge with hover diff. Single row per (date, ticker)
    # invariant is preserved; only the most recent prior is retained.
    prior_action: Mapped[Optional[str]] = mapped_column(String(20))
    prior_conviction_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revised_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

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
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    results: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("snapshot_date", "ticker", name="uq_strike_snap_date_ticker"),
        Index("ix_strike_snap_date", "snapshot_date"),
        Index("ix_strike_snap_user", "user_id"),
    )


# ── Research results ────────────────────────────────────────────────────────

class ResearchResult(Base):
    __tablename__ = "research_results"
    __table_args__ = (
        Index("ix_research_ticker", "ticker"),
        Index("ix_research_analyzed", "analyzed_at"),
        Index("ix_research_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
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

    # Deep-news enrichment (Tier 1) — clustered headlines, sentiment timeline,
    # source-quality breakdown, LLM-synthesized narrative summary.
    news_summary: Mapped[Optional[str]] = mapped_column(Text)
    news_clusters: Mapped[Optional[dict]] = mapped_column(JSON)
    sentiment_timeline: Mapped[Optional[list]] = mapped_column(JSON)
    top_headlines: Mapped[Optional[list]] = mapped_column(JSON)

    # Bull / bear / watch synthesis (Tier 3) — LLM-generated thesis trio.
    bull_case: Mapped[Optional[str]] = mapped_column(Text)
    bear_case: Mapped[Optional[str]] = mapped_column(Text)
    watch_text: Mapped[Optional[str]] = mapped_column(Text)

    # Enrichment status: PENDING / COMPLETE / PARTIAL / FAILED. PARTIAL means
    # news clustering succeeded but the LLM calls were skipped (no API key) or
    # failed for one of the two prompts.
    enrichment_status: Mapped[Optional[str]] = mapped_column(String(20))
    enrichment_error: Mapped[Optional[str]] = mapped_column(Text)


# ── Deep options analyses (expert-level single-ticker options report) ──────


class DeepOptionsAnalysis(Base):
    __tablename__ = "deep_options_analyses"
    __table_args__ = (
        Index("ix_deep_opt_ticker", "ticker"),
        Index("ix_deep_opt_analyzed", "analyzed_at"),
        Index("ix_deep_opt_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Headline
    stock_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    iv_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    iv_percentile: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    directional_bias: Mapped[Optional[str]] = mapped_column(String(20))  # BULLISH, BEARISH, NEUTRAL
    conviction_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    verdict: Mapped[Optional[str]] = mapped_column(String(50))  # BUY_CALL, SELL_CALL, BUY_PUT, SELL_PUT, etc.

    # Structured analysis blobs
    greeks_detail: Mapped[Optional[dict]] = mapped_column(JSON)  # per-expiry ATM greek table
    vol_structure: Mapped[Optional[dict]] = mapped_column(JSON)  # term structure, skew, expected move
    positioning: Mapped[Optional[dict]] = mapped_column(JSON)  # max pain, GEX, pin risk, P/C OI by exp
    liquidity: Mapped[Optional[dict]] = mapped_column(JSON)  # spread distribution, thin OI flags
    strategy: Mapped[Optional[dict]] = mapped_column(JSON)  # recommended strategy with legs
    hidden_risks: Mapped[Optional[list]] = mapped_column(JSON)  # list of risk dicts w/ severity
    rationale: Mapped[Optional[str]] = mapped_column(Text)


# ── Positions (user portfolio tracking) ────────────────────────────────────

class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_position_ticker", "ticker"),
        Index("ix_position_status", "status"),
        Index("ix_position_opened", "opened_at"),
        Index("ix_position_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    position_type: Mapped[str] = mapped_column(String(10), nullable=False)  # CALL, PUT, STOCK
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    current_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    strike_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    premium_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    expiry: Mapped[Optional[date]] = mapped_column(Date)
    stop_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(10), default="OPEN")  # OPEN, CLOSED
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    close_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    realized_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)


# ── Multi-bagger scanner (positional, 3-12mo horizon) ──────────────────────

class MultibaggerUniverse(Base):
    __tablename__ = "multibagger_universe"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_mbu_ticker"),
        Index("ix_mbu_active", "is_active"),
        Index("ix_mbu_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    theme: Mapped[Optional[str]] = mapped_column(String(80))  # e.g. AI_MEMORY, ROBOTICS, NUCLEAR, GLP1
    source: Mapped[str] = mapped_column(String(20), default="SEED")  # SEED, MANUAL
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MultibaggerSnapshot(Base):
    """One row per (run_date, ticker) — weekly positional signal snapshot."""

    __tablename__ = "multibagger_snapshot"
    __table_args__ = (
        UniqueConstraint("run_date", "ticker", name="uq_mbs_date_ticker"),
        Index("ix_mbs_run_date", "run_date"),
        Index("ix_mbs_tier", "tier"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    theme: Mapped[Optional[str]] = mapped_column(String(80))

    # Composite + classification
    composite_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    tier: Mapped[str] = mapped_column(String(12), nullable=False)  # HOT, WATCH, MONITOR, IGNORE
    signals_fired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Raw observations (nullable — some data sources may be unavailable)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    market_cap: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    stock_age_months: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 1))
    return_12m: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))  # decimal (e.g. 3.5 = +350%)
    return_6m: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    momentum_percentile: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    # Ratios intentionally wide — small-denominator inputs can produce very
    # large quotients (e.g. a company going from $1M to $500M revenue = 500x).
    rev_growth_latest: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))  # YoY latest Q
    rev_growth_prior: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))  # YoY prior Q
    rev_accel_pp: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))  # stored as decimal (pp/100)
    gross_margin_latest: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    gross_margin_prior: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    margin_delta_pp: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    avg_pt: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    pt_chase_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))  # price / avg_pt
    revisions_90d: Mapped[Optional[int]] = mapped_column(Integer)

    # Per-signal details (JSON: [{signal, points, detail}, ...])
    signals: Mapped[Optional[list]] = mapped_column(JSON)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Industry recommendations (sector-level BUY/HOLD/SELL) ──────────────────

class IndustryRecommendation(Base):
    """One row per (rec_date, industry) — industry-level signal stack.

    The industry is the unit, not a ticker. Signals are industry-native
    (breadth, cap-weighted conviction, sector ETF technicals, aggregated
    news sentiment, geopolitical impact, catalyst density) — not just an
    average of ticker convictions.
    """

    __tablename__ = "industry_recommendations"
    __table_args__ = (
        UniqueConstraint("rec_date", "industry", name="uq_ind_rec_date_industry"),
        Index("ix_ind_rec_date", "rec_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rec_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)

    # Classification
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # BUY, HOLD, SELL, STRONG_BUY, STRONG_SELL
    conviction_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Raw observations
    member_count: Mapped[Optional[int]] = mapped_column(Integer)
    bullish_count: Mapped[Optional[int]] = mapped_column(Integer)  # conviction >= 30
    bearish_count: Mapped[Optional[int]] = mapped_column(Integer)  # conviction <= -16
    breadth_positive_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    breadth_above_50d_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    cap_weighted_conviction: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    etf_symbol: Mapped[Optional[str]] = mapped_column(String(10))
    etf_rsi_14: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    etf_momentum_20d: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    avg_news_sentiment: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    news_article_count: Mapped[Optional[int]] = mapped_column(Integer)
    geopolitical_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    active_catalyst_count: Mapped[Optional[int]] = mapped_column(Integer)

    # Representative tickers (top 3 by market cap or conviction magnitude)
    representative_tickers: Mapped[Optional[list]] = mapped_column(JSON)

    # Per-signal details: [{signal, points, detail}, ...]
    signals: Mapped[Optional[list]] = mapped_column(JSON)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
