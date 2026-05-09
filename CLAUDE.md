# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EdgeFlow is an automated daily analyst-rating-to-options-trade recommendation engine. It monitors analyst upgrades/downgrades, earnings catalysts, market news, and options flow data to generate actionable trade recommendations with strike-level options suggestions. Full-stack app: FastAPI backend, React/TypeScript frontend, PostgreSQL database, all containerized with Docker Compose.

**Branding:** the codebase, container names, DB user, and FastAPI title are all "EdgeFlow" internally. The hosted SaaS product brand is **Vela** (see `vela-strategy.html` at the project root). User-facing UI strings (AppNav title) say "Vela"; everything else stays EdgeFlow until a full rebrand pass is justified.

## Architecture

```
edgeflow/
├── backend/         # Python 3.12, FastAPI, SQLAlchemy, APScheduler
│   ├── main.py      # FastAPI entrypoint
│   ├── config.py    # Env vars, API keys, sector universe, constants, auth/JWT settings
│   ├── auth/        # JWT verifier, get_current_user dependency, dev-token mint
│   ├── db/          # PostgreSQL schema (init_db.py), SQLAlchemy models, connection pool
│   ├── services/    # Core business logic — pipeline phases + entitlements.py (tier+credits)
│   ├── api/         # REST routes (routes.py)
│   └── utils/
│       ├── data_sources.py   # API clients (FMP, yfinance, Finnhub, NewsAPI)
│       ├── scoring.py        # Keyword-based scoring, impact levels, composite scores
│       └── finbert.py        # FinBERT sentiment model (ProsusAI/finbert)
├── frontend/        # React 18 + TypeScript, Vite, Tailwind CSS
│   └── src/
│       ├── App.tsx                     # BrowserRouter; AuthProvider + EntitlementsProvider; RequireAuth on every protected route
│       ├── contexts/
│       │   ├── AuthContext.tsx         # Fetches /api/me, holds token in localStorage, axios interceptor
│       │   ├── EntitlementsContext.tsx # Fetches /api/me/entitlements, exposes tier/credits/isAdmin
│       │   └── ResearchContext.tsx, OptionsLabContext.tsx
│       ├── pages/
│       │   ├── LoginPage.tsx           # Dev-token mint fallback (real auth provider TBD)
│       │   ├── Dashboard.tsx, ResearchPage.tsx, PositionsPage.tsx, KnowledgePage.tsx, ...
│       ├── components/
│       │   ├── AppNav.tsx              # Top nav: tier badge, credit widget, LEGACY chip, sign-out
│       │   ├── RequireAuth.tsx         # Redirects to /login when user is null
│       │   └── ... (TickerDetail, RecommendationCard, StrikeRecommender, etc.)
│       ├── hooks/useEdgeFlow.ts        # Data fetching hooks with polling
│       ├── types/index.ts              # TypeScript interfaces
│       └── utils/api.ts                # Axios client; interceptor attaches Bearer <token>
├── docker-compose.yml             # postgres:16 + backend + frontend
├── docker-compose.auth-test.yml   # Override layering JWT mode env vars for smoke tests
└── .env                           # API keys (FMP, Finnhub, NewsAPI, DATABASE_URL, JWT_*, LEGACY_*)
```

### Service Layer (backend/services/)

The daily workflow runs as a phased pipeline orchestrated by APScheduler in `scheduler.py`. All times US Eastern:

0. **universe_discoverer.py** — Scans FMP market lists (most active, gainers, losers) and news-trending tickers to surface candidates for user approval into the universe. Runs at 05:00 EST before watchlist rotation. Candidates go to `discovery_candidates` table with `PENDING` status; user approves/dismisses via `/api/universe/candidates/{id}/approve|dismiss`.
1. **watchlist_manager.py** — Scores stocks from `universe_stocks` DB table (not hardcoded config) across 5 sectors, applies liquidity filters (≥$5B cap, ≥2M vol, ≥1K options vol, ≥5 analysts), rotates watchlist (max 30 active, max 5 changes/day). Manual (`is_manual`), locked (`is_locked`), and **unusual options activity** entries are protected from rotation. Unusual options = today's call volume ≥3x the 20-day historical average (queried from `options_snapshots`); toxic tickers override this protection. Uses delete-before-insert for idempotent daily snapshots. Composite score includes `recommendation_conviction` (15% weight) — feeds the recommendation engine's output back into watchlist ranking so SELL/STRONG_SELL stocks are deprioritized. Toxic removal: tickers with 3+ consecutive SELL/STRONG_SELL recommendations are flagged for removal regardless of composite score (`TOXIC_CONVICTION_DAYS` in config).
2. **analyst_tracker.py** — Detects rating changes since prior close, classifies as TIER_CHANGE/PT_CHANGE/INITIATION/REITERATION, scores by firm tier (T1=bulge bracket, T2=mid-tier, T3=boutique)
3. **earnings_calendar.py** — Tracks earnings dates, manages catalyst windows (T-7 to T+10). Sanitizes yfinance garbage in `earnings_time` (float values → null) and `fiscal_quarter` (revenue estimates → null).
4. **news_scanner.py** — Fetches/categorizes news into 7 categories (EARNINGS, ANALYST, MACRO, SECTOR, INSIDER, PRODUCT, GEOPOLITICAL), scores sentiment via **FinBERT** (ProsusAI/finbert) using batch inference. FinBERT returns continuous scores in [-1.0, 1.0] with financial domain understanding. Model loads once (singleton in `utils/finbert.py`) and persists in memory. Tracks per-ticker relevance via `news_ticker_relevance` junction table (many-to-many). Relevance scored by text matching: HEADLINE=1.0, SUMMARY=0.7, API_RELATED=0.5, SEARCHED=0.3. Duplicate articles (same URL) skip MarketNews creation but backfill relevance rows for newly-relevant tickers.
5. **options_analyzer.py** — Pulls chains via yfinance, persists `atm_iv` and computes IV rank via `utils/iv_history.compute_iv_rank_and_percentile` (historical rank over last ~year of snapshots when ≥20 data points, stabilized cross-sectional fallback otherwise — never raw chain min/max, which is unstable). Detects unusual activity (>1.5x avg volume). Includes **strike recommender** (`recommend_strikes` / `recommend_strikes_all`) with three risk profiles (conservative/moderate/aggressive) and budget-filtered contract selection.
5a. **deep_options_analyzer.py** — Expert-level single-ticker options report. Consumes a DTE-spread chain (`DataSourceClient.get_options_chain(ticker, mode="spread")` returns up to 8 expirations bucketed 0-7/7-21/21-45/45-90/90-180/180+d). Computes: ATM greeks table with Rho/Vanna/Charm/Vomma, IV rank + percentile, vol term structure (contango/backwardation), 25-delta skew, expected moves, max pain per expiry, signed gamma exposure (GEX), pin magnets, P/C OI by expiry, liquidity rating. Outputs a strategy verdict (LONG_CALL / LONG_PUT / BULL_CALL_SPREAD / BEAR_PUT_SPREAD / BULL_PUT_SPREAD / BEAR_CALL_SPREAD / IRON_CONDOR / LONG_STRADDLE / NO_TRADE) selected by bias × IV bucket × earnings proximity, plus a `hidden_risks` list (IV_CRUSH, THETA_BURN, VEGA_EXPOSURE, PIN_RISK, LIQUIDITY, NEGATIVE_GEX, BACKWARDATION, PUT_SKEW, ASSIGNMENT_RISK). ATM IV falls back to median of valid near-ATM strikes when direct-ATM contract has stale weekend quotes.
6. **recommendation_engine.py** — Stacks signals into conviction scores (-100 to +100), generates rationale text, selects optimal options contracts, classifies STRONG_BUY through STRONG_SELL. Each day's recommendation is stored independently (`uq_rec_date_ticker` constraint). `analyze_single()` runs the same pipeline but returns a data dict without persisting to `recommendations` — used by the Research feature.

