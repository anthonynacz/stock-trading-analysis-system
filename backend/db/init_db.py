import asyncio

from sqlalchemy import select

from config import SECTORS
from db.connection import engine, async_session
from db.models import (  # noqa: F401 — imported for metadata registration
    Base,
    IndustryRecommendation,
    MultibaggerSnapshot,
    MultibaggerUniverse,
    Position,
    Sector,
    UniverseStock,
)
from services.multibagger_seed import SCANNER_SEED

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
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
