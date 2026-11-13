"""Site login: the owner's username and password, the token it mints, and the gate that closes the site."""
from __future__ import annotations

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import middleware, site_login
from config import settings

SECRET = "unit-test-secret-of-sufficient-length-0123456789"


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(settings, "SITE_USERNAME", "owner")
    monkeypatch.setattr(settings, "SITE_PASSWORD", "correct horse")
    monkeypatch.setattr(settings, "JWT_HS256_SECRET", SECRET)
    monkeypatch.setattr(settings, "JWT_JWKS_URL", "")
    monkeypatch.setattr(settings, "JWT_ISSUER", "vela-test")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "")
    monkeypatch.setattr(settings, "LEGACY_MODE", False)
    monkeypatch.setattr(site_login, "throttle", site_login.Throttle())


def client() -> TestClient:
    from api.routes import router

    app = FastAPI()
    app.middleware("http")(site_login.site_gate)
    app.include_router(router)

    @app.get("/")
    async def root():
        return {"ok": True}

    return TestClient(app)


def login(c: TestClient, username="owner", password="correct horse"):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_not_configured_refuses(site, monkeypatch):
    monkeypatch.setattr(settings, "SITE_PASSWORD", "")
    assert not site_login.configured()
    assert not site_login.credentials_ok("owner", "")
    assert login(client(), password="").status_code == 503


def test_login_mints_a_token_the_verifier_accepts(site):
    r = login(client())
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == site_login.SESSION_DAYS * 86400
    claims = middleware._verify_jwt(body["access_token"])
    assert claims["sub"] == site_login.SITE_SUBJECT
    assert claims["iss"] == "vela-test"


@pytest.mark.parametrize("username,password", [("owner", "wrong"), ("someone", "correct horse"), ("", ""), ("Owner", "correct horse")])
def test_wrong_credentials_are_refused(site, username, password):
    r = login(client(), username, password)
    assert r.status_code == 401
    assert "access_token" not in r.text


def test_repeated_failures_are_throttled_then_a_good_password_is_still_refused(site, monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("api.routes.asyncio.sleep", no_sleep)
    c = client()
    for _ in range(site_login.MAX_FAILURES):
        assert login(c, password="nope").status_code == 401
    assert login(c, password="nope").status_code == 429
    assert login(c).status_code == 429


def test_throttle_window_expires():
    t = site_login.Throttle(limit=2, window_s=10)
    t.fail("a", now=0)
    t.fail("a", now=1)
    assert t.blocked("a", now=5)
    assert not t.blocked("b", now=5)
    assert not t.blocked("a", now=12)
    t.fail("a", now=13)
    t.clear("a")
    assert not t.blocked("a", now=13)


def test_gate_closes_everything_but_the_login(site):
    c = client()
    assert c.get("/").status_code == 401
    assert c.get("/api/radar").status_code == 401
    assert c.get("/api/watchlist").status_code == 401
    assert c.post("/api/dev/token", json={"email": "x@example.com"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Basic abc"}).status_code == 401
    token = login(c).json()["access_token"]
    assert c.get("/", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_gate_refuses_expired_and_foreign_tokens(site):
    from datetime import datetime, timedelta, timezone

    c = client()
    old, _ = site_login.mint_site_token(datetime.now(tz=timezone.utc) - timedelta(days=site_login.SESSION_DAYS + 1))
    assert c.get("/", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    forged = jwt.encode({"sub": site_login.SITE_SUBJECT, "iss": "vela-test"}, "another-secret-of-sufficient-length-0123456789", algorithm="HS256")
    assert c.get("/", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_gate_is_off_in_legacy_mode(site, monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_MODE", True)
    assert client().get("/").status_code == 200


def test_site_token_resolves_to_the_legacy_admin(site, monkeypatch):
    import asyncio

    owner = object()

    async def legacy(db):
        return owner

    async def from_claims(db, claims):
        raise AssertionError("a site token must not create or link a user row")

    monkeypatch.setattr(middleware, "_legacy_user", legacy)
    monkeypatch.setattr(middleware, "_user_from_claims", from_claims)
    token, _ = site_login.mint_site_token()
    assert asyncio.run(middleware._user_from_token(None, token)) is owner
