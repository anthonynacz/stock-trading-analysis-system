# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EdgeFlow is an automated daily analyst-rating-to-options-trade recommendation engine. It monitors analyst upgrades/downgrades, earnings catalysts, market news, and options flow data to generate actionable trade recommendations with strike-level options suggestions. Full-stack app: FastAPI backend, React/TypeScript frontend, PostgreSQL database, all containerized with Docker Compose.

## Architecture

```
edgeflow/
├── backend/         # Python 3.12, FastAPI, SQLAlchemy, APScheduler
│   ├── main.py      # FastAPI entrypoint
│   ├── config.py    # Env vars, API keys, sector universe, constants
│   ├── db/          # PostgreSQL schema (init_db.py), SQLAlchemy models, connection pool
│   ├── services/    # Core business logic (see Service Layer below)
│   ├── api/         # REST routes (routes.py)
│   └── utils/
│       ├── data_sources.py   # API clients (FMP, yfinance, Finnhub, NewsAPI)
│       ├── scoring.py        # Keyword-based scoring, impact levels, composite scores
│       └── finbert.py        # FinBERT sentiment model (ProsusAI/finbert)
├── frontend/        # React 18 + TypeScript, Vite, Tailwind CSS
│   └── src/
│       ├── App.tsx                     # BrowserRouter with all routes
│       ├── pages/
│       │   ├── Dashboard.tsx           # Main dashboard with date stepper
│       │   ├── ResearchPage.tsx        # On-demand single-ticker analysis page
│       │   ├── PositionsPage.tsx       # Portfolio tracking with P&L and rec overlay
│       │   └── KnowledgePage.tsx       # Signal docs, trading guide, classification
│       ├── components/
│       │   ├── AppNav.tsx              # Top navigation (all pages)
│       │   ├── WatchlistGrid.tsx       # Sector-grouped stock cards with lock/manual badges
│       │   ├── WatchlistChanges.tsx    # Entrants/exiters bar
│       │   ├── TickerDetail.tsx        # Right panel: price, signals, options flow, strike recommender
│       │   ├── ResearchDetail.tsx      # Research result detail panel
│       │   ├── StrikeRecommender.tsx   # Risk-profiled options strike recommendations
│       │   ├── RecommendationCard.tsx  # Expandable rec cards with signal bullets
│       │   ├── StatusBar.tsx           # Date stepper, refresh, system status
│       │   ├── CatalystCalendar.tsx    # Upcoming earnings with countdown
│       │   ├── NewsTimeline.tsx        # Scrollable news feed with ticker badges
│       │   ├── NewsModeSelector.tsx    # News mode toggle (All/Watchlist/Ticker)
│       │   └── TrendChart.tsx          # Recharts dual line charts with SMA overlays
│       ├── hooks/useEdgeFlow.ts        # Data fetching hooks with polling
│       ├── types/index.ts              # TypeScript interfaces
│       └── utils/api.ts                # Axios API client
├── docker-compose.yml   # postgres:16 + backend + frontend
└── .env                 # API keys (FMP, Finnhub, NewsAPI, DATABASE_URL)
```

### Service Layer (backend/services/)

The daily workflow runs as a phased pipeline orchestrated by APScheduler in `scheduler.py`. All times US Eastern:

