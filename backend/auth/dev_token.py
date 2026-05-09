"""Mint short-lived HS256 JWTs for local testing.

Only enabled when DEV_TOKEN_ENABLED=true AND JWT_HS256_SECRET is set. Never
leave DEV_TOKEN_ENABLED on in any environment that's reachable from the
public internet — anyone could mint a token for any email.

The minted token uses the same audience/issuer the verifier expects, so it
exercises the real validation path (just with HS256 instead of RS256).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config import settings


def mint_dev_token(email: str, sub: Optional[str] = None, ttl_minutes: int = 60) -> str:
    if not settings.JWT_HS256_SECRET:
        raise RuntimeError("JWT_HS256_SECRET not configured — cannot mint dev token")
    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "sub": sub or f"dev:{email}",
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "iss": settings.JWT_ISSUER or "dev",
    }
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_HS256_SECRET, algorithm="HS256")
