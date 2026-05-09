"""Alerts worker.

Periodic scanner that detects per-user trigger conditions, dedups via
`alert_log`, and dispatches via the delivery layer (currently a structured-
log stub at `_dispatch_alert`). Cron runs every 30 minutes during market
hours (06:00–17:30 ET) — see services/scheduler.py.

Triggers (one entry in alerts_config gates each):
  - rec_change         — today's action differs from yesterday's for a held/watched ticker
  - conviction_breach  — abs(today's weighted conviction) >= alerts_config.conviction_threshold
  - earnings_proximity — earnings within alerts_config.earnings_days_before for held/watched
  - unusual_flow       — options_snapshots.unusual_activity flips True
  - news_spike         — news_sentiment magnitude >= 0.5 today
  - insider_filing     — new analyst_ratings or insider entries (not yet — placeholder)

Tier gating: an alert fires only if the user's tier permits it
(`ALERT_KEYS[k].tier_min` from services/preferences.py). Alerts saved by a
FREE user are persisted to prefs but never delivered until they upgrade.

Dedup keys are deterministic so duplicate detection is cheap. Examples:
  rec_change:NVDA:2026-05-07:HOLD->BUY   (one fire per same-day flip)
  conviction_breach:NVDA:2026-05-07:+72  (one fire per breach value)
  earnings_proximity:NVDA:2026-05-12:T-3 (one fire per (ticker, earnings_date, days))
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import (
    AlertLog,
    EarningsCalendar,
    MarketNews,
    NewsTickerRelevance,
    OptionsSnapshot,
    Position,
    Recommendation,
    User,
    UserPreferences,
    Watchlist,
)
from services.entitlements import (
    get_entitlements_for,
    get_subscription,
    get_tier_for,
)
from services.preferences import (
    ALERT_KEYS,
    apply_signal_weights,
    get_signal_group_weights,
)

logger = logging.getLogger(__name__)


# Tier ordering (mirror of frontend SettingsPage TIER_RANK)
_TIER_RANK = {"FREE": 0, "STARTER": 1, "PRO": 2, "PREMIUM": 3, "ADMIN": 99}


def _tier_meets_min(current: str, min_tier: str | None) -> bool:
    if not min_tier:
        return True
    return _TIER_RANK.get(current, 0) >= _TIER_RANK.get(min_tier, 0)


_ALERT_TIER_MIN: dict[str, str] = {a["key"]: a.get("tier_min") for a in ALERT_KEYS}


# ── Dispatch ────────────────────────────────────────────────────────────────

async def _dispatch_alert(
    session: AsyncSession,
    user: User,
    alert_type: str,
    ticker: str | None,
    key: str,
    payload: dict[str, Any],
) -> bool:
    """Insert into alert_log (dedup) and stub-deliver. Returns True iff
    a NEW alert was recorded (false on dedup hit).

    The unique constraint on (user_id, key) is the dedup primitive — we
    rely on IntegrityError to short-circuit duplicates without a SELECT-
    then-INSERT race.
    """
    # Check existence first (cheap; avoids loud rollback log noise on dup)
    existing = await session.execute(
        select(AlertLog.id).where(
            AlertLog.user_id == user.id, AlertLog.key == key,
        )
    )
    if existing.scalars().first() is not None:
        return False

    log = AlertLog(
        user_id=user.id,
        alert_type=alert_type,
        ticker=ticker,
        key=key,
        payload=payload,
        delivered=True,  # stub-delivered immediately; flip to False if delivery fails
        delivery_channel="email_log_stub",
    )
    session.add(log)
    await session.flush()
    logger.info(
        "ALERT_STUB user=%s type=%s ticker=%s key=%s payload=%s",
        user.email, alert_type, ticker, key, payload,
    )
    return True


# ── Detectors ───────────────────────────────────────────────────────────────

async def _scan_rec_change(
    session: AsyncSession, user: User, scope_tickers: set[str], weights: dict[str, float]
) -> int:
    """Today's weighted_action != yesterday's weighted_action for a ticker
    in the user's scope. Fires once per (ticker, date, transition)."""
    if not scope_tickers:
        return 0
    today = date.today()
    yesterday = today - timedelta(days=1)
    res = await session.execute(
        select(Recommendation).where(
            Recommendation.ticker.in_(scope_tickers),
            Recommendation.recommendation_date.in_([today, yesterday]),
        )
    )
    by_ticker_date: dict[tuple[str, date], Recommendation] = {}
    for r in res.scalars():
        by_ticker_date[(r.ticker, r.recommendation_date)] = r

    fires = 0
    for ticker in scope_tickers:
        today_rec = by_ticker_date.get((ticker, today))
        prev_rec = by_ticker_date.get((ticker, yesterday))
        if today_rec is None or prev_rec is None:
            continue
        # Apply user's signal weights to both for comparable action values
        _, today_action, _ = apply_signal_weights(
            float(today_rec.conviction_score or 0), today_rec.action, today_rec.signals, weights,
        )
        _, prev_action, _ = apply_signal_weights(
            float(prev_rec.conviction_score or 0), prev_rec.action, prev_rec.signals, weights,
        )
        if today_action == prev_action:
            continue
        key = f"rec_change:{ticker}:{today.isoformat()}:{prev_action}->{today_action}"
        sent = await _dispatch_alert(
            session, user, "rec_change", ticker, key,
            {"prev": prev_action, "now": today_action},
        )
        if sent:
            fires += 1
    return fires


