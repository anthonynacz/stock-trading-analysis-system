# CLAUDE.md

Top-level guidance for this repo. Topic-specific detail lives in nested `CLAUDE.md` files (auto-loaded when working in those subdirectories).

## Project Overview

EdgeFlow is an automated daily analyst-rating-to-options-trade recommendation engine. It monitors analyst upgrades/downgrades, earnings catalysts, market news, and options flow data to generate actionable trade recommendations with strike-level options suggestions. Full-stack app: FastAPI backend, React/TypeScript frontend, PostgreSQL database, all containerized with Docker Compose.

**Branding:** the codebase, container names, DB user, and FastAPI title are all "EdgeFlow" internally. The hosted SaaS product brand is **Vela** (see `vela-strategy.html` at the project root). User-facing UI strings (AppNav title) say "Vela"; everything else stays EdgeFlow until a full rebrand pass is justified.

## Repo Layout

```
agenticanalysis/
├── backend/         # Python 3.12, FastAPI, SQLAlchemy, APScheduler — see backend/CLAUDE.md
│   ├── services/    # Pipeline phases, signal stacking, scanners — see backend/services/CLAUDE.md
│   ├── db/          # PostgreSQL schema + Alembic — see backend/db/CLAUDE.md
│   ├── auth/        # JWT, LEGACY_MODE, entitlements — see backend/auth/CLAUDE.md
│   ├── api/         # REST routes — see backend/api/CLAUDE.md
│   └── utils/       # FinBERT, data sources, yfinance — see backend/utils/CLAUDE.md
├── frontend/        # React 18 + TypeScript, Vite, Tailwind — see frontend/CLAUDE.md
├── docker-compose.yml             # postgres:16 + backend + frontend
├── docker-compose.auth-test.yml   # Override layering JWT mode env vars for smoke tests
└── .env                           # API keys (FMP, Finnhub, NewsAPI, DATABASE_URL, JWT_*, LEGACY_*)
```

## Nested CLAUDE.md index

- **`backend/CLAUDE.md`** — stack, common commands, env vars, async SQLAlchemy gotchas, pipeline idempotency
- **`backend/services/CLAUDE.md`** — pipeline phases, signal stacking, entry strategy, strike recommender, industries, charts, multibagger scanner
- **`backend/db/CLAUDE.md`** — table groups, Alembic workflow, key columns, schema caveats
- **`backend/auth/CLAUDE.md`** — LEGACY_MODE, JWT verifier, dev tokens, entitlements & credits
- **`backend/api/CLAUDE.md`** — full endpoint reference
- **`backend/utils/CLAUDE.md`** — FinBERT, data source fallback chains, yfinance sanitization, geopolitical, greeks, IV history
- **`frontend/CLAUDE.md`** — auth/provider hierarchy, dashboard layout, pages, design system

## Deployment

Production is deployed on a Hetzner VPS at `204.168.198.65`. Services are exposed as:
- **Frontend**: http://204.168.198.65:3000
- **Backend API**: http://204.168.198.65:8000

Project lives at `/root/edgeflow` on the server. Git remote is `origin = https://github.com/anthonynacz/stock-trading-analysis-system.git`. The server's local branch is `master` but tracks `origin/main` (local dev pushes `master:main`, so prod pulls `main`).

### Deploy workflow

After `git push` from local lands on `origin/main`:
```bash
ssh root@204.168.198.65
cd /root/edgeflow
git pull                                # fast-forwards master from origin/main
docker compose up -d --build            # rebuilds backend + frontend, restarts
```
The backend's `entrypoint.sh` runs `alembic upgrade head` before uvicorn starts, so DB migrations apply automatically on container restart — no manual step needed. The frontend Dockerfile is multi-stage and bakes the compiled bundle in at build time, so a rebuild (not just a restart) is required for any frontend change to be visible.

### Useful server-side commands
```bash
docker compose ps                       # Check container status
docker compose logs --tail=200 -f       # All logs, follow
docker compose logs --tail=200 -f backend
docker compose up -d --build frontend   # Frontend-only rebuild
```

### Single-replica constraint
APScheduler runs in-process inside the backend container. Multiple backend replicas would cause duplicate pipeline runs and upsert races on shared tables (`recommendations`, `watchlist_daily_snapshot`, etc.). Keep `docker-compose.yml` at one backend instance until the scheduler moves to a dedicated worker (or an external lock, e.g. a Postgres advisory lock keyed on phase, is introduced).

## Maintaining these files

Treat the CLAUDE.md files as the project's living memory. Keep them accurate and lean.

**At the start of every session**, read the root file fully. Nested files auto-load when you touch their subdirectory — don't preemptively read them all.

**During and at the end of every task**, update the relevant file when:
- A new architectural decision, convention, or constraint is established
- A non-obvious gotcha, workaround, or "here be dragons" area is discovered
- A command, script, file path, dependency, or environment requirement changes
- A pattern in a file turns out to be wrong, outdated, or superseded
- The user corrects you on something you'll likely get wrong again

**When updating, follow these rules:**
1. **Edit the most-local file that covers the topic.** Don't promote subdirectory detail to the root — root stays thin.
2. **Prune aggressively.** If information is no longer true, delete it — do not leave stale notes with "(deprecated)" tags unless the deprecation itself is load-bearing context.
3. **Prefer editing over appending.** If a section already covers the topic, revise it in place rather than adding a new one.
4. **Record decisions, not narration.** "We use Zod for runtime validation at API boundaries" — yes. "We discussed validation options today" — no.
5. **Keep it skimmable.** Short sections, concrete examples, no filler. If a section exceeds ~15 lines, consider whether half of it is still essential.
6. **Do not record speculation.** Only commit patterns that are actually in use or firmly decided. Ideas and open questions belong in issues/TODOs, not here.
7. **Cross-link, don't duplicate.** Reference other files by path (e.g., `see backend/services/CLAUDE.md § Signal Stacking`) rather than copying content.
8. **Surface the change.** When you update one of these files, briefly mention what you changed and why in your reply, so I can sanity-check it.

If you're unsure whether something belongs in any of these files, ask before writing it.
