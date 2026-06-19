# Backend

Python 3.12, FastAPI, SQLAlchemy (async), APScheduler. Entrypoint: `main.py`. Settings/env: `config.py`.

For topic-specific deep dives:
- `services/CLAUDE.md` — pipeline phases, signal stacking, scanners
- `db/CLAUDE.md` — schema groups, Alembic workflow
- `auth/CLAUDE.md` — LEGACY_MODE, JWT, entitlements
- `api/CLAUDE.md` — endpoint reference
- `utils/CLAUDE.md` — FinBERT, data sources, yfinance gotchas

## Common Commands

```bash
# Start all services
docker compose up --build

# Start just the database
docker compose up db

# Initialize/reset database schema
docker compose exec backend python -c "from db.init_db import init_db; init_db()"

# Run backend locally (needs DATABASE_URL in env)
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Trigger pipeline manually
curl -X POST http://localhost:8000/api/refresh

# Rescore all news with FinBERT (after model changes)
docker compose exec backend python -c "
import asyncio
from sqlalchemy import select
from db.connection import async_session
from db.models import MarketNews
from utils.finbert import score_batch
from decimal import Decimal

async def rescore():
    async with async_session() as session:
        result = await session.execute(select(MarketNews))
        news = result.scalars().all()
        texts = [f'{n.headline} {n.summary or \"\"}' for n in news]
        scores = score_batch(texts)
        for n, s in zip(news, scores):
            n.sentiment_score = Decimal(str(s))
        await session.commit()
        print(f'Rescored {len(news)} articles')

asyncio.run(rescore())
"
```

## Environment Variables

```
# Data sources
FINNHUB_API_KEY=     # Finnhub (60 calls/min free) — used for news; ratings fallback only
NEWSAPI_KEY=         # NewsAPI.org (100 calls/day free) — news fallback
FMP_API_KEY=         # Financial Modeling Prep (Starter $22/mo, 300 req/min) — primary for ratings, earnings, insider trades, news
DATABASE_URL=postgresql+asyncpg://edgeflow:<password>@db:5432/edgeflow  # Docker internal

# Auth / multi-tenancy
LEGACY_MODE=true                      # default; bypasses JWT, returns legacy admin user
LEGACY_USER_EMAIL=anthonynacouzy@gmail.com  # owner of all pre-retrofit rows
JWT_JWKS_URL=                         # OIDC JWKS endpoint (Supabase / Clerk / Auth0)
JWT_HS256_SECRET=                     # HS256 shared secret (dev / simple flows)
JWT_ALGORITHMS=RS256,HS256            # comma-separated; matches the verifier's path
JWT_AUDIENCE=                         # optional aud claim check
JWT_ISSUER=                           # optional iss claim check
DEV_TOKEN_ENABLED=false               # enables POST /api/dev/token — DEV ONLY

# Pipeline tuning
NEWS_FETCH_CONCURRENCY=10             # in-flight per-ticker news fetches inside NewsScanner.scan_news
```

For local dev, `.env` at project root with `DATABASE_URL` pointing to `localhost:<port>`. If local PostgreSQL occupies port 5432, map Docker db to another port (e.g., `5433:5432` in docker-compose.yml).

## Async SQLAlchemy Gotchas

- **Never lazy-load relationships** on async sessions. Always use `selectinload()` (or `joinedload()`) in query options. Lazy access triggers `MissingGreenlet` crash.
- **Pipeline phases must commit explicitly.** Each phase in `scheduler.py` calls `await session.commit()` after success and `await session.rollback()` on error. Services that only `flush()` (analyst_tracker, earnings_calendar) rely on the phase runner to commit.
- The `.env` file is loaded by pydantic-settings relative to CWD. `config.py` searches `[".env", "../.env"]` so it works whether running from `backend/` or project root.

## Pipeline Idempotency

Pipeline re-runs on the same day are safe. `watchlist_manager.rotate_watchlist()` deletes existing snapshots for the date before inserting new ones. The `uq_snapshot_date_ticker` constraint prevents duplicates. Recommendations use `uq_rec_date_ticker` — re-runs update existing rows. Scanner runs use `uq_mbs_date_ticker` with delete-before-insert on `run_date`.

## Known Backend Limitations

- **Finnhub free tier**: The `/api/v1/stock/upgrade-downgrade` endpoint returns 403. Analyst ratings and insider trades use FMP as primary source; Finnhub serves as fallback only.
- **FMP API key**: Required (Starter plan, $22/mo). Covers analyst ratings, earnings (with EPS estimates), insider trades, and stock news. Without it, all FMP methods gracefully fall back to Finnhub/yfinance. Rate limit: 300 requests/min, 20GB bandwidth/30 days (no daily call cap).
- **Container timezone is UTC**: `date.today()` returns the UTC date, not US Eastern. Scheduler intent is US Eastern but the container has no `TZ` env set. Mismatches happen in the 00:00–04:00 UTC window (= 8 PM–midnight EDT). Fix by adding `TZ: America/New_York` to the backend service in `docker-compose.yml` if it bites.
- **FinBERT first load**: Model downloads ~500MB from HuggingFace on first use. Persisted across restarts via the `hf_cache` named volume mounted at `HF_HOME=/root/.cache/huggingface` (in `docker-compose.yml`), so it is **not** re-downloaded on every restart/deploy. A fresh volume (first deploy after adding it) still downloads once. A cold load can make the first synchronous `/api/research/{ticker}` call slow — the frontend nginx `/api` location sets `proxy_read_timeout 300s` (default is 60s) so a slow analysis returns a result instead of a 504 that surfaces as "Analysis failed".
- **yfinance weekend data**: Prices and volumes are stale on weekends/off-hours. Pipeline runs on non-trading days will produce similar recommendations.

## Maintaining this file

This file is the backend's living memory. Update it during/after any task that changes backend-level stack details, commands, env vars, async patterns, or pipeline idempotency. For topic-specific changes (services, db, auth, api, utils), edit the matching nested file instead — don't promote detail here. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