async def _scan_conviction_breach(
    session: AsyncSession,
    user: User,
    scope_tickers: set[str],
    weights: dict[str, float],
    threshold: int,
) -> int:
    """Today's weighted conviction crosses |threshold| for a scope ticker."""
    if not scope_tickers or threshold <= 0:
        return 0
    today = date.today()
    res = await session.execute(
        select(Recommendation).where(
            Recommendation.ticker.in_(scope_tickers),
            Recommendation.recommendation_date == today,
        )
    )
    fires = 0
    for r in res.scalars():
        new_conv, _, _ = apply_signal_weights(
            float(r.conviction_score or 0), r.action, r.signals, weights,
        )
        if abs(new_conv) < threshold:
            continue
        key = f"conviction_breach:{r.ticker}:{today.isoformat()}:{int(round(new_conv))}"
        sent = await _dispatch_alert(
            session, user, "conviction_breach", r.ticker, key,
            {"conviction": round(new_conv, 1), "threshold": threshold},
        )
        if sent:
            fires += 1
    return fires


async def _scan_earnings_proximity(
    session: AsyncSession,
    user: User,
    scope_tickers: set[str],
    days_before: int,
) -> int:
    """Earnings within `days_before` days for a scope ticker."""
    if not scope_tickers or days_before < 0:
        return 0
    today = date.today()
    horizon = today + timedelta(days=days_before)
    res = await session.execute(
        select(EarningsCalendar).where(
            EarningsCalendar.ticker.in_(scope_tickers),
            EarningsCalendar.earnings_date.between(today, horizon),
        )
    )
    fires = 0
    for ec in res.scalars():
        days_until = (ec.earnings_date - today).days
        key = f"earnings_proximity:{ec.ticker}:{ec.earnings_date.isoformat()}:T-{days_until}"
        sent = await _dispatch_alert(
            session, user, "earnings_proximity", ec.ticker, key,
            {"earnings_date": ec.earnings_date.isoformat(), "days_until": days_until,
             "fiscal_quarter": ec.fiscal_quarter},
        )
        if sent:
            fires += 1
    return fires


async def _scan_unusual_flow(
    session: AsyncSession, user: User, scope_tickers: set[str]
) -> int:
    """Today's options snapshot has unusual_activity=True. One fire per
    (ticker, date) — won't repeat on subsequent scans the same day."""
    if not scope_tickers:
        return 0
    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    res = await session.execute(
        select(OptionsSnapshot).where(
            OptionsSnapshot.ticker.in_(scope_tickers),
            OptionsSnapshot.unusual_activity.is_(True),
            OptionsSnapshot.snapshot_time >= today_start,
        )
    )
    fires = 0
    seen: set[str] = set()
    for snap in res.scalars():
        if snap.ticker in seen:
            continue
        seen.add(snap.ticker)
        key = f"unusual_flow:{snap.ticker}:{date.today().isoformat()}"
        sent = await _dispatch_alert(
            session, user, "unusual_flow", snap.ticker, key,
            {"detail": snap.unusual_activity_detail,
             "call_volume": snap.total_call_volume,
             "put_volume": snap.total_put_volume},
        )
        if sent:
            fires += 1
    return fires