0. **universe_discoverer.py** — Scans FMP market lists (most active, gainers, losers) and news-trending tickers to surface candidates for user approval into the universe. Runs at 05:00 EST before watchlist rotation. Candidates go to `discovery_candidates` table with `PENDING` status; user approves/dismisses via `/api/universe/candidates/{id}/approve|dismiss`.
1. **watchlist_manager.py** — Scores stocks from `universe_stocks` DB table (not hardcoded config) across 5 sectors, applies liquidity filters (≥$5B cap, ≥2M vol, ≥1K options vol, ≥5 analysts), rotates watchlist (max 30 active, max 5 changes/day). Manual (`is_manual`), locked (`is_locked`), and **unusual options activity** entries are protected from rotation. Unusual options = today's call volume ≥3x the 20-day historical average (queried from `options_snapshots`); toxic tickers override this protection. Uses delete-before-insert for idempotent daily snapshots. Composite score includes `recommendation_conviction` (15% weight) — feeds the recommendation engine's output back into watchlist ranking so SELL/STRONG_SELL stocks are deprioritized. Toxic removal: tickers with 3+ consecutive SELL/STRONG_SELL recommendations are flagged for removal regardless of composite score (`TOXIC_CONVICTION_DAYS` in config).
2. **analyst_tracker.py** — Detects rating changes since prior close, classifies as TIER_CHANGE/PT_CHANGE/INITIATION/REITERATION, scores by firm tier (T1=bulge bracket, T2=mid-tier, T3=boutique)
3. **earnings_calendar.py** — Tracks earnings dates, manages catalyst windows (T-7 to T+10). Sanitizes yfinance garbage in `earnings_time` (float values → null) and `fiscal_quarter` (revenue estimates → null).
4. **news_scanner.py** — Fetches/categorizes news, scores sentiment via **FinBERT** (ProsusAI/finbert) using batch inference. FinBERT returns continuous scores in [-1.0, 1.0] with financial domain understanding. Model loads once (singleton in `utils/finbert.py`) and persists in memory. Tracks per-ticker relevance via `news_ticker_relevance` junction table (many-to-many). Relevance scored by text matching: HEADLINE=1.0, SUMMARY=0.7, API_RELATED=0.5, SEARCHED=0.3. Duplicate articles (same URL) skip MarketNews creation but backfill relevance rows for newly-relevant tickers.
5. **options_analyzer.py** — Pulls chains via yfinance, calculates IV rank, detects unusual activity (>1.5x avg volume). Includes **strike recommender** (`recommend_strikes` / `recommend_strikes_all`) with three risk profiles (conservative/moderate/aggressive) and budget-filtered contract selection.
6. **recommendation_engine.py** — Stacks signals into conviction scores (-100 to +100), generates rationale text, selects optimal options contracts, classifies STRONG_BUY through STRONG_SELL. Each day's recommendation is stored independently (`uq_rec_date_ticker` constraint). `analyze_single()` runs the same pipeline but returns a data dict without persisting to `recommendations` — used by the Research feature.

### Signal Stacking (conviction scoring)

Signals are stacked across eight categories. Classification thresholds: ≥60 STRONG_BUY, 30-59 BUY, -15 to 29 HOLD, -30 to -16 SELL, ≤-31 STRONG_SELL.

**Analyst signals**: T1/T2/T3 upgrades (+15 to +40) / downgrades (-15 to -40), PT raises (+10/+15) / cuts (-10/-15).
**Earnings signals**: Beat (+20), Beat+Raised (+30), Miss (-20), Miss+Lowered (-30), Active Catalyst Window (+5).
**Technical signals**: RSI oversold/overbought (±10/±15), price vs 50d SMA (±10), price vs 200d SMA (±5), 5d/20d momentum (±10), volume-confirmed moves (±10), short squeeze setup (+15), high short interest (-5).
**Drawdown signals**: Sharp 1d drop >5% (-15), rapid 2d drawdown >7% (-20), rapid 3d drawdown >10% (-15), distribution/heavy selling volume (-10). These fire independently and stack.
**Reversal signals**: Oversold Bounce Setup (+15/+20) and Strong Reversal Setup (+20/+25) fire only when drawdown is active AND RSI <30 AND price near support (200d SMA or 52w low). Selling exhaustion (low down-day volume) adds confidence.
**52-week & value signals (context-gated)**: Near 52w high (+5), deep pullback (+10 normally, +5 if actively declining). Below consensus PT (+10 normally, +5 if actively declining). Gating prevents value traps during falling-knife scenarios.
**Relative strength**: Stock vs SPY 5d momentum comparison. Outperforming (+10), underperforming (-10). SPY data fetched once per pipeline via `get_market_benchmark()`.
**OHLC candlestick signals**: Gap-Down After Up Day (-12), Upper Wick Rejection (-8), Close Near Low (-5), Close Near High (+5). Derived from Open/High/Low in yfinance 2-month history. Silently skip when OHLC unavailable — all close-only signals unaffected.
**Options signals**: Unusual call volume (+15) / put volume (-15) vs 20-day historical avg, put/call OI skew (±10), high IV rank (-10).
**News signals**: FinBERT sentiment scaled by magnitude and article count (±5 to ±15), sector tailwind/headwind (±10).
**Other**: Insider buying (+5) / selling (-5), stock already moved (-5).

Technical indicators (RSI-14, 5d/20d momentum, volume ratio, drawdown metrics, consecutive down days, down-day volume ratio, OHLC candlestick metrics) are computed from yfinance 2-month price history via `DataSourceClient.get_technical_indicators()`. Moving averages (50d, 200d), short interest, and 52-week range come from yfinance `.info` via `get_stock_data()`. SPY benchmark comes from `get_market_benchmark()`.

### Strike Recommender

Three risk profiles control delta ranges, DTE windows, and contract filtering:
- **Conservative**: delta 0.50-0.80, DTE 7-30 — ITM/ATM, high probability
- **Moderate**: delta 0.30-0.55, DTE 14-50 — ATM to slightly OTM, balanced
- **Aggressive**: delta 0.10-0.35, DTE 21-75 — OTM, maximum leverage