### Signal Stacking (conviction scoring)

Signals are stacked across eight categories. Classification thresholds: ≥60 STRONG_BUY, 30-59 BUY, -15 to 29 HOLD, -30 to -16 SELL, ≤-31 STRONG_SELL.

**Analyst signals**: T1/T2/T3 upgrades (+15 to +40) / downgrades (-15 to -40), PT raises (+10/+15) / cuts (-10/-15). **Analyst Revision Cluster** (+10/+15 or -10/-15): ≥2 distinct T1/T2 firms acting bullish (or bearish) within the last 14 days — captures sustained positioning shifts rather than single-firm reactions.
**Earnings signals**: Beat (+20), Beat+Raised (+30), Miss (-20), Miss+Lowered (-30), Active Catalyst Window (+5). Window spans **T-14 to T+10** (ACTIVE) — extended from T-7 to get ahead of IV expansion / institutional positioning typically seen 10-14 days pre-earnings.
**Technical signals**: RSI oversold/overbought (±10/±15), price vs 50d SMA (±10), price vs 200d SMA (±5), 5d/20d momentum (±10), volume-confirmed moves (±10), short squeeze setup (+15), high short interest (-5).
**Drawdown signals**: Sharp 1d drop >5% (-15), rapid 2d drawdown >7% (-20), rapid 3d drawdown >10% (-15), distribution/heavy selling volume (-10). These fire independently and stack.
**Reversal signals**: Oversold Bounce Setup (+15/+20) and Strong Reversal Setup (+20/+25) fire only when drawdown is active AND RSI <30 AND price near support (200d SMA or 52w low). Selling exhaustion (low down-day volume) adds confidence.
**52-week & value signals (context-gated)**: Near 52w high (+5), deep pullback (+10 normally, +5 if actively declining). Below consensus PT (+10 normally, +5 if actively declining). Gating prevents value traps during falling-knife scenarios.
**Relative strength**: Stock vs SPY 5d momentum comparison. Outperforming (+10), underperforming (-10). SPY data fetched once per pipeline via `get_market_benchmark()`.
**OHLC candlestick signals**: Gap-Down After Up Day (-12), Upper Wick Rejection (-8), Close Near Low (-5), Close Near High (+5). Derived from Open/High/Low in yfinance 2-month history. Silently skip when OHLC unavailable — all close-only signals unaffected.
**Options signals**: Unusual call volume (+15) / put volume (-15) vs 20-day historical avg, put/call OI skew (±10), high IV rank (-10). **Smart Money Positioning** (+12): fires when 3-day trailing avg call volume ≥2x 20d baseline AND IV rank has risen >10pp over last ~5 days — institutional accumulation ahead of a catalyst.
**Greek signals**: Computed from Black-Scholes (`utils/greeks.py`) using yfinance IV per contract. Heavy Theta Decay (-8 if ATM theta >3% premium/day, -5 if >2%). IV Crush Risk (-12 if earnings within 7 days AND vega >8% premium per 1% IV). Favorable Theta (+5 if theta <1%, near-term DTE ≤14, and score >30). Greeks replace the crude moneyness-based delta estimate with proper BS delta. Gamma, theta, vega are computed on-the-fly (not persisted — too ephemeral).
**News signals**: FinBERT sentiment scaled by magnitude and article count (±5 to ±15), sector tailwind/headwind (±10). **When geopolitical signals fire, news sentiment points are halved** to reduce double-counting (same articles drive both signals).
**Geopolitical signals**: Keyword-detected events (MILITARY_CONFLICT, TRADE_WAR, SANCTIONS, DIPLOMATIC_BREAKTHROUGH, OIL_DISRUPTION, REGULATION) from MACRO/SECTOR/GEOPOLITICAL news. Sector-specific impact mapping — same event has different points per EdgeFlow sector (e.g., military conflict: Energy +18, Semis -12). Points scaled by FinBERT sentiment magnitude (0.6x–1.0x of base), clamped ±20 per signal. One signal per event type (deduplicated). **Total geopolitical contribution capped at ±20** to prevent correlated events (e.g., MILITARY_CONFLICT + OIL_DISRUPTION) from stacking beyond a single event's impact. Detection in `utils/geopolitical.py`.
**Other**: Insider buying (+5) / selling (-5), stock already moved (-5). **Insider Buy Cluster** (+8/+12) supersedes the generic "Insider Buying" signal when ≥2 distinct insiders bought in the last 30 days AND buy count > sell count — convergent accumulation is a stronger pre-position trigger than raw dollar value.

