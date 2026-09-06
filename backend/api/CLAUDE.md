# API Routes

All routes in `routes.py`. User-owned endpoints (positions, research, deep-options, strikes/snapshots) are **scoped by `current_user.id`**: reads filter, writes set, cross-tenant id access returns 404. ADMIN role (legacy user) bypasses entitlement gating.

```
GET  /api/me                           # Current user (id, email, role, provider, legacy_mode)
GET  /api/me/entitlements              # Tier + feature flags + monthly quotas + credit balances
POST /api/dev/token                    # Mint dev HS256 JWT { email } (gated by DEV_TOKEN_ENABLED)
GET  /api/watchlist                    # Active watchlist with status tags; ?date= for historical
GET  /api/watchlist/history            # Historical snapshots; ?ticker=&days= (no portal consumer)
GET  /api/watchlist/changes            # Entrants/exiters for a date; ?date=
POST /api/watchlist                    # Add manual ticker { "ticker": "AAPL" }
DELETE /api/watchlist/{ticker}         # Remove manual ticker
PUT  /api/watchlist/{ticker}/lock      # Toggle lock (protected from rotation)
POST /api/watchlist/rotate-out/preview # Preview manual rotate-out { tickers: [] } → per-ticker { gate{blocked,reasons}, candidate|null }. Read-only; scores universe for next-in-line. Gate = "strong imminent potential" (PRE_POSITION / leading signal / active catalyst / unusual options / bullish high conviction)
POST /api/watchlist/rotate-out/commit  # Commit swap { pairs:[{remove,add}] }. Re-validates gate server-side (hard block → blocked[]); removes outgoing (auto OR manual — unlike DELETE which only removes is_manual), adds candidate grace-locked (is_locked=true), then spawns background rec generation. → { status, committed, blocked, analysis_started }
GET  /api/watchlist/rotate-out/status  # Poll background analysis state → { running, started_at, tickers{ticker:pending|analyzing|done|error}, errors }
GET  /api/pipeline-dates               # Distinct snapshot dates (last 90 days)
GET  /api/recommendations              # Recs; ?action=&min_conviction=&date=&sort=conviction|revised_at  (default conviction; revised_at suppresses the personalized weighted re-sort)
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
GET  /api/strikes/snapshots/dates      # List dates with saved snapshots (no portal consumer)
GET  /api/trends/{ticker}               # Daily trend data with SMA; ?days=20&sma=5
GET  /api/status                       # Health check, last refresh times
POST /api/refresh                      # Legacy full pipeline trigger (same as /pipeline/run with no body; no portal consumer)
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
POST /api/positions                    # Create position { ticker, position_type, quantity, entry_price, recommendation_id?, ... } (rec link validated quietly; bogus id → unlinked)
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
GET  /api/outcomes/summary             # Recommendation performance aggregates; ?days=90&min_signal_n=3 → overall / by_action / per-signal hit rates
GET  /api/charts/datasets              # List available chart datasets + their metric/aggregation options (drives form rendering)
POST /api/charts/query                 # Run chart query — { dataset, spec } → { series, x_label, y_label, chart_type, meta }
```

## Maintaining this file

Update this file every time a route is added, removed, renamed, or has its method / query params / body shape changed. Keep the one-line descriptions concise — anything longer belongs in route docstrings or the relevant service file. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
