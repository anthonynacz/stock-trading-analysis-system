"""Provider-agnostic JWT auth middleware.

Verifies bearer tokens against either:
  - JWKS endpoint (RS256/ES256 — Supabase, Clerk, Auth0, any OIDC IdP), or
  - shared HS256 secret (dev / simple flows).

The `get_current_user` FastAPI dependency:
  - returns the legacy admin user when LEGACY_MODE=True (default during retrofit),
  - otherwise validates the Authorization: Bearer <jwt> header,
  - upserts a User row keyed by (provider, provider_user_id), then returns it.

Provider is taken from the JWT's `iss` claim — same token from Supabase vs
Clerk results in two different User rows even if email matches, which is the
correct multi-tenancy behavior. Email-based linking only happens when an
existing legacy/manual user has no provider_user_id yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.connection import get_db
from db.models import User

logger = logging.getLogger(__name__)

# auto_error=False so we can raise AuthError ourselves with a consistent shape.
_security = HTTPBearer(auto_error=False)


class AuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Token verification ──────────────────────────────────────────────────────

def _verify_jwt(token: str) -> dict:
    """Decode + verify a JWT; return its claims. Raises AuthError on failure."""
    decode_options = {
        "verify_aud": bool(settings.JWT_AUDIENCE),
    }
    audience = settings.JWT_AUDIENCE or None
    issuer = settings.JWT_ISSUER or None
    algorithms = settings.jwt_algorithms_list

    try:
        if settings.JWT_JWKS_URL:
            jwks_client = jwt.PyJWKClient(settings.JWT_JWKS_URL)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=[a for a in algorithms if a != "HS256"] or ["RS256"],
                audience=audience,
                issuer=issuer,
                options=decode_options,
            )
        if settings.JWT_HS256_SECRET:
            return jwt.decode(
                token,
                settings.JWT_HS256_SECRET,
                algorithms=["HS256"],
                audience=audience,
                issuer=issuer,
                options=decode_options,
            )
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except jwt.InvalidAudienceError:
        raise AuthError("Invalid audience")
    except jwt.InvalidIssuerError:
        raise AuthError("Invalid issuer")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")

    raise AuthError(
        "Auth not configured: set JWT_JWKS_URL or JWT_HS256_SECRET, "
        "or enable LEGACY_MODE for retrofit"
    )


# ── User resolution ─────────────────────────────────────────────────────────

async def _legacy_user(db: AsyncSession) -> User:
    """Resolve the legacy admin user (created by init_db). Defensive recreate
    if missing so a misconfigured deploy still serves requests."""
    result = await db.execute(
        select(User).where(User.email == settings.LEGACY_USER_EMAIL)
    )
    user = result.scalars().first()
    if user is None:
        logger.warning(
            "Legacy user %s missing — auto-creating", settings.LEGACY_USER_EMAIL
        )
        user = User(
            email=settings.LEGACY_USER_EMAIL,
            provider="legacy",
            role="ADMIN",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def _user_from_claims(db: AsyncSession, claims: dict) -> User:
    """Resolve or upsert a User row from validated JWT claims."""
    sub = claims.get("sub")
    email = claims.get("email")
    provider = claims.get("iss") or "unknown"

    if not sub:
        raise AuthError("JWT missing 'sub' claim")

    # 1) Try by (provider, sub) — primary key for federated identities
    result = await db.execute(
        select(User).where(
            User.provider == provider,
            User.provider_user_id == sub,
        )
    )
    user = result.scalars().first()

    # 2) Else, try linking an existing email-only user (e.g. legacy admin
    #    upgrading to real auth). Only safe when provider_user_id is NULL.
    if user is None and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalars().first()
        if user is not None and user.provider_user_id is None:
            user.provider = provider
            user.provider_user_id = sub
            logger.info(
                "Linked existing user %s to provider=%s sub=%s", email, provider, sub
            )

    # 3) New user — create + seed FREE subscription
    is_new = False
    if user is None:
        if not email:
            raise AuthError("JWT missing 'email' claim and no user found by sub")
        user = User(
            email=email,
            provider=provider,
            provider_user_id=sub,
            role="USER",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        is_new = True
        logger.info("Created user %s (provider=%s)", email, provider)

    if not user.is_active:
        raise AuthError("User account is disabled")

    # Idempotent — only inserts when no row exists. Runs on every
    # authenticated request to self-heal users who pre-date the entitlements
    # retrofit (cheap: one SELECT when the row already exists).
    from services.entitlements import ensure_subscription
    from services.preferences import ensure_preferences
    await ensure_subscription(db, user)
    await ensure_preferences(db, user)

    user.last_seen_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(user)
    _ = is_new  # kept for future telemetry (welcome email, etc.)
    return user


# ── FastAPI dependencies ────────────────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Returns the authenticated user. Raises 401 if auth fails.

    Resolution order:
      1. If a Bearer token is present and valid, use it. This lets a real
         user "log in" alongside an active LEGACY_MODE — useful for testing
         non-legacy flows without taking the whole app off legacy.
      2. Otherwise, in LEGACY_MODE, return the legacy admin user (auth bypass).
      3. Otherwise, raise 401.
    """
    if credentials is not None and credentials.credentials:
        claims = _verify_jwt(credentials.credentials)
        return await _user_from_claims(db, claims)

    if settings.LEGACY_MODE:
        return await _legacy_user(db)

    raise AuthError("Missing Authorization header")


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Soft variant: returns None on no/invalid token. Use for endpoints that
    should still respond to anonymous callers but personalize when a token
    is present."""
    if settings.LEGACY_MODE:
        return await _legacy_user(db)
    if credentials is None or not credentials.credentials:
        return None
    try:
        claims = _verify_jwt(credentials.credentials)
        return await _user_from_claims(db, claims)
    except HTTPException:
        return None