### Entry strategy (PRE_POSITION) triggers

`_determine_entry_strategy()` marks a rec as `PRE_POSITION` when EITHER:
- Catalyst window is ACTIVE (T-14 to T-0) AND conviction ≥ 30, OR
- A leading pre-position signal is present AND conviction ≥ 25. Leading signals: **Analyst Revision Cluster**, **Smart Money Positioning**, **Insider Buy Cluster**. This lets PRE_POSITION fire weeks before an earnings catalyst when institutional positioning clusters. Rationale text names the leading signal instead of "catalyst window active" when it triggers this branch.

Technical indicators (RSI-14, 5d/20d momentum, volume ratio, drawdown metrics, consecutive down days, down-day volume ratio, OHLC candlestick metrics) are computed from yfinance 2-month price history via `DataSourceClient.get_technical_indicators()`. Moving averages (50d, 200d), short interest, and 52-week range come from yfinance `.info` via `get_stock_data()`. SPY benchmark comes from `get_market_benchmark()`.

### Strike Recommender

Three risk profiles control delta ranges, DTE windows, and contract filtering:
- **Conservative**: delta 0.50-0.80, DTE 7-30 — ITM/ATM, high probability
- **Moderate**: delta 0.30-0.55, DTE 14-50 — ATM to slightly OTM, balanced
- **Aggressive**: delta 0.10-0.35, DTE 21-75 — OTM, maximum leverage

Filters: OI ≥ 10, bid-ask spread ≤ 1.50, premium * 100 ≤ budget. Delta computed via Black-Scholes using yfinance IV per contract (`utils/greeks.py`). Gamma, theta, vega also returned per contract. Contracts with heavy theta (>3% premium/day) are scored lower. Explanation text includes greek warnings (HIGH THETA, HIGH VEGA). The `/strikes/all` endpoint fetches the options chain once and returns all three profiles in a single response.

### Sentiment Analysis (FinBERT)

News sentiment uses `ProsusAI/finbert`, a BERT model fine-tuned on financial text. The wrapper is in `utils/finbert.py`:
- `score_sentiment(text)` — single text, returns float in [-1.0, 1.0]
- `score_batch(texts)` — batch scoring, used by news_scanner for efficiency
- Score = P(positive) - P(negative) from softmax output
- Model lazy-loads on first call (~500MB, cached by HuggingFace)
- Runs on CPU (torch CPU-only installed via `--index-url https://download.pytorch.org/whl/cpu`)

### Data Sources

Unified in `utils/data_sources.py` with rate limiting and fallback chains:
- **Analyst ratings**: FMP (primary) → Finnhub → yfinance
- **Earnings calendar**: FMP (primary, provides EPS estimates) → yfinance
- **Insider trades**: FMP (primary) → Finnhub
- **Prices/fundamentals/options**: yfinance (primary, no key needed)
- **News**: Finnhub → FMP → NewsAPI

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

# Run frontend locally
cd frontend && npm install && npm run dev

# Trigger pipeline manually
curl -X POST http://localhost:8000/api/refresh

# Type check frontend
cd frontend && npx tsc --noEmit

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

## Database

PostgreSQL 16. Tables fall into three groups:

**Shared / pipeline-owned** (no `user_id`): `sectors`, `universe_stocks`, `discovery_candidates`, `watchlist_daily_snapshot`, `analyst_ratings`, `earnings_calendar`, `market_news`, `news_ticker_relevance`, `options_snapshots`, `suggested_options`, `recommendations`, `multibagger_snapshot`, `industry_recommendations`. Written by the daily APScheduler pipeline; read by every user.

**User-owned** (have `user_id` FK to `users`): `watchlist`, `positions`, `strike_snapshots`, `research_results`, `deep_options_analyses`, `multibagger_universe`, `chart_configs`. Routes scope reads/writes by `current_user.id`. **Note:** `watchlist` and `multibagger_universe` carry the column but per-user route scoping is deferred to the Custom Universe Slots feature — the daily pipeline still writes them globally.

**SaaS / auth** (multi-tenancy): `users`, `subscriptions`, `credit_balances`, `credit_ledger`, `chart_configs`. See "Multi-tenancy & Auth" and "Entitlements & Credits" below.

**Schema is owned by Alembic** (`backend/alembic/`). The container's `entrypoint.sh` runs `alembic upgrade head` before uvicorn starts, so the DB is always at the latest revision when the app boots. Migrations live in `backend/alembic/versions/`. The baseline (`5de04647a468_baseline_schema.py`) delegates to `Base.metadata.create_all` — `db/models.py` is the source of truth for schema; subsequent migrations are autogen diffs against that.

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

### Key Columns Added After Initial Schema

