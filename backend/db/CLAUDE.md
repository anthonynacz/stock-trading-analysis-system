# Database

PostgreSQL 16. `db/models.py` is the source of truth; Alembic owns schema migrations.

## Table Groups

**Shared / pipeline-owned** (no `user_id`): `sectors`, `universe_stocks`, `discovery_candidates`, `watchlist_daily_snapshot`, `analyst_ratings`, `earnings_calendar`, `market_news`, `news_ticker_relevance`, `options_snapshots`, `suggested_options`, `recommendations`, `multibagger_snapshot`, `industry_recommendations`. Written by the daily APScheduler pipeline; read by every user.

**User-owned** (have `user_id` FK to `users`): `watchlist`, `positions`, `strike_snapshots`, `research_results`, `deep_options_analyses`, `multibagger_universe`, `chart_configs`. Routes scope reads/writes by `current_user.id`. **Note:** `watchlist` and `multibagger_universe` carry the column but per-user route scoping is deferred to the Custom Universe Slots feature — the daily pipeline still writes them globally.

**SaaS / auth** (multi-tenancy): `users`, `subscriptions`, `credit_balances`, `credit_ledger`, `chart_configs`. See `backend/auth/CLAUDE.md`.

## Alembic Workflow

Container's `entrypoint.sh` runs `alembic upgrade head` before uvicorn starts, so the DB is always at the latest revision when the app boots. Migrations live in `backend/alembic/versions/`. The baseline (`5de04647a468_baseline_schema.py`) delegates to `Base.metadata.create_all` — subsequent migrations are autogen diffs against `db/models.py`.

`db/init_db.py` is **seed-only** post-Alembic adoption — it inserts data (legacy admin user, sectors, multibagger universe, dev test user) but never alters tables. Idempotent and safe to run on every startup.

Workflow for a schema change:
```
# 1. Edit db/models.py
# 2. Generate migration
docker compose exec backend alembic revision --autogenerate -m "your_change"
# 3. Review the generated file in backend/alembic/versions/
# 4. Apply locally
docker compose exec backend alembic upgrade head
# 5. Commit + deploy — entrypoint.sh runs `alembic upgrade head` on prod startup
```

All `DateTime` columns use `DateTime(timezone=True)` (maps to PostgreSQL `TIMESTAMPTZ`). This is required because asyncpg strictly rejects tz-aware Python datetimes for `TIMESTAMP WITHOUT TIME ZONE` columns.

## Key Columns Added After Initial Schema

These columns were added via `ALTER TABLE` and may need to be re-added if the database is recreated from models alone:
- `watchlist.is_manual`, `watchlist.is_locked` — `BOOLEAN NOT NULL DEFAULT FALSE`
- `watchlist_daily_snapshot` — `UNIQUE(snapshot_date, ticker)` (`uq_snapshot_date_ticker`)
- `options_snapshots.atm_iv` — `NUMERIC(6,4)` — persisted near-ATM IV for historical IV rank (`utils/iv_history.py`)
- `recommendations.prior_action / prior_conviction_score / revision_number / revised_at / revision_reason` — same-day revision tracking. `revision_reason` is populated by the intraday news rescore path with the headline that triggered the revision; daily pipeline revisions leave it NULL.
- **All user-owned tables** — `user_id INTEGER REFERENCES users(id)` added by `_ensure_user_id_columns()` in `init_db.py` on first startup after the retrofit. Idempotent.

## Known Schema Caveats

- **`strike_snapshots` unique constraint**: Currently `UNIQUE(snapshot_date, ticker)` — global, not per-user. In LEGACY_MODE this is fine (one user); in JWT mode two different users saving the same `(date, ticker)` will collide. Fix: change to `UNIQUE(snapshot_date, ticker, user_id)`.
- **Watchlist + multibagger universe per-user scoping**: Tables have `user_id` columns (backfilled to legacy admin), but routes don't filter by user yet — the daily pipeline writes them globally. Per-user scoping deferred to the Custom Universe Slots feature (see `vela-strategy.html` §2).

## Maintaining this file

Update this file whenever you change table-group membership, add a new schema caveat, alter the Alembic workflow, or add an `ALTER TABLE` that lives outside an Alembic migration. Don't list every column — `db/models.py` is authoritative for that. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