async def _scan_news_spike(
    session: AsyncSession, user: User, scope_tickers: set[str]
) -> int:
    """News with FinBERT |sentiment| >= 0.5 today, mapped to scope tickers
    via news_ticker_relevance."""
    if not scope_tickers:
        return 0
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    res = await session.execute(
        select(MarketNews, NewsTickerRelevance.ticker)
        .join(NewsTickerRelevance, NewsTickerRelevance.news_id == MarketNews.id)
        .where(
            NewsTickerRelevance.ticker.in_(scope_tickers),
            MarketNews.published_at >= cutoff,
        )
    )
    fires = 0
    for news, ticker in res.all():
        score = float(news.sentiment_score) if news.sentiment_score is not None else 0.0
        if abs(score) < 0.5:
            continue
        key = f"news_spike:{ticker}:{news.id}"
        sent = await _dispatch_alert(
            session, user, "news_spike", ticker, key,
            {"headline": (news.headline or "")[:200],
             "sentiment": round(score, 2),
             "source": news.source},
        )
        if sent:
            fires += 1
    return fires


# ── Per-user driver ─────────────────────────────────────────────────────────

async def _scope_tickers(session: AsyncSession, user: User) -> set[str]:
    """Tickers the user "cares about" — held positions + active watchlist
    (which already includes any custom-universe injections)."""
    pos_res = await session.execute(
        select(Position.ticker).where(
            Position.user_id == user.id,
            Position.status == "OPEN",
        )
    )
    held = {row[0] for row in pos_res.all()}
    wl_res = await session.execute(
        select(Watchlist.ticker).where(Watchlist.is_active.is_(True))
    )
    watched = {row[0] for row in wl_res.all()}
    return held | watched


async def _scan_for_user(session: AsyncSession, user: User) -> dict[str, int]:
    pref_res = await session.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    )
    pref = pref_res.scalars().first()
    if pref is None:
        return {}

    cfg = pref.alerts_config or {}
    sub = await get_subscription(session, user)
    tier = get_tier_for(user, sub)
    ent = get_entitlements_for(tier)

    # If alerts feature isn't unlocked at all, skip — user can save but won't deliver
    if not ent.alerts_enabled and tier != "ADMIN":
        return {}

    scope = await _scope_tickers(session, user)
    weights = get_signal_group_weights(pref)
    counts: dict[str, int] = {}

    if cfg.get("rec_change") and _tier_meets_min(tier, _ALERT_TIER_MIN.get("rec_change")):
        counts["rec_change"] = await _scan_rec_change(session, user, scope, weights)

    if cfg.get("conviction_breach") and _tier_meets_min(tier, _ALERT_TIER_MIN.get("conviction_breach")):
        threshold = int(cfg.get("conviction_threshold", 60))
        counts["conviction_breach"] = await _scan_conviction_breach(
            session, user, scope, weights, threshold
        )

    if cfg.get("earnings_proximity") and _tier_meets_min(tier, _ALERT_TIER_MIN.get("earnings_proximity")):
        days = int(cfg.get("earnings_days_before", 3))
        counts["earnings_proximity"] = await _scan_earnings_proximity(
            session, user, scope, days
        )

    if cfg.get("unusual_flow") and _tier_meets_min(tier, _ALERT_TIER_MIN.get("unusual_flow")):
        counts["unusual_flow"] = await _scan_unusual_flow(session, user, scope)

    if cfg.get("news_spike") and _tier_meets_min(tier, _ALERT_TIER_MIN.get("news_spike")):
        counts["news_spike"] = await _scan_news_spike(session, user, scope)

    # insider_filing intentionally not implemented yet — needs a per-user
    # baseline of "last seen insider filing", deferred until insider data
    # ingestion is hardened.

    return counts


# ── Cron entry point ────────────────────────────────────────────────────────

async def run_alerts_scan() -> dict[str, Any]:
    started = datetime.now(tz=timezone.utc)
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "users_examined": 0,
        "alerts_fired": 0,
        "by_type": {},
        "errors": 0,
    }
    session = async_session()
    try:
        users_q = await session.execute(select(User).where(User.is_active.is_(True)))
        for user in users_q.scalars():
            summary["users_examined"] += 1
            try:
                counts = await _scan_for_user(session, user)
                for k, v in counts.items():
                    summary["alerts_fired"] += v
                    summary["by_type"][k] = summary["by_type"].get(k, 0) + v
                await session.commit()
            except Exception:
                logger.exception("alerts scan failed for user %s", user.id)
                summary["errors"] += 1
                await session.rollback()
    finally:
        await session.close()

    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    logger.info("Alerts scan done: %s", summary)
    return summary