These columns were added via `ALTER TABLE` and may need to be re-added if the database is recreated from models alone:
- `watchlist.is_manual`, `watchlist.is_locked` — `BOOLEAN NOT NULL DEFAULT FALSE`
- `watchlist_daily_snapshot` — `UNIQUE(snapshot_date, ticker)` (`uq_snapshot_date_ticker`)
- `options_snapshots.atm_iv` — `NUMERIC(6,4)` — persisted near-ATM IV for historical IV rank (`utils/iv_history.py`)
- `recommendations.prior_action / prior_conviction_score / revision_number / revised_at` — same-day revision tracking
- **All user-owned tables** — `user_id INTEGER REFERENCES users(id)` added by `_ensure_user_id_columns()` in `init_db.py` on first startup after the retrofit. Idempotent.

## Multi-tenancy & Auth

**`LEGACY_MODE` (default `True`).** Bypasses all JWT verification; every authenticated route receives the legacy admin user (`role=ADMIN`, email from `LEGACY_USER_EMAIL`). Frontend works without a login screen. **This is the current default** — flip to `False` only when frontend auth integration is wired to a real provider.

**Provider-agnostic JWT verifier** (`backend/auth/middleware.py`). Configure ONE of:
- `JWT_JWKS_URL` — for OIDC providers (Supabase Auth / Clerk / Auth0). Verifier fetches the JWKS, validates signature + issuer + audience.
- `JWT_HS256_SECRET` — for HS256-signed tokens (dev / simple flows).

**`get_current_user` FastAPI dependency.** Either returns the legacy admin (LEGACY_MODE) or validates `Authorization: Bearer <jwt>` and upserts a `User` row keyed by `(provider=iss, provider_user_id=sub)`. Email-based linking only happens for users with no `provider_user_id` (i.e., the legacy admin first time it logs in via real auth). **Cross-tenant access returns 404, not 403** — it doesn't leak the existence of other users' rows. Every authenticated request idempotently calls `ensure_subscription` (cheap SELECT when row exists) so pre-retrofit users self-heal.

**Dev-token endpoint.** `POST /api/dev/token { email }` mints a short-lived HS256 JWT for local testing. Gated by `DEV_TOKEN_ENABLED=true` AND a `JWT_HS256_SECRET`. **Never enable in production** — it lets anyone mint a token for any email.

**Smoke-testing JWT mode.** Use the override file:
```bash
docker compose -f docker-compose.yml -f docker-compose.auth-test.yml up -d backend
# revert: docker compose up -d backend
```

## Entitlements & Credits

Service: `backend/services/entitlements.py`. The SaaS gatekeeper. Every gated route calls `check_and_consume(db, user, "research")` (or `"deep_options"`, `"scanner"`) before doing expensive work.

**Tier registry** (`ENTITLEMENTS` dict): FREE / STARTER / PRO / PREMIUM / ADMIN. Each tier defines monthly credit quotas, feature flags (`alerts_enabled`, `custom_signal_weights`, `position_aware_recs`, `api_access`, `real_time_data`), and numeric caps (`max_watchlist_tickers`, `max_open_positions`, `max_universe_slots`, `research_retention_days`). The `UNLIMITED = 1_000_000` sentinel represents "no cap"; frontend renders as `∞` via `fmtCount(n, sentinel)`.

**Tier resolution.** `get_tier_for(user, sub)`:
1. `user.role == "ADMIN"` → ADMIN tier (overrides any subscription, no DB writes during consume)
2. No subscription OR sub.status not in (ACTIVE, TRIAL) → FREE
3. Otherwise → `sub.tier`

**Credit lifecycle.** Three tables:
- `subscriptions` — one per user; `tier`, `status`, `current_period_end`, `external_provider`, `external_subscription_id`. Stripe webhook target.
- `credit_balances` — `(user_id, feature)` → live `balance` + `last_grant_at`.
- `credit_ledger` — append-only audit (`delta`, `balance_after`, `reason`, `ref_id`).

**Lazy monthly grants.** `check_and_consume` rolls a 30-day window keyed off `last_grant_at`: if elapsed, balance is RESET (not added) to the tier's monthly quota and a `monthly_grant` ledger row is written before the consume. Replaced by Stripe `invoice.payment_succeeded` webhook handling once billing ships. Monthly grants are "use it or lose it"; **purchased credit packs go through `grant_credits` with `reason="credit_pack"` and ARE additive — they never expire.**

**Errors.** `QuotaExceeded` raises 402 with `{"error": "quota_exceeded", ...}`. `FeatureLocked` raises 402 with `{"error": "feature_locked", ...}` when the user's tier has zero monthly quota AND no purchased packs (different upgrade message).

**Currently gated:** `POST /research/{ticker}` consumes `research`. `POST /options/deep/{ticker}` and `POST /scanner/run` are NOT gated yet — wire them similarly when needed.

## Async SQLAlchemy Gotchas

- **Never lazy-load relationships** on async sessions. Always use `selectinload()` (or `joinedload()`) in query options. Lazy access triggers `MissingGreenlet` crash.
- **Pipeline phases must commit explicitly.** Each phase in `scheduler.py` calls `await session.commit()` after success and `await session.rollback()` on error. Services that only `flush()` (analyst_tracker, earnings_calendar) rely on the phase runner to commit.
- The `.env` file is loaded by pydantic-settings relative to CWD. `config.py` searches `[".env", "../.env"]` so it works whether running from `backend/` or project root.

## yfinance Data Quality

