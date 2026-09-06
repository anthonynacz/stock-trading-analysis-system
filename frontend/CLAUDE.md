# Frontend

React 18 + TypeScript, Vite, Tailwind CSS. Entrypoint: `src/main.tsx` → `src/App.tsx`.

```
src/
├── App.tsx                     # BrowserRouter; providers; pathless ProtectedLayout route + lazy pages
├── contexts/
│   ├── AuthContext.tsx         # Fetches /api/me, holds token in localStorage, listens for vela:unauthorized
│   ├── EntitlementsContext.tsx # Fetches /api/me/entitlements, exposes tier/credits/isAdmin
│   └── ResearchContext.tsx, OptionsLabContext.tsx
├── pages/                      # LoginPage, Dashboard, ResearchPage, PositionsPage, KnowledgePage, ...
│   └── knowledge/              # One lazy file per Knowledge tab + shared.tsx (content constants)
├── components/                 # AppNav, RequireAuth, TickerDetail, RecommendationCard, StrikeRecommender, ...
│   └── ui/                     # feedback (Spinner/LoadingRow/ErrorBox/EmptyCard/PageSpinner), badges,
│                               #   ConvictionBar, SegmentedControl, TabBar, LockedBadge
├── hooks/
│   ├── useEdgeFlow.ts          # Data fetching hooks (foreground vs background refetch)
│   └── usePolling.ts           # Visibility-aware interval used by every poller
├── types/index.ts              # TypeScript interfaces
└── utils/
    ├── api.ts                  # Axios client; request/response interceptors; getApiErrorMessage/isQuotaError
    ├── format.ts               # fmtNum/fmtPrice/fmtStrike/fmtSigned/fmtPct/fmtMarketCap/fmtDelta/...
    ├── options.ts              # RISK_LEVELS, isHeavyTheta
    ├── theme.ts                # BRAND, PALETTE, ACTION_COLORS/ACTION_TEXT_CLASS and other colour maps
    └── recommendation.ts       # classifyConviction, detectDemotion
```

## Common Commands

```bash
cd frontend && npm install && npm run dev   # local dev server (proxies /api to localhost:8000)
cd frontend && npm run dev:prodapi          # proxy /api to the Hetzner backend via .env.prodapi (VITE_API_PROXY)
cd frontend && npx tsc --noEmit             # type check
cd frontend && npm run build                # tsc + vite build (what the Dockerfile runs)
```

## Auth + Provider Hierarchy

