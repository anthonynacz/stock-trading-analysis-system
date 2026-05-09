"""AM digest service.

Composes a personalized morning summary email for each user and dispatches
it via the delivery layer. Currently the delivery is a structured-log stub
because the deployment doesn't have SMTP credentials wired — the swap point
is `_dispatch_email`, which can be replaced with a Postmark / SES / SMTP
client without touching the composer.

Cron model (in services/scheduler.py): runs every 30 min between 06:00–13:00
ET on weekdays. Each tick examines `digest_config.send_time_utc` for active
users and dispatches to those whose configured send-time falls within the
past 30-minute window. This avoids per-user timer state at the cost of a
30-min delivery granularity which is fine for a morning briefing.

Per-user content (toggleable via digest_config flags):
  - include_top_picks         → top 3 weighted recs from user's perspective
  - include_position_health   → open positions + their health flags
  - include_catalysts_3d      → catalysts within 3 days for held + watchlist tickers
  - include_industry_summary  → BUY/SELL industry counts + dominant sector
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import (
    EarningsCalendar,
    IndustryRecommendation,
    Position,
    Recommendation,
    User,
    UserPreferences,
    Watchlist,
)
from services.entitlements import (
    UNLIMITED,
    get_entitlements_for,
    get_subscription,
    get_tier_for,
)
from services.preferences import (
    apply_signal_weights,
    get_signal_group_weights,
)

logger = logging.getLogger(__name__)


# ── Composer ────────────────────────────────────────────────────────────────

async def _build_top_picks(
    session: AsyncSession, user: User, weights: dict[str, float], limit: int = 3
) -> list[dict[str, Any]]:
    """Top N watchlist recommendations for today, re-weighted per the user."""
    today = date.today()
    res = await session.execute(
        select(Recommendation).where(Recommendation.recommendation_date == today)
    )
    rows = list(res.scalars())
    if not rows:
        return []
    enriched = []
    for r in rows:
        new_conv, new_action, _ = apply_signal_weights(
            float(r.conviction_score or 0), r.action, r.signals, weights
        )
        enriched.append({
            "ticker": r.ticker,
            "action": new_action,
            "conviction": round(new_conv, 1),
            "rationale": (r.rationale or "")[:200],
        })
    enriched.sort(key=lambda x: abs(x["conviction"]), reverse=True)
    return enriched[:limit]


async def _build_position_health(
    session: AsyncSession, user: User
) -> list[dict[str, Any]]:
    """Open positions with at least one health flag worth surfacing."""
    res = await session.execute(
        select(Position).where(
            Position.user_id == user.id,
            Position.status == "OPEN",
        )
    )
    out = []
    for pos in res.scalars():
        days_to_expiry = (pos.expiry - date.today()).days if pos.expiry else None
        # Lightweight inline flag computation — same predicates as routes._compute_position_health
        flags = []
        if pos.expiry and days_to_expiry is not None and 0 <= days_to_expiry <= 7:
            flags.append(f"{days_to_expiry}d to expiry")
        if pos.expiry and days_to_expiry is not None and days_to_expiry < 0:
            flags.append(f"expired {abs(days_to_expiry)}d ago")
        if pos.current_price is not None and pos.stop_loss is not None:
            if float(pos.current_price) <= float(pos.stop_loss):
                flags.append("at/below stop")
        if flags:
            out.append({
                "ticker": pos.ticker,
                "type": pos.position_type,
                "qty": pos.quantity,
                "flags": flags,
            })
    return out


async def _build_catalysts_3d(
    session: AsyncSession, user: User
) -> list[dict[str, Any]]:
    """Earnings catalysts in the next 3 days for tickers user cares about
    (open positions + active watchlist)."""
    today = date.today()
    horizon = today + timedelta(days=3)

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

    relevant = held | watched
    if not relevant:
        return []

    cat_res = await session.execute(
        select(EarningsCalendar).where(
            EarningsCalendar.ticker.in_(relevant),
            EarningsCalendar.earnings_date.between(today, horizon),
        ).order_by(EarningsCalendar.earnings_date.asc())
    )
    out = []
    for ec in cat_res.scalars():
        out.append({
            "ticker": ec.ticker,
            "date": ec.earnings_date.isoformat(),
            "days_until": (ec.earnings_date - today).days,
            "time": ec.earnings_time,
            "is_held": ec.ticker in held,
        })
    return out


async def _build_industry_summary(
    session: AsyncSession, user: User
) -> dict[str, Any] | None:
    """Latest industry counts + the most-bullish sector."""
    today = date.today()
    res = await session.execute(
        select(IndustryRecommendation).where(IndustryRecommendation.rec_date == today)
    )
    rows = list(res.scalars())
    if not rows:
        return None
    counts: dict[str, int] = {"BUY": 0, "STRONG_BUY": 0, "HOLD": 0, "SELL": 0, "STRONG_SELL": 0}
    top = max(rows, key=lambda r: float(r.conviction_score or 0))
    for r in rows:
        counts[r.action] = counts.get(r.action, 0) + 1
    return {
        "top_industry": top.industry,
        "top_industry_action": top.action,
        "top_industry_conviction": round(float(top.conviction_score), 1),
        "buy_count": counts["BUY"] + counts["STRONG_BUY"],
        "sell_count": counts["SELL"] + counts["STRONG_SELL"],
        "hold_count": counts["HOLD"],
    }


async def compose_digest(session: AsyncSession, user: User) -> dict[str, Any] | None:
    """Build the digest payload for `user`. Returns None when nothing
    meaningful to send (no recs, no positions, no catalysts) so we don't
    spam empty emails."""
    res = await session.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    )
    pref = res.scalars().first()
    if pref is None:
        return None
    cfg = pref.digest_config or {}
    if not cfg.get("enabled", True):
        return None

    weights = get_signal_group_weights(pref)
    payload: dict[str, Any] = {
        "user_id": user.id,
        "email": user.email,
        "as_of": datetime.now(tz=timezone.utc).isoformat(),
        "sections": {},
    }

    if cfg.get("include_top_picks", True):
        picks = await _build_top_picks(session, user, weights, limit=3)
        if picks:
            payload["sections"]["top_picks"] = picks

    if cfg.get("include_position_health", True):
        health = await _build_position_health(session, user)
        if health:
            payload["sections"]["position_health"] = health

    if cfg.get("include_catalysts_3d", True):
        cats = await _build_catalysts_3d(session, user)
        if cats:
            payload["sections"]["catalysts_3d"] = cats

    if cfg.get("include_industry_summary", False):
        ind = await _build_industry_summary(session, user)
        if ind:
            payload["sections"]["industry_summary"] = ind

    if not payload["sections"]:
        return None
    return payload


# ── Delivery ────────────────────────────────────────────────────────────────

def _dispatch_email(payload: dict[str, Any]) -> None:
    """Stub: log structured payload. Swap with Postmark/SES when SMTP creds
    are wired. Logging is structured (single line per delivery) so log
    aggregation can audit without parsing prose."""
    sections = payload.get("sections", {})
    summary = " | ".join(f"{k}={len(v) if isinstance(v, list) else 1}" for k, v in sections.items())
    logger.info(
        "DIGEST_EMAIL_STUB to=%s sections=[%s] payload_size=%d",
        payload.get("email"), summary, len(str(payload)),
    )


# ── Dispatcher (cron entry point) ───────────────────────────────────────────

def _send_time_in_window(send_time_utc: str | None, now_utc: datetime) -> bool:
    """True iff `send_time_utc` ("HH:MM") falls within the past 30-min slot
    relative to `now_utc`. The cron fires on the hour and half-hour, so the
    window is [now-30min, now)."""
    if not send_time_utc or len(send_time_utc) != 5 or send_time_utc[2] != ":":
        return False
    try:
        hh, mm = int(send_time_utc[:2]), int(send_time_utc[3:])
    except ValueError:
        return False
    target = now_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
    delta = (now_utc - target).total_seconds()
    return 0 <= delta < 1800


async def run_digest_dispatch() -> dict[str, Any]:
    """APScheduler entry. Iterate active users; for each user whose
    digest_config.send_time_utc falls in the past 30-min window, compose
    and dispatch."""
    started = datetime.now(tz=timezone.utc)
    summary = {
        "started_at": started.isoformat(),
        "users_examined": 0,
        "digests_sent": 0,
        "errors": 0,
    }
    session = async_session()
    try:
        users_q = await session.execute(select(User).where(User.is_active.is_(True)))
        for user in users_q.scalars():
            summary["users_examined"] += 1
            try:
                pref_res = await session.execute(
                    select(UserPreferences).where(UserPreferences.user_id == user.id)
                )
                pref = pref_res.scalars().first()
                if pref is None:
                    continue
                cfg = pref.digest_config or {}
                if not cfg.get("enabled", True):
                    continue
                if not _send_time_in_window(cfg.get("send_time_utc"), started):
                    continue
                payload = await compose_digest(session, user)
                if payload is None:
                    continue
                _dispatch_email(payload)
                summary["digests_sent"] += 1
            except Exception:
                logger.exception("digest dispatch failed for user %s", user.id)
                summary["errors"] += 1
                await session.rollback()
        await session.commit()
    finally:
        await session.close()

    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    logger.info("Digest dispatch done: %s", summary)
    return summary