yfinance returns inconsistent types that cause asyncpg crashes if not sanitized:
- **NaN for integers**: `volume`, `openInterest` can be `float('nan')`. Use `_safe_int()` helper in `options_analyzer.py`.
- **NaN/Inf for Decimals**: `stock_price`, `iv_rank`, `put_call_ratio` need NaN/Inf guards. Use `_safe_decimal()`.
- **Float for VARCHAR fields**: `earnings_time` and `fiscal_quarter` may be float or large numbers. Sanitized in `earnings_calendar.py` via `_sanitize_earnings_time()` (keeps BMO/AMC, drops numeric garbage) and `_sanitize_string_field()` (drops large numeric strings like revenue estimates).
- **Fields widened**: `earnings_time` is `String(50)` (was 20), `fiscal_quarter` is `String(50)` (was 10).

## API Endpoints

User-owned endpoints (positions, research, deep-options, strikes/snapshots) are **scoped by `current_user.id`**: reads filter, writes set, cross-tenant id access returns 404. ADMIN role (legacy user) bypasses entitlement gating.

```
GET  /api/me                           # Current user (id, email, role, provider, legacy_mode)
GET  /api/me/entitlements              # Tier + feature flags + monthly quotas + credit balances
POST /api/dev/token                    # Mint dev HS256 JWT { email } (gated by DEV_TOKEN_ENABLED)
GET  /api/watchlist                    # Active watchlist with status tags; ?date= for historical
GET  /api/watchlist/history            # Historical snapshots; ?ticker=&days=
GET  /api/watchlist/changes            # Entrants/exiters for a date; ?date=
POST /api/watchlist                    # Add manual ticker { "ticker": "AAPL" }
DELETE /api/watchlist/{ticker}         # Remove manual ticker
PUT  /api/watchlist/{ticker}/lock      # Toggle lock (protected from rotation)
GET  /api/pipeline-dates               # Distinct snapshot dates (last 90 days)
GET  /api/recommendations              # Recs sorted by conviction; ?action=&min_conviction=&date=
GET  /api/recommendations/{ticker}     # Ticker rec history (last 30 days)
GET  /api/news                         # News feed; ?mode=general|ticker|watchlist&ticker=&min_relevance=0.3&category=&impact_level=&limit=
GET  /api/catalysts                    # Upcoming earnings (14 days) with fiscal quarter + EPS
GET  /api/reports/daily                # Download PDF daily report; ?date= (default today)
GET  /api/options/watchlist/strikes     # Strike recs for all watchlist tickers; ?budget=
GET  /api/options/{ticker}             # Latest options snapshot
GET  /api/options/{ticker}/strikes     # Strike rec for one risk level; ?risk=&budget=
GET  /api/options/{ticker}/strikes/all # Strike recs for all 3 risk levels; ?budget=
POST /api/options/deep/{ticker}        # Run expert-level options analysis (greeks, vol structure, strategy, hidden risks)
GET  /api/options/deep                 # List deep analyses; ?ticker=&limit=&offset=
GET  /api/options/deep/{id}            # Single deep analysis
DELETE /api/options/deep/{id}          # Delete a deep analysis
POST /api/strikes/snapshots            # Save strike scan results { budget, results }
GET  /api/strikes/snapshots            # Load saved snapshot; ?date=
GET  /api/strikes/snapshots/dates      # List dates with saved snapshots
GET  /api/trends/{ticker}               # Daily trend data with SMA; ?days=20&sma=5
GET  /api/status                       # Health check, last refresh times
POST /api/refresh                      # Legacy full pipeline trigger (same as /pipeline/run with no body)
POST /api/pipeline/run                 # Start pipeline; body { phases?: string[] } for selective runs
GET  /api/pipeline/status              # Poll pipeline progress (status, current phase, completed phases)
POST /api/research/{ticker}            # On-demand full analysis for any ticker (~30-60s)
GET  /api/research                     # List research results; ?ticker=&limit=&offset=
GET  /api/research/{id}                # Single research result
DELETE /api/research/{id}              # Delete research result
GET  /api/universe                     # Universe grouped by sector + pending candidate count
POST /api/universe                     # Add stock to universe { ticker, sector }
DELETE /api/universe/{ticker}          # Soft-remove stock from universe
GET  /api/universe/candidates          # List pending discovery candidates
POST /api/universe/candidates/{id}/approve  # Approve candidate into sector { sector }
POST /api/universe/candidates/{id}/dismiss  # Dismiss candidate
POST /api/universe/discover            # Trigger discovery manually
POST /api/positions                    # Create position { ticker, position_type, quantity, entry_price, ... }
GET  /api/positions                    # List positions; ?status=OPEN/CLOSED, ?ticker=
GET  /api/positions/{id}               # Single position with P&L + recommendation
PUT  /api/positions/{id}               # Update mutable fields (stop_loss, target, notes, current_price)
POST /api/positions/{id}/close         # Close position { close_price, notes? }
DELETE /api/positions/{id}             # Hard delete position
POST /api/positions/{id}/refresh-price # Refresh stock price via yfinance
GET  /api/scanner/universe             # List scanner universe (active tickers, grouped by theme)
POST /api/scanner/universe             # Add ticker { ticker, theme? }
DELETE /api/scanner/universe/{ticker}  # Soft-remove ticker
GET  /api/scanner/dates                # Distinct scanner run dates (180 day window)
GET  /api/scanner/results              # Scanner rows; ?date=&tier=&theme=
POST /api/scanner/run                  # Trigger scanner run (background; poll /scanner/status)
GET  /api/scanner/status               # Poll run state (running, started_at, last_result)
GET  /api/industries                   # Latest industry recommendations (one row per sector); ?date=
GET  /api/industries/{name}            # Industry detail: latest + 30d history + today's member recs (uses :path so AI/Semiconductors works)
GET  /api/charts/datasets              # List available chart datasets + their metric/aggregation options (drives form rendering)
POST /api/charts/query                 # Run chart query — { dataset, spec } → { series, x_label, y_label, chart_type, meta }
```

## Frontend

### Auth + Provider Hierarchy

