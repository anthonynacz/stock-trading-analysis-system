# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EdgeFlow is an automated daily analyst-rating-to-options-trade recommendation engine. It monitors analyst upgrades/downgrades, earnings catalysts, market news, and options flow data to generate actionable trade recommendations. Full-stack app: FastAPI backend, React/TypeScript frontend, PostgreSQL database, all containerized with Docker Compose.

## Architecture

```
edgeflow/
├── backend/         # Python 3.12, FastAPI, SQLAlchemy, APScheduler
│   ├── main.py      # FastAPI entrypoint
│   ├── config.py    # Env vars, API keys, constants
│   ├── db/          # PostgreSQL schema (init_db.py), SQLAlchemy models, connection pool
│   ├── services/    # Core business logic (see Service Layer below)
│   ├── api/         # REST routes + optional WebSocket
│   └── utils/       # data_sources.py (API clients), scoring.py
├── frontend/        # React 18 + TypeScript, Vite, Tailwind CSS, Recharts
│   └── src/
│       ├── pages/Dashboard.tsx        # Single-page dashboard
│       ├── components/                # WatchlistGrid, RecommendationCard, SignalBadge, etc.
│       ├── hooks/useEdgeFlow.ts       # Data fetching hooks
│       └── utils/scoring.ts
├── docker-compose.yml   # postgres:16 + backend + frontend
└── .env                 # API keys (FMP, Finnhub, NewsAPI, DATABASE_URL)
```

### Service Layer (backend/services/)

The daily workflow runs as a phased pipeline orchestrated by APScheduler in `scheduler.py`. All times US Eastern:

1. **watchlist_manager.py** — Scores ~70 stocks across 5 sectors, applies liquidity filters (≥$5B cap, ≥2M vol, ≥1K options vol, ≥5 analysts), rotates watchlist (max 30 active, max 5 changes/day)
2. **analyst_tracker.py** — Detects rating changes since prior close, classifies as TIER_CHANGE/PT_CHANGE/INITIATION/REITERATION, scores by firm tier (T1=bulge bracket, T2=mid-tier, T3=boutique)
3. **earnings_calendar.py** — Tracks earnings dates, manages catalyst windows (T-7 to T+10)
4. **news_scanner.py** — Fetches/categorizes news, assigns sentiment scores (-1.0 to 1.0)
5. **options_analyzer.py** — Pulls chains via yfinance, calculates IV rank, detects unusual activity (>1.5x avg volume)
6. **recommendation_engine.py** — Stacks signals into conviction scores (-100 to +100), generates rationale text, selects optimal options contracts, classifies STRONG_BUY through STRONG_SELL

### Signal Stacking (conviction scoring)

Bullish signals add points (+5 to +40), bearish signals subtract (-5 to -40), neutralizing factors (high IV, already-moved stock) subtract further. Classification thresholds: ≥60 STRONG_BUY, 30-59 BUY, -15 to 29 HOLD, -30 to -16 SELL, ≤-31 STRONG_SELL.

### Data Sources

Unified in `utils/data_sources.py` with rate limiting and fallback chains:
- **Analyst ratings**: Financial Modeling Prep API → Benzinga → MarketBeat scraping
- **Prices/fundamentals/options**: yfinance (primary, no key needed) → Alpha Vantage (backup)
- **News**: Finnhub → NewsAPI → SEC EDGAR RSS
- **Earnings**: yfinance → FMP

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

# Run backend tests
cd backend && python -m pytest

# Run a single backend test
cd backend && python -m pytest tests/test_watchlist.py::test_scoring -v

# Frontend tests
cd frontend && npm test

# Type check frontend
cd frontend && npx tsc --noEmit

# Lint frontend
cd frontend && npx eslint src/
```

## Database

PostgreSQL 16. Key tables: `sectors`, `watchlist`, `watchlist_daily_snapshot`, `analyst_ratings`, `earnings_calendar`, `market_news`, `options_snapshots`, `suggested_options`, `recommendations`. Schema created via `db/init_db.py` using SQLAlchemy models from `db/models.py`. The `recommendations` table must be created before `suggested_options` (FK dependency).

## API Endpoints

```
GET  /api/watchlist              # Active watchlist with status tags
GET  /api/watchlist/history      # Historical snapshots
GET  /api/recommendations        # Today's recs sorted by conviction DESC
GET  /api/recommendations/{ticker}
GET  /api/news                   # Filterable news feed
GET  /api/catalysts              # Upcoming catalyst calendar (14 days)
GET  /api/options/{ticker}
GET  /api/status                 # Health check, last refresh times
POST /api/refresh                # Manual full refresh trigger
```

## Frontend Design System

Dark theme. Font: Inter. Background: `#0f1117` (page), `#161b22` (cards), `#21262d` (borders). Text: `#e6edf3` (primary), `#8b949e` (secondary).

Action colors: STRONG_BUY `#2ea043`, BUY `#56d364`, HOLD `#d29922`, SELL `#f85149`, STRONG_SELL `#da3633`. New entrant badge: `#58a6ff`.

## Environment Variables

```
FMP_API_KEY=         # Financial Modeling Prep (250 calls/day free)
FINNHUB_API_KEY=     # Finnhub (60 calls/min free)
NEWSAPI_KEY=         # NewsAPI.org (100 calls/day free)
DATABASE_URL=postgresql://edgeflow:edgeflow@localhost:5432/edgeflow
```

## Sector Universe

5 sectors with ~13 stocks each (~70 total universe). Active watchlist is max 30 (6 per sector). Sectors: AI/Semiconductors, Fintech/Payments, Energy/Commodities, Healthcare/Biotech, Consumer/Cloud/Enterprise.
