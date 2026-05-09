"""Entitlements + credits service — the SaaS gatekeeper.

Three responsibilities:

1. **Tier registry** (`ENTITLEMENTS`) — maps tier name to feature flags +
   monthly credit quotas + numeric limits. Single source of truth for what
   each tier can do; consumed by both backend gates and the frontend UI.

2. **Subscription resolution** — `get_subscription` / `get_tier` / `ensure_subscription`.
   Every authenticated user has exactly one subscription row; absence implies
   FREE. ADMIN role bypass: legacy admin and any future ADMIN-role user gets
   the ADMIN tier regardless of subscription, so internal tooling and the
   retrofit's legacy mode both keep working.

3. **Credit consumption** — `check_and_consume(user, feature)` is the
   atomic gate: lazy-refreshes the monthly grant if a billing period has
   elapsed, decrements balance, writes a ledger row, raises 402 on
   insufficient credits. `grant_credits` is the inverse for top-ups.

Flow when a paid user calls a gated endpoint:

    consume_credit(user, "research")
      → resolve subscription → tier
      → if tier == ADMIN: no-op
      → else: get-or-create CreditBalance(user, feature)
              → if last_grant_at is None or > 30d ago: refresh balance to monthly quota
              → if balance <= 0: raise QuotaExceeded (402)
              → balance -= 1, write ledger row
              → commit

Future Stripe integration replaces only the subscription update path
(`upgrade_user` / `cancel_user`) — the consumption side is provider-agnostic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CreditBalance, CreditLedger, Subscription, User

logger = logging.getLogger(__name__)


# ── Entitlements registry ───────────────────────────────────────────────────

# Sentinel for "unlimited" — represented as a sufficiently large int so we can
# treat the same code path for finite + infinite. ADMIN tier uses this and the
# consume path short-circuits before checking balance.
UNLIMITED = 1_000_000


@dataclass(frozen=True)
class TierEntitlements:
    """Concrete per-tier feature/quota schema.

    All credit fields are MONTHLY quotas that refill on the user's billing
    cycle. Numeric limits (max_*) are static caps. Boolean flags are
    feature toggles checked at the route layer before the credit gate.
    """

    label: str
    monthly_research_credits: int
    monthly_deep_options_credits: int
    monthly_scanner_credits: int
    max_watchlist_tickers: int
    max_open_positions: int
    max_universe_slots: int
    research_retention_days: int
    alerts_enabled: bool
    custom_signal_weights: bool
    position_aware_recs: bool
    api_access: bool
    real_time_data: bool
    # Manual trigger of POST /api/refresh and /api/pipeline/run.
    # FREE/STARTER rely on the daily cron; PRO+ can request fresh data on
    # demand. The trigger itself is single-instance (concurrent clicks
    # subscribe to the existing run rather than starting a new one), so this
    # flag is purely about who can *initiate* the global refresh.
    manual_pipeline_trigger: bool
    # Phase allowlist applied when manual_pipeline_trigger=True. PRO gets
    # the cheap intraday subset (~100 paid API calls); PREMIUM and ADMIN
    # get the full pipeline (~250 calls). FREE/STARTER is intentionally
    # empty (manual_pipeline_trigger=False already gates them out).
    #
    # The expensive phases (discovery, watchlist rotation, ratings, earnings)
    # are once-a-day operations driven by cron — there's no real value in
    # PRO-tier users invoking them on-demand.
    allowed_pipeline_phases: tuple[str, ...]


_INTRADAY_PHASES = ("news", "options", "recommendations", "industries")
_FULL_PHASES = (
    "discovery", "watchlist", "ratings", "earnings",
    "news", "options", "recommendations", "industries",
)


ENTITLEMENTS: dict[str, TierEntitlements] = {
    "FREE": TierEntitlements(
        label="Free",
        monthly_research_credits=0,
        monthly_deep_options_credits=0,
        monthly_scanner_credits=0,
        max_watchlist_tickers=25,
        max_open_positions=5,
        max_universe_slots=0,
        research_retention_days=7,
        alerts_enabled=False,
        custom_signal_weights=False,
        position_aware_recs=False,
        api_access=False,
        real_time_data=False,
        manual_pipeline_trigger=False,
        allowed_pipeline_phases=(),
    ),
    "STARTER": TierEntitlements(
        label="Starter",
        monthly_research_credits=3,
        monthly_deep_options_credits=1,
        monthly_scanner_credits=0,
        max_watchlist_tickers=25,
        max_open_positions=5,
        max_universe_slots=0,
        research_retention_days=7,
        alerts_enabled=True,
        custom_signal_weights=False,
        position_aware_recs=False,
        api_access=False,
        real_time_data=False,
        manual_pipeline_trigger=False,
        allowed_pipeline_phases=(),
    ),
    "PRO": TierEntitlements(
        label="Pro",
        monthly_research_credits=40,
        monthly_deep_options_credits=15,
        monthly_scanner_credits=4,
        max_watchlist_tickers=100,
        max_open_positions=UNLIMITED,
        max_universe_slots=5,
        research_retention_days=30,
        alerts_enabled=True,
        custom_signal_weights=True,
        position_aware_recs=True,
        api_access=False,
        real_time_data=False,
        manual_pipeline_trigger=True,
        allowed_pipeline_phases=_INTRADAY_PHASES,  # 4 phases, ~100 calls
    ),
    "PREMIUM": TierEntitlements(
        label="Premium",
        monthly_research_credits=200,
        monthly_deep_options_credits=60,
        monthly_scanner_credits=20,
        max_watchlist_tickers=UNLIMITED,
        max_open_positions=UNLIMITED,
        max_universe_slots=25,
        research_retention_days=UNLIMITED,
        alerts_enabled=True,
        custom_signal_weights=True,
        position_aware_recs=True,
        api_access=True,
        real_time_data=False,
        manual_pipeline_trigger=True,
        allowed_pipeline_phases=_FULL_PHASES,      # all 8, ~250 calls
    ),
    "ADMIN": TierEntitlements(
        label="Admin",
        monthly_research_credits=UNLIMITED,
        monthly_deep_options_credits=UNLIMITED,
        monthly_scanner_credits=UNLIMITED,
        max_watchlist_tickers=UNLIMITED,
        max_open_positions=UNLIMITED,
        max_universe_slots=UNLIMITED,
        research_retention_days=UNLIMITED,
        alerts_enabled=True,
        custom_signal_weights=True,
        position_aware_recs=True,
        api_access=True,
        real_time_data=True,
        manual_pipeline_trigger=True,
        allowed_pipeline_phases=_FULL_PHASES,
    ),
}

# Map feature name → ENTITLEMENTS field for monthly quota lookup
FEATURE_TO_QUOTA_FIELD: dict[str, str] = {
    "research": "monthly_research_credits",
    "deep_options": "monthly_deep_options_credits",
    "scanner": "monthly_scanner_credits",
}


# ── Errors ──────────────────────────────────────────────────────────────────

class QuotaExceeded(HTTPException):
    """Raised when a paid user has zero credits left for a feature."""

    def __init__(self, feature: str, tier: str):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "quota_exceeded",
                "feature": feature,
                "tier": tier,
                "message": (
                    f"You've used your monthly {feature} credits on the {tier} tier. "
                    f"Upgrade or buy a credit pack to continue."
                ),
            },
        )


class FeatureLocked(HTTPException):
    """Raised when a feature isn't available on the user's tier at all."""

    def __init__(self, feature: str, tier: str):
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "feature_locked",
                "feature": feature,
                "tier": tier,
                "message": (
                    f"{feature} is not available on the {tier} tier. Upgrade to unlock."
                ),
            },
        )


# ── Subscription resolution ─────────────────────────────────────────────────

async def get_subscription(db: AsyncSession, user: User) -> Optional[Subscription]:
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    return result.scalars().first()


def get_tier_for(user: User, sub: Optional[Subscription]) -> str:
    """Resolve a user's effective tier.

    ADMIN role overrides any subscription so internal/dev users always have
    full access. Otherwise tier follows the active subscription, defaulting
    to FREE when missing or non-active.
    """
    if user.role == "ADMIN":
        return "ADMIN"
    if sub is None:
        return "FREE"
    if sub.status not in ("ACTIVE", "TRIAL"):
        return "FREE"
    return sub.tier if sub.tier in ENTITLEMENTS else "FREE"


def get_entitlements_for(tier: str) -> TierEntitlements:
    return ENTITLEMENTS.get(tier, ENTITLEMENTS["FREE"])


async def ensure_subscription(
    db: AsyncSession, user: User, default_tier: Optional[str] = None
) -> Subscription:
    """Idempotently create a subscription row for a user.

    Used by init_db to backfill the legacy admin (ADMIN tier) and by
    new-user upsert to seed FREE tier on first login. Caller commits.
    """
    sub = await get_subscription(db, user)
    if sub is not None:
        return sub
    tier = default_tier or ("ADMIN" if user.role == "ADMIN" else "FREE")
    sub = Subscription(
        user_id=user.id,
        tier=tier,
        status="ACTIVE",
        external_provider="manual",
    )
    db.add(sub)
    await db.flush()
    return sub


# ── Credit balance helpers ──────────────────────────────────────────────────

async def _get_or_create_balance(
    db: AsyncSession, user: User, feature: str
) -> CreditBalance:
    res = await db.execute(
        select(CreditBalance).where(
            CreditBalance.user_id == user.id, CreditBalance.feature == feature
        )
    )
    bal = res.scalars().first()
    if bal is None:
        bal = CreditBalance(user_id=user.id, feature=feature, balance=0)
        db.add(bal)
        await db.flush()
    return bal


def _needs_refresh(bal: CreditBalance, now: datetime) -> bool:
    """True if the monthly quota should be re-granted now.

    We use a 30-day rolling window keyed off `last_grant_at`. This is
    correct enough until Stripe webhooks drive billing-cycle-aligned
    refreshes, at which point this function is replaced by listening to
    `invoice.payment_succeeded` events.
    """
    if bal.last_grant_at is None:
        return True
    return (now - bal.last_grant_at) >= timedelta(days=30)


async def _refresh_monthly_grant(
    db: AsyncSession, user: User, feature: str, tier: str, bal: CreditBalance
) -> None:
    """Grant the user's monthly quota for `feature`. Writes a ledger row.

    Note: monthly grants RESET the balance to the quota (not additive). Any
    leftover credits from the previous period are discarded — this is how
    consumer SaaS typically works ("use it or lose it"). Purchased credit
    packs go through `grant_credits` with reason="credit_pack" and ARE
    additive — they're tracked separately to never expire.
    """
    ent = get_entitlements_for(tier)
    quota_field = FEATURE_TO_QUOTA_FIELD.get(feature)
    monthly_quota = getattr(ent, quota_field, 0) if quota_field else 0
    delta = monthly_quota - bal.balance  # may be negative if user had bought packs
    bal.balance = monthly_quota
    bal.last_grant_at = datetime.now(tz=timezone.utc)
    db.add(CreditLedger(
        user_id=user.id,
        feature=feature,
        delta=delta,
        balance_after=bal.balance,
        reason="monthly_grant",
        ref_id=None,
    ))


async def check_and_consume(
    db: AsyncSession, user: User, feature: str, ref_id: Optional[str] = None
) -> int:
    """Consume one credit of `feature` for the user. Returns new balance.

    Raises:
      - QuotaExceeded (402) if balance would go negative.
      - FeatureLocked (402) if user's tier has 0 monthly_quota AND user has
        no purchased credits to spend.

    ADMIN tier short-circuits with no DB writes — admins are unconstrained.
    """
    if user.role == "ADMIN":
        return UNLIMITED  # short-circuit; never consume for admins

    sub = await get_subscription(db, user)
    tier = get_tier_for(user, sub)
    bal = await _get_or_create_balance(db, user, feature)

    now = datetime.now(tz=timezone.utc)
    if _needs_refresh(bal, now):
        await _refresh_monthly_grant(db, user, feature, tier, bal)

    if bal.balance <= 0:
        ent = get_entitlements_for(tier)
        quota_field = FEATURE_TO_QUOTA_FIELD.get(feature)
        monthly_quota = getattr(ent, quota_field, 0) if quota_field else 0
        # If the tier has 0 quota AND no top-up packs ever granted, this is
        # a "feature locked" not "quota exceeded" — different upgrade message.
        if monthly_quota == 0:
            await db.commit()  # persist any refresh side-effect
            raise FeatureLocked(feature, tier)
        await db.commit()
        raise QuotaExceeded(feature, tier)

    bal.balance -= 1
    db.add(CreditLedger(
        user_id=user.id,
        feature=feature,
        delta=-1,
        balance_after=bal.balance,
        reason="feature_use",
        ref_id=ref_id,
    ))
    await db.commit()
    return bal.balance


async def grant_credits(
    db: AsyncSession,
    user: User,
    feature: str,
    amount: int,
    reason: str = "credit_pack",
    ref_id: Optional[str] = None,
) -> int:
    """Add `amount` credits to (user, feature). Use for purchased packs and
    manual admin top-ups. Does NOT touch last_grant_at."""
    if amount <= 0:
        raise ValueError("grant_credits amount must be positive")
    bal = await _get_or_create_balance(db, user, feature)
    bal.balance += amount
    db.add(CreditLedger(
        user_id=user.id,
        feature=feature,
        delta=amount,
        balance_after=bal.balance,
        reason=reason,
        ref_id=ref_id,
    ))
    await db.commit()
    return bal.balance


async def get_balances_summary(db: AsyncSession, user: User) -> dict[str, int]:
    """Return current balance for each known credit feature (lazy-refreshed).

    ADMIN tier returns UNLIMITED for all without touching the DB.
    """
    if user.role == "ADMIN":
        return {f: UNLIMITED for f in FEATURE_TO_QUOTA_FIELD}

    sub = await get_subscription(db, user)
    tier = get_tier_for(user, sub)
    out: dict[str, int] = {}
    now = datetime.now(tz=timezone.utc)
    for feature in FEATURE_TO_QUOTA_FIELD:
        bal = await _get_or_create_balance(db, user, feature)
        if _needs_refresh(bal, now):
            await _refresh_monthly_grant(db, user, feature, tier, bal)
        out[feature] = bal.balance
    await db.commit()
    return out
