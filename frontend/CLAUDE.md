# Frontend

React 18 + TypeScript, Vite, Tailwind CSS. Entrypoint: `src/main.tsx` → `src/App.tsx`.

```
src/
├── App.tsx                     # BrowserRouter; AuthProvider + EntitlementsProvider; RequireAuth on every protected route
├── contexts/
│   ├── AuthContext.tsx         # Fetches /api/me, holds token in localStorage, axios interceptor
│   ├── EntitlementsContext.tsx # Fetches /api/me/entitlements, exposes tier/credits/isAdmin
│   └── ResearchContext.tsx, OptionsLabContext.tsx
├── pages/                      # LoginPage, Dashboard, ResearchPage, PositionsPage, KnowledgePage, ...
├── components/                 # AppNav, RequireAuth, TickerDetail, RecommendationCard, StrikeRecommender, ...
├── hooks/useEdgeFlow.ts        # Data fetching hooks with polling
├── types/index.ts              # TypeScript interfaces
└── utils/api.ts                # Axios client; interceptor attaches Bearer <token>
```

## Common Commands

```bash
cd frontend && npm install && npm run dev   # local dev server
cd frontend && npx tsc --noEmit             # type check
```

## Auth + Provider Hierarchy

`App.tsx` wraps every route in `AuthProvider` → `EntitlementsProvider` → (other context providers). Every protected page is wrapped in `<RequireAuth>` which redirects to `/login` when `user` is null (won't happen in LEGACY_MODE; the legacy admin always resolves). `/login` is the only public route.

- `AuthContext` — fetches `/api/me` on mount + on token change; persists token in `localStorage["vela.access_token"]`. Axios `request` interceptor in `utils/api.ts` attaches `Authorization: Bearer <token>` automatically (no-op when no token).
- `EntitlementsContext` — fetches `/api/me/entitlements`; exposes `data / isAdmin / isUnlimited(n) / refresh`. `fmtCount(n, sentinel)` renders `∞` when `n >= sentinel`.
- `LoginPage` — email-only form that calls `POST /api/dev/token`. **Real auth provider integration is deferred** — when adopted (Supabase / Clerk / Auth0), replace this page's body but keep the `setToken` → `refresh()` → navigate flow intact.
- `AppNav` — top nav rebranded **Vela**. Right side: 🔬 credit balance widget (research credits this period / monthly quota), tier chip (FREE/STARTER/PRO/PREMIUM/ADMIN, color-coded), `LEGACY` chip when backend is in legacy mode, user initials avatar, sign-out (only when an explicit token is held).

## Dashboard Layout

The app uses react-router-dom with routes: `/` (Dashboard), `/universe` (Universe), `/research` (Research), `/options-lab` (Options Lab), `/scanner` (Scanner), `/industries` (Industries), `/charts` (Charts), `/positions` (Positions), `/knowledge` (Knowledge), `/login` (public). A top nav bar (`AppNav`) links all protected pages. Nginx SPA fallback (`try_files $uri $uri/ /index.html`) handles client-side routing.

The Dashboard has the following sections:
1. **StatusBar** — Date stepper (left/right arrows to navigate pipeline days), refresh button, system status
2. **WatchlistChanges** — Horizontal bar showing NEW_ENTRANT (green) and REMOVED (red) badges for the selected date
3. **Watchlist + TickerDetail** — 3:2 column layout. Left: sector-grouped stock cards with status/manual/lock badges and add-ticker input. Right: detail panel on click with trend charts (price/conviction + IV/sentiment with configurable SMA), price, signals, options flow (sentiment-rated), and strike recommender.
4. **Recommendations** — Expandable cards with signal bullet lists, price/risk row, entry/exit strategy. Header has a `Top conviction | Recently revised` segmented toggle (persisted to `localStorage["vela.rec_sort"]`) that switches the API's `sort=` param. `RecommendationCard` shows a `REV · Xh ago` chip on rows revised within the last 4h, with hover tooltip surfacing `revision_reason` (e.g. the intraday news headline that triggered the rescore). `StatusBar` shows "News Xm ago" pulled from `last_refresh.intraday_news`.
5. **Strike Scanner** — Budget slider, "Scan Watchlist" button, "Save Snapshot" button. View selector switches between live scan and saved historical snapshots. Results in 2-column grid with risk level tabs.
6. **News + Catalysts** — 3:2 column layout. News timeline with mode selector (All/Watchlist/Ticker) and upcoming earnings calendar with countdown (days/hours). Ticker and Watchlist modes show relevance-scored ticker badges on each article.

## Key UI Features

- **Manual watchlist**: Input field in watchlist header to add tickers. Manual entries show purple MANUAL badge and X remove button on hover.
- **Rotate-out (multi-select swap)**: An amber checkbox on each watchlist card and recommendation card (selection is keyed by ticker, shared across both views; disabled for locked/REMOVED tickers). `Dashboard` holds the `selected: Set<string>`. A floating `SelectionActionBar` appears while any are selected → "Rotate out (n)" opens `RotationPreviewModal`, which previews per-ticker the gate result (blocked rows red, excluded) and the proposed replacement candidate (or "no eligible replacement"), commits the confirmed swaps, then polls `useRotationStatus` (2s) showing per-ticker pending/analyzing/done/error until the new recs are generated. **Hard block**: tickers with strong imminent potential cannot be rotated out (no override). New entrants come in grace-locked (🔒). API wrappers: `previewRotateOut` / `commitRotateOut` / `getRotationStatus` in `utils/api.ts`.
- **Lock toggle**: Hover any card to see lock/unlock icon. Locked tickers show 🔒 and are protected from rotation.
- **Strike Recommender**: Budget slider ($50-$10,000), single "Find Strikes" fetch returns all 3 risk profiles, tabs switch instantly. Green/gray dots on tabs indicate which profiles have results. Call cards (green border) and put cards (red border) show strike, expiry, premium, delta, breakeven, OI, and explanation.
- **Sentiment indicators**: Options flow metrics (IV Rank, Put/Call Ratio, volumes) show color-coded sentiment tags (Bullish/Bearish/Neutral/Elevated).
- **Date navigation**: Historical dates poll-disabled, driven from `watchlist_daily_snapshot`. Today drives from active watchlist + snapshot overlay.

## Universe Page

Universe management at `/universe`. Two-tab layout: **Universe** tab shows all stocks grouped by sector with source badges (SEED/MANUAL/DISCOVERED), add-ticker input with sector dropdown, and remove buttons. **Candidates** tab shows pending discovery candidates with price, change %, market cap, rationale, sector dropdown defaulting to suggested sector, and approve/dismiss buttons. "Run Discovery" button triggers manual discovery scan. Pending count badge on the Candidates tab.

## Research Page

On-demand single-ticker analysis at `/research`. Enter any ticker (not just watchlist members) to run a full pipeline assessment: analyst ratings, news/sentiment, earnings, options flow, signal stacking, and recommendation. Results are persisted to `research_results` table with JSON-embedded options data and suggested contracts.

Layout: ticker input + "Analyze" button at top, 3:2 grid-to-detail layout below. Research grid shows cards with ticker, action badge, conviction bar, price, timestamp, and delete button on hover. Click a card to open the detail panel (same visual patterns as TickerDetail: price/targets, conviction bar, signal bullets, entry/exit, options flow, suggested options, trend chart, strike recommender). Analysis takes ~30-60s per ticker (runs all pipeline phases for a single ticker).

## Positions Page

Portfolio tracking at `/positions`. Informational only — no broker integration. Supports CALL, PUT, and STOCK position types. Summary bar shows open count, total unrealized P&L, win/loss counts. Open/Closed tabs filter positions. 3:2 grid layout: position cards (left) with P&L-colored borders, detail panel (right) with recommendation overlay for watchlist tickers or "Run Analysis" button for non-watchlist tickers. Inline AddPositionForm with conditional fields (strike/premium/expiry for options). Dashboard integration: TickerDetail's "Open Position" button navigates to `/positions?open=true&ticker=X&...` with pre-filled form data. P&L: STOCK = (current - entry) * qty; CALL/PUT = (current - premium) * qty * 100.

## Knowledge Page

Documentation page at `/knowledge`. Tabs: Trading Guide, Signals, Classification, Watchlist, Strikes, Pipeline. Trading Guide includes daily workflow, signal quality for weeklies, 8 scenario playbooks, risk management rules, feature usage tips, and quick decision matrix.

## Design System

Dark theme. Font: Inter. Background: `#0f1117` (page), `#161b22` (cards), `#21262d` (borders). Text: `#e6edf3` (primary), `#8b949e` (secondary).

Action colors: STRONG_BUY `#2ea043`, BUY `#56d364`, HOLD `#d29922`, SELL `#f85149`, STRONG_SELL `#da3633`. New entrant: `#58a6ff`. Manual badge: purple.

## Maintaining this file

Update this file when routes are added/removed, the provider hierarchy changes, dashboard sections are restructured, a new page is added, design tokens shift, or `localStorage` keys / axios interceptor behavior changes. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
