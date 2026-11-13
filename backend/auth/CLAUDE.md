# Auth, Multi-tenancy & Entitlements

## Multi-tenancy & Auth

**`LEGACY_MODE` (default `True`) means the site is open.** Every request resolves to the legacy admin user (`role=ADMIN`, email from `LEGACY_USER_EMAIL`) with no login. Fine for local development; on a public server set it to `false`.

**Site login (`backend/auth/site_login.py`), the way to close the site.** One owner account whose username and password live only in the server's `.env`:
```
LEGACY_MODE=false
SITE_USERNAME=<name>
SITE_PASSWORD=<long password>
JWT_HS256_SECRET=<48+ random characters>   # python -c "import secrets; print(secrets.token_urlsafe(48))"
DEV_TOKEN_ENABLED=false
```
- `POST /api/auth/login {username, password}` returns a 30-day HS256 token (`sub = site-owner`). `get_current_user` maps that token to the legacy admin, so every existing row stays the owner's and no user row is created.
- **The gate:** with `LEGACY_MODE` off, the `site_gate` HTTP middleware (registered in `main.py`) answers 401 to every request without a valid token, including routes that declare no auth dependency, `/docs` and `/`. Only `/api/auth/login` is open. A new public route (a webhook) must be added to `OPEN_PATHS`.
- Failed sign-ins: 8 per client address per 15 minutes, then 429 (in memory; requests through the frontend's nginx share one address).
- Changing `JWT_HS256_SECRET` signs everyone out. Changing the password does not revoke tokens already issued.
- `docker-compose.yml` holds no auth values: they come from `.env`. Missing values leave the defaults, i.e. an open site.
- The site is served over plain HTTP, so the password and token cross the network unencrypted until TLS is added.

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

## Maintaining this file

Update this file when LEGACY_MODE or site-login behavior changes, a new JWT provider gets wired in, tier definitions/quotas change, new features are added to the gated list, or new error semantics (`QuotaExceeded` / `FeatureLocked`) appear. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