Filters: OI ≥ 10, bid-ask spread ≤ 1.50, premium * 100 ≤ budget. Delta estimated from moneyness with 3x slope. The `/strikes/all` endpoint fetches the options chain once and returns all three profiles in a single response.

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

PostgreSQL 16. Key tables: `sectors`, `universe_stocks`, `discovery_candidates`, `watchlist`, `watchlist_daily_snapshot`, `analyst_ratings`, `earnings_calendar`, `market_news`, `news_ticker_relevance`, `options_snapshots`, `suggested_options`, `recommendations`, `strike_snapshots`, `research_results`, `positions`. Schema created via `db/init_db.py` using SQLAlchemy models from `db/models.py`. The `recommendations` table must be created before `suggested_options` (FK dependency). `universe_stocks` is seeded from `config.SECTORS` on first init.

All `DateTime` columns use `DateTime(timezone=True)` (maps to PostgreSQL `TIMESTAMPTZ`). This is required because asyncpg strictly rejects tz-aware Python datetimes for `TIMESTAMP WITHOUT TIME ZONE` columns. Schema changes require dropping and recreating tables (no Alembic migrations yet).

### Key Columns Added After Initial Schema

These columns were added via `ALTER TABLE` and may need to be re-added if the database is recreated from models alone:
- `watchlist.is_manual` — `BOOLEAN NOT NULL DEFAULT FALSE` — marks manually-added tickers
- `watchlist.is_locked` — `BOOLEAN NOT NULL DEFAULT FALSE` — locks tickers from auto-rotation
- `watchlist_daily_snapshot` — `UNIQUE(snapshot_date, ticker)` constraint (`uq_snapshot_date_ticker`)

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

```
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
```

## Frontend

### Dashboard Layout

The app uses react-router-dom with routes: `/` (Dashboard), `/universe` (Universe), `/research` (Research), `/positions` (Positions), `/knowledge` (Knowledge). A top nav bar (`AppNav`) links all pages. Nginx SPA fallback (`try_files $uri $uri/ /index.html`) handles client-side routing.

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
FINNHUB_API_KEY=     # Finnhub (60 calls/min free) — used for news; ratings fallback only
NEWSAPI_KEY=         # NewsAPI.org (100 calls/day free) — news fallback
FMP_API_KEY=         # Financial Modeling Prep (Starter $22/mo, 300 req/min) — primary for ratings, earnings, insider trades, news
DATABASE_URL=postgresql+asyncpg://edgeflow:<password>@db:5432/edgeflow  # Docker internal
```

For local dev, `.env` at project root with `DATABASE_URL` pointing to `localhost:<port>`. If local PostgreSQL occupies port 5432, map Docker db to another port (e.g., `5433:5432` in docker-compose.yml).

## Known Limitations

- **Finnhub free tier**: The `/api/v1/stock/upgrade-downgrade` endpoint returns 403. Analyst ratings and insider trades use FMP as primary source; Finnhub serves as fallback only.
- **FMP API key**: Required (Starter plan, $22/mo). Covers analyst ratings, earnings (with EPS estimates), insider trades, and stock news. Without it, all FMP methods gracefully fall back to Finnhub/yfinance. Rate limit: 300 requests/min, 20GB bandwidth/30 days (no daily call cap).
- **No Alembic migrations**: Schema changes require dropping and recreating tables. On production, this means `docker compose down -v` to reset the pgdata volume. Manual columns (`is_manual`, `is_locked`) must be re-added via ALTER TABLE.
- **FinBERT first load**: Model downloads ~500MB from HuggingFace on first pipeline run. Subsequent runs use cached model. Container restarts re-download unless a volume is mounted for the HF cache.
- **yfinance weekend data**: Prices and volumes are stale on weekends/off-hours. Pipeline runs on non-trading days will produce similar recommendations.

## Sector Universe

5 sectors with ~13 stocks each (~68 total universe, stored in `universe_stocks` table). Seeded from `config.SECTORS` on first init; expandable via Universe page (manual add or discovery approval). Active watchlist is max 30 (6 per sector). Sectors: AI/Semiconductors, Fintech/Payments, Energy/Commodities, Healthcare/Biotech, Consumer/Cloud/Enterprise.

## Pipeline Idempotency

Pipeline re-runs on the same day are safe. `watchlist_manager.rotate_watchlist()` deletes existing snapshots for the date before inserting new ones. The `uq_snapshot_date_ticker` constraint prevents duplicates. Recommendations use `uq_rec_date_ticker` — re-runs update existing rows.


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