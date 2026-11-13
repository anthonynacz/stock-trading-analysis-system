"""Site login: one owner account (username + password) kept in the server's `.env`.

`POST /api/auth/login` checks SITE_USERNAME / SITE_PASSWORD and answers with an HS256 token signed
with JWT_HS256_SECRET. That token resolves to the legacy admin (the owner of every row), so signing in
changes who may look, not whose data it is. With LEGACY_MODE off, `site_gate` refuses every request
that carries no valid token, whether or not the route declares an auth dependency.

Nothing here is stored in the database and nothing secret is ever logged.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings

# `sub` of a site-login token; `get_current_user` maps it to the legacy admin.
SITE_SUBJECT = "site-owner"
SESSION_DAYS = 30
# Reachable without a token while the gate is on.
OPEN_PATHS = frozenset({"/api/auth/login"})

# Failed sign-ins allowed per client address inside the window, before 429.
MAX_FAILURES = 8
FAILURE_WINDOW_S = 15 * 60


def configured() -> bool:
    return bool(settings.SITE_USERNAME and settings.SITE_PASSWORD and settings.JWT_HS256_SECRET)


def credentials_ok(username: str, password: str) -> bool:
    """Constant-time on both fields, so the answer time says nothing about which one was wrong."""
    if not configured():
        return False
    user_ok = hmac.compare_digest(username.encode(), settings.SITE_USERNAME.encode())
    pass_ok = hmac.compare_digest(password.encode(), settings.SITE_PASSWORD.encode())
    return user_ok and pass_ok


def mint_site_token(now: datetime | None = None) -> tuple[str, int]:
    """(token, lifetime in seconds)."""
    now = now or datetime.now(tz=timezone.utc)
    ttl = timedelta(days=SESSION_DAYS)
    payload: dict = {
        "sub": SITE_SUBJECT,
        "email": settings.LEGACY_USER_EMAIL,
        "iat": now,
        "exp": now + ttl,
        "iss": settings.JWT_ISSUER or "vela",
    }
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_HS256_SECRET, algorithm="HS256"), int(ttl.total_seconds())


class Throttle:
    """Failed sign-ins per client address, in memory (the backend is a single process)."""

    def __init__(self, limit: int = MAX_FAILURES, window_s: float = FAILURE_WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            self._failures.pop(key, None)
        return q

    def blocked(self, key: str, now: float | None = None) -> bool:
        return len(self._recent(key, time.monotonic() if now is None else now)) >= self.limit

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._recent(key, now)
        self._failures[key].append(now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


throttle = Throttle()


async def site_gate(request: Request, call_next):
    """HTTP middleware: with LEGACY_MODE off, nothing is served without a valid bearer token."""
    if settings.LEGACY_MODE or request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
        return await call_next(request)

    from auth.middleware import AuthError, _verify_jwt

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("Sign in required")
        _verify_jwt(token.strip())
    except AuthError as e:
        return JSONResponse({"detail": e.detail}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)
