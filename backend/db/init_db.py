import asyncio
import logging

from sqlalchemy import select, text

from config import SECTORS, settings
from db.connection import async_session
from db.models import (  # noqa: F401 — imported for metadata registration
    AlertLog,
    Base,
    ChartConfig,
    CreditBalance,
    CreditLedger,
    IndustryRecommendation,
    MultibaggerSnapshot,
    MultibaggerUniverse,
    Position,
    Sector,
    Subscription,
    UniverseStock,
    User,
    UserPreferences,
)
from services.multibagger_seed import SCANNER_SEED

logger = logging.getLogger(__name__)

# Tables with a user_id FK. Backfilled to the legacy admin on first run after
# the SaaS retrofit. The schema (column definition + index) is now created
# by Alembic; init_db only handles the *data* backfill.
USER_OWNED_TABLES = (
    "watchlist",
    "positions",
    "strike_snapshots",
    "research_results",
    "deep_options_analyses",
    "multibagger_universe",
)


async def _ensure_legacy_user(session) -> User:
    """Ensure the legacy admin user exists; return it.

    All pre-retrofit rows are owned by this user. When real users sign up
    later, their JWT 'sub' upserts a fresh User row separately.
    """
    result = await session.execute(
        select(User).where(User.email == settings.LEGACY_USER_EMAIL)
    )
    user = result.scalars().first()
    if user is None:
        user = User(
            email=settings.LEGACY_USER_EMAIL,
            provider="legacy",
            provider_user_id=None,
            role="ADMIN",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("init_db: created legacy admin user %s (id=%s)", user.email, user.id)
    return user


DEV_TEST_EMAIL = "anthony@vela.io"


async def _ensure_dev_test_user(session) -> None:
    """Create a Pro-tier test user (idempotent) for non-legacy login.

    Email: anthony@vela.io  ·  Tier: PRO  ·  Credits: pre-granted to mirror
    the monthly Pro quota so the user can immediately exercise gated
    features (Research, Deep Options, Scanner) without waiting for the
    first lazy grant.
    """
    from services.entitlements import (
        ENTITLEMENTS,
        ensure_subscription,
        grant_credits,
    )
    from services.preferences import ensure_preferences

    res = await session.execute(select(User).where(User.email == DEV_TEST_EMAIL))
    user = res.scalars().first()
    if user is None:
        user = User(
            email=DEV_TEST_EMAIL,
            provider="vela-dev",
            provider_user_id=f"dev:{DEV_TEST_EMAIL}",
            role="USER",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info("init_db: created dev test user %s (id=%s)", user.email, user.id)

    # Ensure Pro subscription
    sub = await ensure_subscription(session, user, default_tier="PRO")
    if sub.tier != "PRO":
        sub.tier = "PRO"
        sub.status = "ACTIVE"
    await session.commit()

    # Pre-create preferences so settings UI loads instantly
    await ensure_preferences(session, user)
    await session.commit()

    # Top up credits to one full Pro period if balance is zero. Idempotent —
    # repeat startups won't pile up credits because grant_credits won't
    # auto-trigger here once a balance exists.
    pro_ent = ENTITLEMENTS["PRO"]
    from services.entitlements import _get_or_create_balance
    for feature, monthly in [
        ("research", pro_ent.monthly_research_credits),
        ("deep_options", pro_ent.monthly_deep_options_credits),
        ("scanner", pro_ent.monthly_scanner_credits),
    ]:
        bal = await _get_or_create_balance(session, user, feature)
        if bal.balance == 0:
            await grant_credits(session, user, feature, monthly, reason="dev_seed")


async def _backfill_user_ids(session, legacy_user_id: int) -> None:
    """Set user_id = legacy_user_id on any pre-existing rows that lack it."""
    for tbl in USER_OWNED_TABLES:
        res = await session.execute(text(
            f"UPDATE {tbl} SET user_id = :uid WHERE user_id IS NULL"
        ), {"uid": legacy_user_id})
        if res.rowcount:
            logger.info("init_db: backfilled %d %s rows to legacy user", res.rowcount, tbl)
    await session.commit()

SEED_SECTORS = [
    "AI/Semiconductors",
    "Fintech/Payments",
    "Energy/Commodities",
    "Healthcare/Biotech",
    "Consumer/Cloud/Enterprise",
    "Industrials/Defense",
    "Power/Utilities/Nuclear",
    "Communications/Media",
]


async def init_db():
    """Seed-only init. Schema migrations are owned by Alembic and run before
    this function is called (see backend/entrypoint.sh). This function is
    idempotent and safe to run on every startup — it only inserts data when
    rows are missing, never alters tables.
    """
    async with async_session() as session:
        # Seed the legacy admin user, then backfill NULL user_id rows on
        # owned tables to point at it. Schema for these columns is created
        # by Alembic's baseline migration; this is purely a data-fill step
        # that's only meaningful on the first init after the SaaS retrofit.
        legacy_user = await _ensure_legacy_user(session)
        await _backfill_user_ids(session, legacy_user.id)

        # Seed an ADMIN-tier subscription for the legacy user so the
        # entitlements service treats it as unconstrained. Future signed-up
        # users get FREE tier on first JWT validation (handled by
        # ensure_subscription called from the auth middleware path).
        from services.entitlements import ensure_subscription
        await ensure_subscription(session, legacy_user, default_tier="ADMIN")
        await session.commit()

        # Dev-only: pre-create a Pro-tier test user so the operator can
        # log in via /login and explore the non-legacy flow without going
        # through a real auth provider. Idempotent — only inserts when
        # missing, and only when DEV_TOKEN_ENABLED is on (the same flag
        # that enables /api/dev/token, so they're consistent).
        if settings.DEV_TOKEN_ENABLED:
            await _ensure_dev_test_user(session)

        # Seed sectors if empty
        result = await session.execute(select(Sector))
        if not result.scalars().first():
            for name in SEED_SECTORS:
                session.add(Sector(name=name, max_stocks=6))
            await session.commit()
            print(f"Seeded {len(SEED_SECTORS)} sectors")

        # Seed universe stocks from config if empty
        us_result = await session.execute(select(UniverseStock).limit(1))
        if not us_result.scalars().first():
            sector_result = await session.execute(select(Sector))
            sector_map = {s.name: s.id for s in sector_result.scalars().all()}
            count = 0
            for sector_name, cfg in SECTORS.items():
                sector_id = sector_map.get(sector_name)
                if not sector_id:
                    continue
                for ticker in cfg["universe"]:
                    session.add(UniverseStock(
                        ticker=ticker,
                        sector_id=sector_id,
                        source="SEED",
                        is_active=True,
                    ))
                    count += 1
            await session.commit()
            print(f"Seeded {count} universe stocks from config")

        # Seed multi-bagger scanner universe if empty
        mb_result = await session.execute(select(MultibaggerUniverse).limit(1))
        if not mb_result.scalars().first():
            for ticker, theme in SCANNER_SEED:
                session.add(
                    MultibaggerUniverse(
                        ticker=ticker,
                        theme=theme,
                        source="SEED",
                        is_active=True,
                    )
                )
            await session.commit()
            print(f"Seeded {len(SCANNER_SEED)} scanner universe stocks")

    print("Database initialized successfully")


if __name__ == "__main__":
    asyncio.run(init_db())
