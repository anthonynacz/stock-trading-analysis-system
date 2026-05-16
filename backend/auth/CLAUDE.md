# Auth, Multi-tenancy & Entitlements

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

## Maintaining this file

Update this file when LEGACY_MODE behavior changes, a new JWT provider gets wired in, tier definitions/quotas change, new features are added to the gated list, or new error semantics (`QuotaExceeded` / `FeatureLocked`) appear. Full rules in the root `CLAUDE.md` § Maintaining these files. Surface what you changed in your reply.