`App.tsx` wraps every route in `AuthProvider` → `EntitlementsProvider` → (other context providers). Protected pages sit under one pathless `ProtectedLayout` route (`<RequireAuth>` + `<AppNav>` mounted once, `<Suspense>` + `<Outlet>`); only `Dashboard` and `LoginPage` are imported eagerly — every other page is `React.lazy` and must keep its default export. `RequireAuth` redirects to `/login` (with `state.from`) when `user` is null (won't happen in LEGACY_MODE; the legacy admin always resolves) and shows a retry panel when `/api/me` fails for a non-401 reason (API outage). `/login` is the only public route.

- `AuthContext` — fetches `/api/me` on mount + on token change; persists token in `localStorage["vela.access_token"]`. Axios `request` interceptor in `utils/api.ts` attaches `Authorization: Bearer <token>` automatically (no-op when no token). The `response` interceptor clears the stored token on any 401 and dispatches a `vela:unauthorized` window event (`UNAUTHORIZED_EVENT`); `AuthContext` listens and nulls `user`, so `RequireAuth` bounces to `/login`.
- `getApiErrorMessage(err, fallback)` / `isQuotaError(err)` in `utils/api.ts` are the only sanctioned error extractors — FastAPI `detail` may be an object (402 quota / feature-lock payloads), never render it raw.
- `EntitlementsContext` — fetches `/api/me/entitlements`; exposes `data / loading / isAdmin / isUnlimited(n) / refresh` (no `error` — a failed fetch is non-fatal). `fmtCount(n, sentinel)` renders `∞` when `n >= sentinel`.
- `LoginPage` — email-only form that calls `POST /api/dev/token`; navigates back to `location.state.from` after login. **Real auth provider integration is deferred** — when adopted (Supabase / Clerk / Auth0), replace this page's body but keep the `setToken` → `refresh()` → navigate flow intact.
- `AppNav` — top nav rebranded **Vela**. Right side: 🔬 credit balance widget (research credits this period / monthly quota), tier chip (FREE/STARTER/PRO/PREMIUM/ADMIN, color-coded), `LEGACY` chip when backend is in legacy mode, user initials avatar, sign-out (only when an explicit token is held).

## Data Hooks + Polling

`hooks/useEdgeFlow.ts` hooks return `{ data, loading, error, refetch }`. `loading` means "no data yet": interval ticks call `refetch({ background: true })`, which never flips `loading`, so sections don't unmount every minute (expanded cards, filters and in-progress forms survive). Page gates must read `x.loading && !x.data`. When a hook's params change (new fetcher identity) `data`/`error` are reset to `null` before the foreground fetch, so the same gate also covers param-change fetches (date step, tab switch, sort toggle). Stale responses are dropped via a per-hook sequence counter (also bumped on unmount). All intervals go through `hooks/usePolling.ts` (`usePolling(refetch, ms | null)`): ticks skip while `document.hidden`, a single catch-up fires on return if a full interval elapsed, and `null` disables polling (historical dates). Job-status polls (`useRotationStatus`, PipelineRunner, Scanner) stay on their own short-lived intervals.

## Dashboard Layout

The app uses react-router-dom with routes: `/` (Dashboard), `/universe` (Universe), `/research` (Research), `/options-lab` (Options Lab), `/scanner` (Scanner), `/industries` (Industries), `/charts` (Charts), `/positions` (Positions), `/performance` (Performance), `/schedule` (Schedule), `/settings` (Settings), `/knowledge` (Knowledge), `/login` (public). A top nav bar (`AppNav`) links all protected pages. Nginx SPA fallback (`try_files $uri $uri/ /index.html`) handles client-side routing.

The Dashboard has the following sections:
1. **StatusBar** — Date stepper (left/right arrows to navigate pipeline days), refresh button, system status
2. **WatchlistChanges** — Horizontal bar showing NEW_ENTRANT (green) and REMOVED (red) badges for the selected date
2a. **Industries** — Compact `IndustryCard` grid (`compact` prop hides the detail rows) linking to `/industries?industry=`
3. **Watchlist + TickerDetail** — 3:2 column layout. Left: sector-grouped stock cards with status/manual/lock badges and add-ticker input. Right: detail panel on click with trend charts (price/conviction + IV/sentiment with configurable SMA), price, signals, options flow (sentiment-rated), and strike recommender.
4. **Recommendations** — Expandable cards with signal bullet lists, price/risk row, entry/exit strategy. Header has a `Top conviction | Recently revised` segmented toggle (persisted to `localStorage["vela.rec_sort"]`) that switches the API's `sort=` param. `RecommendationCard` shows a `REV · Xh ago` chip on rows revised within the last 4h, with hover tooltip surfacing `revision_reason` (e.g. the intraday news headline that triggered the rescore). `StatusBar` shows "News Xm ago" pulled from `last_refresh.intraday_news`.
5. **Strike Scanner** — Budget slider, "Scan Watchlist" button, "Save Snapshot" button. Historical dates auto-load the saved snapshot for that date; today shows the live scan. Results in 2-column grid with risk level tabs.
6. **News + Catalysts** — 3:2 column layout. News timeline with mode selector (All/Watchlist/Ticker) and upcoming earnings calendar with countdown (days/hours). Ticker and Watchlist modes show relevance-scored ticker badges on each article.

## Key UI Features

- **Manual watchlist**: Input field in watchlist header to add tickers. Manual entries show an accent (emerald) MANUAL badge and X remove button on hover.
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

## Performance Page

Recommendation outcome analytics at `/performance` (backed by `GET /api/outcomes/summary`, data from the nightly outcome_scoring job). Window selector (30/90/180d). Sections: overall hit-rate tiles per horizon (T+1/5/20), by-action table, and a per-signal hit-rate table (sortable by hit rate / avg adjusted return / sample size) meant to inform Signal Weights tuning in Settings. Empty state explains the 18:00 ET scoring job. The "Open Position" deep-link from TickerDetail now carries `rec=<id>` which PositionsPage forwards as `recommendation_id` on create (dropped if the user changes the ticker in the form).

## Knowledge Page

Documentation page at `/knowledge`. Tabs (`?tab=` keys, deep-linkable): guide, signals, classification, watchlist, strikes, optionslab, strategies, pipeline — one lazy file each under `pages/knowledge/`, rendered by `KnowledgePage.tsx` via a `TAB_COMPONENTS` map. Strategies hosts the interactive expiration-payoff SVG. Content constants (`WATCHLIST_LIMITS`, `IV_BUCKETS`, `DTE_BUCKETS` in `pages/knowledge/shared.tsx`, signal points in `SignalsTab.tsx`) mirror `backend/services/CLAUDE.md` — update both when limits, thresholds, or signal points change. Trading Guide includes daily workflow, signal quality for weeklies, 8 scenario playbooks, risk management rules, feature usage tips, and quick decision matrix.

## Settings Page

`/settings` — left nav with seven panes: Profile, Risk Profile, Signal Weights, Industry Weights, Alerts, Custom Universe, AM Digest (`SECTIONS` in `SettingsPage.tsx`); a draft of `/api/me/preferences` is edited locally and saved via `usePreferences`. Discord delivery requires `alerts_config.channel === "discord"` — the Alerts pane sets it via the Delivery `SegmentedControl` (Discord option is Premium/Admin only) and the webhook input, which also flips `channel` to `discord` when a URL is entered (back to `email` when cleared).

## Design System

Dark theme. Font: Inter. Background: `#0f1117` (page), `#161b22` (cards), `#21262d` (borders). Text: `#e6edf3` (primary), `#8b949e` (secondary). Brand string comes from `BRAND` in `utils/theme.ts` — never hardcode "Vela".

Action colors: STRONG_BUY `#2ea043`, BUY `#56d364`, HOLD `#d29922`, SELL `#f85149`, STRONG_SELL `#da3633` — only ever via `ACTION_COLORS` / `ACTION_TEXT_CLASS` from `utils/theme.ts`. New entrant: `#58a6ff`. Manual badge: accent (emerald).

- **Buttons / panels**: `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger` and `.detail-panel` are `@layer components` classes in `index.css`; add per-site layout modifiers (`w-full`, `whitespace-nowrap`) alongside them instead of inventing new colour treatments.
- **Focus**: a base `:focus-visible` accent ring is applied globally in `index.css` — never add `focus:outline-none`.
- **Feedback states**: use `components/ui/feedback.tsx` (`Spinner`, `LoadingRow`, `ErrorBox`, `EmptyCard`, `PageSpinner`) rather than inline spinner/error/empty markup; sizes are literal class maps (Tailwind purge).
- **Formatters**: all number rendering goes through `utils/format.ts`. `fmtSigned` renders zero as `+0`; `fmtPct(v)` expects a percent and `fmtPct(v, d, { fraction: true })` a ratio (Scanner/Performance data are fractions, Options Lab percents); `fmtStrike` drops cents on whole-dollar strikes.

## Build / Deploy

`vite.config.ts` splits `node_modules` into stable `vendor-react` / `vendor-router` / `vendor-axios` / `vendor-recharts` chunks (function-form `manualChunks`) so app-only deploys don't invalidate library bytes; pages and Knowledge tabs are their own lazy chunks. `nginx.conf` gzips text assets, serves `/assets/` with `Cache-Control: immutable` (hashed filenames), and `no-cache` for `index.html`; `/api` proxies to the backend with 300s read timeouts for slow research calls.

## Maintaining this file

Update this file when routes are added/removed, the provider hierarchy changes, dashboard sections are restructured, a new page or Knowledge tab is added, design tokens or shared `ui/` primitives / formatter contracts change, or `localStorage` keys / axios interceptor / polling behavior changes. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