`App.tsx` wraps every route in `AuthProvider` → `EntitlementsProvider` → (other context providers). Every protected page is wrapped in `<RequireAuth>` which redirects to `/login` when `user` is null (won't happen in LEGACY_MODE; the legacy admin always resolves). `/login` is the only public route.

- `AuthContext` — fetches `/api/me` on mount + on token change; persists token in `localStorage["vela.access_token"]`. Axios `request` interceptor in `utils/api.ts` attaches `Authorization: Bearer <token>` automatically (no-op when no token).
- `EntitlementsContext` — fetches `/api/me/entitlements`; exposes `data / isAdmin / isUnlimited(n) / refresh`. `fmtCount(n, sentinel)` renders `∞` when `n >= sentinel`.
- `LoginPage` — email-only form that calls `POST /api/dev/token`. **Real auth provider integration is deferred** — when adopted (Supabase / Clerk / Auth0), replace this page's body but keep the `setToken` → `refresh()` → navigate flow intact.
- `AppNav` — top nav rebranded **Vela**. Right side: 🔬 credit balance widget (research credits this period / monthly quota), tier chip (FREE/STARTER/PRO/PREMIUM/ADMIN, color-coded), `LEGACY` chip when backend is in legacy mode, user initials avatar, sign-out (only when an explicit token is held).

### Dashboard Layout

The app uses react-router-dom with routes: `/` (Dashboard), `/universe` (Universe), `/research` (Research), `/options-lab` (Options Lab), `/scanner` (Scanner), `/industries` (Industries), `/charts` (Charts), `/positions` (Positions), `/knowledge` (Knowledge), `/login` (public). A top nav bar (`AppNav`) links all protected pages. Nginx SPA fallback (`try_files $uri $uri/ /index.html`) handles client-side routing.

The Dashboard has the following sections:
1. **StatusBar** — Date stepper (left/right arrows to navigate pipeline days), refresh button, system status
2. **WatchlistChanges** — Horizontal bar showing NEW_ENTRANT (green) and REMOVED (red) badges for the selected date
3. **Watchlist + TickerDetail** — 3:2 column layout. Left: sector-grouped stock cards with status/manual/lock badges and add-ticker input. Right: detail panel on click with trend charts (price/conviction + IV/sentiment with configurable SMA), price, signals, options flow (sentiment-rated), and strike recommender.
4. **Recommendations** — Expandable cards with signal bullet lists, price/risk row, entry/exit strategy
5. **Strike Scanner** — Budget slider, "Scan Watchlist" button, "Save Snapshot" button. View selector switches between live scan and saved historical snapshots. Results in 2-column grid with risk level tabs.
6. **News + Catalysts** — 3:2 column layout. News timeline with mode selector (All/Watchlist/Ticker) and upcoming earnings calendar with countdown (days/hours). Ticker and Watchlist modes show relevance-scored ticker badges on each article.

### Key UI Features

- **Manual watchlist**: Input field in watchlist header to add tickers. Manual entries show purple MANUAL badge and X remove button on hover.
- **Lock toggle**: Hover any card to see lock/unlock icon. Locked tickers show 🔒 and are protected from rotation.
- **Strike Recommender**: Budget slider ($50-$10,000), single "Find Strikes" fetch returns all 3 risk profiles, tabs switch instantly. Green/gray dots on tabs indicate which profiles have results. Call cards (green border) and put cards (red border) show strike, expiry, premium, delta, breakeven, OI, and explanation.
- **Sentiment indicators**: Options flow metrics (IV Rank, Put/Call Ratio, volumes) show color-coded sentiment tags (Bullish/Bearish/Neutral/Elevated).
- **Date navigation**: Historical dates poll-disabled, driven from `watchlist_daily_snapshot`. Today drives from active watchlist + snapshot overlay.

### Universe Page

Universe management at `/universe`. Two-tab layout: **Universe** tab shows all stocks grouped by sector with source badges (SEED/MANUAL/DISCOVERED), add-ticker input with sector dropdown, and remove buttons. **Candidates** tab shows pending discovery candidates with price, change %, market cap, rationale, sector dropdown defaulting to suggested sector, and approve/dismiss buttons. "Run Discovery" button triggers manual discovery scan. Pending count badge on the Candidates tab.

### Research Page

On-demand single-ticker analysis at `/research`. Enter any ticker (not just watchlist members) to run a full pipeline assessment: analyst ratings, news/sentiment, earnings, options flow, signal stacking, and recommendation. Results are persisted to `research_results` table with JSON-embedded options data and suggested contracts.

Layout: ticker input + "Analyze" button at top, 3:2 grid-to-detail layout below. Research grid shows cards with ticker, action badge, conviction bar, price, timestamp, and delete button on hover. Click a card to open the detail panel (same visual patterns as TickerDetail: price/targets, conviction bar, signal bullets, entry/exit, options flow, suggested options, trend chart, strike recommender). Analysis takes ~30-60s per ticker (runs all pipeline phases for a single ticker).

### Positions Page

Portfolio tracking at `/positions`. Informational only — no broker integration. Supports CALL, PUT, and STOCK position types. Summary bar shows open count, total unrealized P&L, win/loss counts. Open/Closed tabs filter positions. 3:2 grid layout: position cards (left) with P&L-colored borders, detail panel (right) with recommendation overlay for watchlist tickers or "Run Analysis" button for non-watchlist tickers. Inline AddPositionForm with conditional fields (strike/premium/expiry for options). Dashboard integration: TickerDetail's "Open Position" button navigates to `/positions?open=true&ticker=X&...` with pre-filled form data. P&L: STOCK = (current - entry) * qty; CALL/PUT = (current - premium) * qty * 100.

### Knowledge Page

Documentation page at `/knowledge`. Tabs: Trading Guide, Signals, Classification, Watchlist, Strikes, Pipeline. Trading Guide includes daily workflow, signal quality for weeklies, 8 scenario playbooks, risk management rules, feature usage tips, and quick decision matrix.

### Design System

Dark theme. Font: Inter. Background: `#0f1117` (page), `#161b22` (cards), `#21262d` (borders). Text: `#e6edf3` (primary), `#8b949e` (secondary).

Action colors: STRONG_BUY `#2ea043`, BUY `#56d364`, HOLD `#d29922`, SELL `#f85149`, STRONG_SELL `#da3633`. New entrant: `#58a6ff`. Manual badge: purple.

## Deployment

Production is deployed on a Hetzner VPS at `204.168.198.65`. Services are exposed as:
- **Frontend**: http://204.168.198.65:3000
- **Backend API**: http://204.168.198.65:8000

SSH into the server to manage the deployment:
```bash
ssh root@204.168.198.65
```

Useful server-side commands:
```bash
# Check container status
docker compose ps

# View all logs (last 200 lines, follow)
docker compose logs --tail=200 -f

# View backend logs only
docker compose logs --tail=200 -f backend

# Full rebuild and restart
docker compose up --build -d
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
```

For local dev, `.env` at project root with `DATABASE_URL` pointing to `localhost:<port>`. If local PostgreSQL occupies port 5432, map Docker db to another port (e.g., `5433:5432` in docker-compose.yml).

## Known Limitations

- **Finnhub free tier**: The `/api/v1/stock/upgrade-downgrade` endpoint returns 403. Analyst ratings and insider trades use FMP as primary source; Finnhub serves as fallback only.
- **FMP API key**: Required (Starter plan, $22/mo). Covers analyst ratings, earnings (with EPS estimates), insider trades, and stock news. Without it, all FMP methods gracefully fall back to Finnhub/yfinance. Rate limit: 300 requests/min, 20GB bandwidth/30 days (no daily call cap).
- **Alembic adopted (May 2026).** Schema migrations live in `backend/alembic/versions/`. Container entrypoint runs `alembic upgrade head` before uvicorn. `db/models.py` is the source of truth — generate diffs via `alembic revision --autogenerate -m "..."` and review before applying.
- **`strike_snapshots` unique constraint**: Currently `UNIQUE(snapshot_date, ticker)` — global, not per-user. In LEGACY_MODE this is fine (one user); in JWT mode two different users saving the same `(date, ticker)` will collide. Fix when adopting Alembic: change to `UNIQUE(snapshot_date, ticker, user_id)`.
- **Watchlist + multibagger universe per-user scoping**: Tables have `user_id` columns (backfilled to legacy admin), but routes don't filter by user yet — the daily pipeline writes them globally. Per-user scoping is deferred to the Custom Universe Slots feature (see `vela-strategy.html` §2).
- **Container timezone is UTC**: `date.today()` returns the UTC date, not US Eastern. CLAUDE.md says "All times US Eastern" for the scheduler, but that's intent — the container has no `TZ` env set. Mismatches happen in the 00:00–04:00 UTC window (= 8 PM–midnight EDT). Fix by adding `TZ: America/New_York` to the backend service in `docker-compose.yml` if it bites.
- **FinBERT first load**: Model downloads ~500MB from HuggingFace on first pipeline run. Subsequent runs use cached model. Container restarts re-download unless a volume is mounted for the HF cache.
- **yfinance weekend data**: Prices and volumes are stale on weekends/off-hours. Pipeline runs on non-trading days will produce similar recommendations.

## Sector Universe

8 sectors with ~10-17 stocks each (~100 total universe, stored in `universe_stocks` table). Seeded from `config.SECTORS` on first init; expandable via Universe page (manual add or discovery approval). Active watchlist is max 60 (12 per sector). Sectors: AI/Semiconductors, Fintech/Payments, Energy/Commodities, Healthcare/Biotech, Consumer/Cloud/Enterprise, Industrials/Defense, Power/Utilities/Nuclear, Communications/Media.

## Industry (Sector-level) Recommendations

Separate analyzer in `services/industry_analyzer.py` produces one BUY/HOLD/SELL recommendation per industry as its own unit — not an aggregation of ticker convictions. Runs as final pipeline phase `industries` after ticker-level `recommendations`. UI: card grid on Dashboard + full detail view at `/industries` with 30d conviction sparkline.

- **Signals** (7): Breadth (% bullish members, % above 50d SMA), Cap-Weighted Conviction (large caps matter more), Sector ETF technicals (RSI-14 + 20d momentum of SOXX/XLF/XLE/XLV/XLK/ITA/XLU/XLC per `config.SECTOR_ETFS`), Aggregated News Sentiment (FinBERT avg across sector-relevant articles), Geopolitical Impact (via `utils/geopolitical.py` — sector map extended to 8 sectors), Catalyst Density (sector earnings in next 14d).
- **Scoring**: composite clamped [-100, +100]. Same classification bands as ticker recs: ≥60 STRONG_BUY, ≥30 BUY, ≥-15 HOLD, ≥-30 SELL, ≤-31 STRONG_SELL.
- **Table**: `industry_recommendations` with `uq_ind_rec_date_industry`. Re-runs same-day replace via delete-before-insert. Stores raw observations + JSON signals array + top-3 representative tickers.
- **ETF RSI/momentum**: fetched via yfinance `history(period="3mo")`; RSI computed with Wilder's method inline.
- **Geopolitical sector impact map**: `_SECTOR_IMPACT` in `utils/geopolitical.py` now covers all 8 sectors across 6 event types. Net geopolitical contribution is capped at ±25 per industry per run to prevent correlated events from dominating.

## Charts (dynamic visualization builder)

Dataset-first BI in `services/chart_builder.py`, UI at `/charts`. Three curated datasets — picked over universal pivot to avoid nonsensical column combinations on a heterogeneous schema:

- **ticker_time_series**: one ticker, multi-metric line chart over date range. Metrics span recommendations (price, conviction, signal_count, target/stop), options snapshots (iv_rank, iv_percentile, put_call_ratio, atm_iv, volumes), and aggregated news (avg sentiment, article count). Optional SMA smoothing window.
- **signal_breakdown**: bar chart of recommendation signal occurrences. Aggregations: count / sum / avg / min / max over signal points. Filters: tickers, actions, date range. Top-N limit.
- **industry_comparison**: industry-level metric across industries. Two views — `trend` (line per industry over date range) or `snapshot` (bar at latest date). Metrics from `industry_recommendations` columns (conviction, breadth %, ETF RSI/momentum, news sentiment, geopolitical, catalysts).

Each dataset has its own handler that builds parametrized SQL (no string concat). Standardized response: `{dataset, x_label, y_label, chart_type, series: [{name, data: [{x, y}], ...}], meta}`. Frontend uses Recharts (`LineChart` / `BarChart`) with `unifyRows` to merge multi-series across a shared X axis. Form state syncs to URL params (`useSearchParams`) so charts are shareable / bookmarkable.

To add a new dataset: define a handler method in `ChartBuilder.query()` dispatch and add the dataset entry to `/api/charts/datasets`. Frontend form picks up new metric keys automatically; new dataset shapes need a new form component in `ChartsPage.tsx`.

## Multi-bagger Scanner (positional, separate from tactical engine)

Separate universe + separate signal stack in `services/multibagger_scanner.py`, UI at `/scanner`. Targets 3–12 month horizon "shooting stars" (SNDK/NVDA/SMCI-type multi-baggers) rather than tactical 2-week setups. Do **not** mix this into the recommendation engine — the horizons are incompatible.

- **Universe**: `multibagger_universe` table, ~95 growth candidates seeded from `services/multibagger_seed.py` grouped by theme (AI_MEMORY, AI_COMPUTE, AI_APPS, POWER_NUCLEAR, ROBOTICS_SPACE, AUTONOMY_MOBILITY, FINTECH_CRYPTO, BIOTECH_GLP1, QUANTUM, SAAS_GROWTH, RECENT_IPO_SPINOFF). Extendable via `POST /api/scanner/universe` or the page UI.
- **Signals** (6): Revenue Growth Accelerating (YoY Q-latest vs Q-prior in pp, up to +25), Margin Expansion (gross margin YoY Δ in pp, up to +20), Top-Decile 12m Momentum (percentile rank of 12m return vs scanner universe, up to +25), Analysts Chasing (price ≥ avg PT, up to +15), Recent IPO/Spinoff (stock age ≤24mo via `firstTradeDateEpochUtc`, up to +15), Revision Cluster (90d count of bullish actions from `analyst_ratings`, up to +20).
- **Scoring / tier**: composite clamped [0, 120]. Tier: HOT (≥4 signals fired AND composite ≥60), WATCH (≥3 signals OR composite ≥40), MONITOR (≥2 signals), IGNORE otherwise.
- **Data sources** (added in `utils/data_sources.py`): `get_income_statement_quarterly` (FMP quarterly income statements, 5 periods), `get_long_horizon_returns` (yfinance 1y history → 3m/6m/12m returns), `get_stock_age_months` (yfinance `firstTradeDateEpochUtc`).
- **Run cadence**: Manual via `POST /api/scanner/run` (background task; ~3–5 min for 95 tickers on cold cache). Not yet wired to APScheduler — eventually runs weekly. Each run replaces same-date rows via delete-before-insert; `uq_mbs_date_ticker` prevents duplicates.
- **Numeric column widths**: Ratios (`rev_growth_*`, `pt_chase_ratio`, margins) use `Numeric(10–12, 4)` to accommodate small-denominator blowups (a company going from $1M → $500M revenue produces a 500.0 YoY ratio that overflows narrower types).

## Pipeline Idempotency

Pipeline re-runs on the same day are safe. `watchlist_manager.rotate_watchlist()` deletes existing snapshots for the date before inserting new ones. The `uq_snapshot_date_ticker` constraint prevents duplicates. Recommendations use `uq_rec_date_ticker` — re-runs update existing rows. Scanner runs use `uq_mbs_date_ticker` with delete-before-insert on `run_date`.


## Maintaining this file

This file is the project's living memory. Keep it accurate and lean.

**At the start of every session**, read this file fully before acting.

**During and at the end of every task**, update this file when any of the following happens:
- A new architectural decision, convention, or constraint is established
- A non-obvious gotcha, workaround, or "here be dragons" area is discovered
- A command, script, file path, dependency, or environment requirement changes
- A pattern in this file turns out to be wrong, outdated, or superseded
- The user corrects you on something you'll likely get wrong again

**When updating, follow these rules:**
1. **Prune aggressively.** If information is no longer true, delete it — do not leave stale notes with "(deprecated)" tags unless the deprecation itself is load-bearing context.
2. **Prefer editing over appending.** If a section already covers the topic, revise it in place rather than adding a new one.
3. **Record decisions, not narration.** "We use Zod for runtime validation at API boundaries" — yes. "We discussed validation options today" — no.
4. **Keep it skimmable.** Short sections, concrete examples, no filler. If a section exceeds ~15 lines, consider whether half of it is still essential.
5. **Do not record speculation.** Only commit patterns that are actually in use or firmly decided. Ideas and open questions belong in issues/TODOs, not here.
6. **Surface the change.** When you update this file, briefly mention what you changed and why in your reply, so I can sanity-check it.

If you're unsure whether something belongs here, ask before writing it.