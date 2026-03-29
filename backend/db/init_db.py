import asyncio

from sqlalchemy import select

from db.connection import engine, async_session
from db.models import Base, Sector

SEED_SECTORS = [
    "AI/Semiconductors",
    "Fintech/Payments",
    "Energy/Commodities",
    "Healthcare/Biotech",
    "Consumer/Cloud/Enterprise",
]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        result = await session.execute(select(Sector))
        if not result.scalars().first():
            for name in SEED_SECTORS:
                session.add(Sector(name=name, max_stocks=6))
            await session.commit()
            print(f"Seeded {len(SEED_SECTORS)} sectors")

    print("Database initialized successfully")


if __name__ == "__main__":
    asyncio.run(init_db())
