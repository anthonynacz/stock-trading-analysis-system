"""Momentum Radar API routes and the `radar_entry` alert (PORT_SPEC section 7).

Routes are exercised three ways: called directly with a fake RadarStore (the
logic), against the real RadarStore on SQLite (the contract with radar/store.py),
and through FastAPI's TestClient with dependency overrides (status codes and
query validation). The alert dispatch runs its real SQL against an in-memory
SQLite database through a thin async-over-sync session, so the dedup, tier
gating and opt-in filters are checked end to end without Postgres.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

import config
import radar
import radar.config
from api import routes
from db.models import (
    AlertLog,
    Base,
    Position,
    RadarBackupManifest,
    RadarHousekeepingRun,
    RadarTableMetric,
    Subscription,
    User,
    UserPreferences,
    Watchlist,
)
from services import alerts, preferences

UTC = timezone.utc


def _z(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# 2026-10-07 is a Wednesday in EDT: regular session 13:30Z-20:00Z, first scan due 13:35Z.
DAY = "2026-10-07"


def _state(tick: datetime, *, generated: datetime | None = None, session_date: str = DAY,
           open_: str = "2026-10-07T13:30:00Z", close: str = "2026-10-07T20:00:00Z",
           status: str = "ok", last_bar: str | None = None) -> dict:
    return {
        "schema": 1, "generated_at": _iso(generated or tick + timedelta(seconds=55)), "tick_id": _iso(tick),
        "last_bar": last_bar, "status": status, "message": "",
        "session": {"date": session_date, "phase": "regular", "open": open_, "close": close, "half_day": False},
        "next_tick_at": None, "params_version": "radar-sm-1", "members": [], "heating": [],
        "recent_exits": [], "sector_banners": [], "disclaimer": "Educational analysis, not financial advice.",
    }


class FakeStore:
    """The pinned RadarStore reads (PORT_SPEC section 4), recording how they were called."""

    def __init__(self, state: dict | None = None, events: list[dict] | None = None,
                 ticks: list[dict] | None = None, scan_log: list[dict] | None = None) -> None:
        self.state = state or {}
        self.events = events or []
        self.ticks = ticks or []
        self.scan_log = scan_log or []
        self.calls: list[tuple] = []

    def load_state(self) -> dict:
        self.calls.append(("load_state",))
        return self.state

    def recent_events(self, *, since=None, ticker=None, limit=500) -> list[dict]:
        self.calls.append(("recent_events", since, ticker, limit))
        return list(self.events)

    def member_ticks(self, ticker: str, session_date: str) -> list[dict]:
        self.calls.append(("member_ticks", ticker, session_date))
        return list(self.ticks)

    def scan_log_tail(self, n: int = 50) -> list[dict]:
        self.calls.append(("scan_log_tail", n))
        return list(self.scan_log)


ADMIN = SimpleNamespace(id=1, role="ADMIN", email="admin@example.com")
MEMBER = SimpleNamespace(id=2, role="USER", email="user@example.com")


@pytest.fixture(autouse=True)
def _idle_housekeeping_state():
    """The manual-run state is module-level; every test starts and ends idle."""
    def reset() -> None:
        routes._radar_hk_state.clear()
        routes._radar_hk_state.update(running=False, run_id=None, mode=None, started_at=None,
                                      finished_at=None, status=None, error=None)
    reset()
    yield
    reset()


# ── Async-over-sync session on SQLite ──────────────────────────────────────

class AsyncOverSync:
    """Just enough of AsyncSession over a sync Session: the code under test
    awaits the same calls and its SQL runs for real on SQLite."""

    def __init__(self, session: Session) -> None:
        self.s = session

    async def execute(self, stmt, *args, **kwargs):
        return self.s.execute(stmt, *args, **kwargs)

    async def get(self, entity, ident):
        return self.s.get(entity, ident)

    def add(self, obj) -> None:
        self.s.add(obj)

    async def flush(self) -> None:
        self.s.flush()

    async def commit(self) -> None:
        self.s.commit()

    async def rollback(self) -> None:
        self.s.rollback()


@pytest.fixture
def sync_session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        User.__table__, Subscription.__table__, UserPreferences.__table__, AlertLog.__table__,
        Position.__table__, Watchlist.__table__,
    ])
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


# ── Preferences: the radar_entry alert key ─────────────────────────────────

def test_radar_entry_alert_key_is_catalogued_validated_and_off_by_default():
    keys = {a["key"]: a for a in preferences.ALERT_KEYS}
    entry = keys["radar_entry"]
    assert entry["label"] == "Momentum Radar entry"
    assert entry["description"] == "A stock enters the 5-minute Momentum Radar (racing up or down)"
    assert entry["tier_min"] == keys["news_spike"]["tier_min"]
    assert alerts._ALERT_TIER_MIN["radar_entry"] == keys["news_spike"]["tier_min"]

    assert preferences.DEFAULT_PREFS()["alerts_config"]["radar_entry"] is False
    assert preferences._validate_alerts({"radar_entry": 1})["radar_entry"] is True
    assert preferences._validate_alerts({})["radar_entry"] is False
    assert any(a["key"] == "radar_entry" for a in preferences.get_catalog()["alert_keys"])


def test_serialize_backfills_radar_entry_for_rows_saved_before_the_key_existed():
    pref = SimpleNamespace(risk_profile="moderate", signal_group_weights={}, industry_weights={},
                           custom_universe=[], alerts_config={"news_spike": True}, digest_config={},
                           updated_at=None)
    cfg = preferences.serialize_preferences(pref)["alerts_config"]
    assert cfg["radar_entry"] is False and cfg["news_spike"] is True


# ── Alerts: Discord line ───────────────────────────────────────────────────

def test_radar_entry_discord_line_has_arrow_detail_intensity_and_link():
    event = {"id": "20261007T1415Z-NVDA-ENTER-1", "type": "ENTER", "ticker": "NVDA", "dir": "up",
             "intensity": 80.6, "detail": "+2.9σ vs market in 15 min; volume 3.4× normal for this time",
             "price": 181.2, "ts": "2026-10-07T14:15:00Z", "late": False}
    payload = alerts._radar_entry_payload(event, "http://vela.test/radar")
    assert payload["intensity"] == 81 and payload["direction"] == "up"
    assert alerts._format_alert("radar_entry", "NVDA", payload) == (
        "📡 **NVDA** racing ↑ — +2.9σ vs market in 15 min; volume 3.4× normal for this time (intensity 81)"
        "\nhttp://vela.test/radar"
    )
    down = alerts._radar_entry_payload({**event, "dir": "down", "detail": "", "intensity": None}, None)
    assert alerts._format_alert("radar_entry", "NVDA", down) == "📡 **NVDA** racing ↓"


# ── Alerts: dispatch_radar_entries ─────────────────────────────────────────

def _user(s: Session, uid: int, *, role: str = "USER", tier: str | None = None, active: bool = True,
          cfg: dict | None = None) -> None:
    s.add(User(id=uid, email=f"u{uid}@example.com", role=role, is_active=active))
    if tier:
        s.add(Subscription(user_id=uid, tier=tier, status="ACTIVE"))
    if cfg is not None:
        s.add(UserPreferences(user_id=uid, risk_profile="moderate", alerts_config=cfg))


ENTER_A = {"id": "20261007T1415Z-NVDA-ENTER-1", "type": "ENTER", "ticker": "NVDA", "dir": "up",
           "intensity": 81.0, "detail": "+2.9σ vs market in 15 min", "price": 181.2,
           "ts": "2026-10-07T14:15:00Z"}
ENTER_B = {"id": "20261007T1415Z-XOM-ENTER-1", "type": "ENTER", "ticker": "XOM", "dir": "down",
           "intensity": 64.2, "detail": "-3.1σ vs market in 30 min", "price": 108.4,
           "ts": "2026-10-07T14:15:00Z"}
EXIT_C = {"id": "20261007T1415Z-AMD-EXIT-1", "type": "EXIT", "ticker": "AMD", "dir": "up",
          "intensity": 40.0, "detail": "Momentum faded", "ts": "2026-10-07T14:15:00Z"}


@pytest.fixture
def opted_in_users(sync_session: Session) -> Session:
    s = sync_session
    hook = "https://discord.test/hook"
    _user(s, 1, role="ADMIN", cfg={"radar_entry": True, "channel": "discord", "discord_webhook_url": hook})
    _user(s, 2, tier="PRO", cfg={"radar_entry": True})                    # email log stub
    _user(s, 3, tier="STARTER", cfg={"radar_entry": True})                # alerts on, tier below PRO
    _user(s, 4, cfg={"radar_entry": True})                                # FREE: alerts locked
    _user(s, 5, tier="PRO", cfg={"radar_entry": False, "news_spike": True})  # opted out
    _user(s, 6, tier="PRO", active=False, cfg={"radar_entry": True})      # disabled account
    _user(s, 7, tier="PRO")                                               # no preferences row
    s.commit()
    return s


def _alert_keys(s: Session) -> set[tuple[int, str]]:
    return {(r.user_id, r.key) for r in s.execute(select(AlertLog)).scalars()}


@pytest.mark.asyncio
async def test_dispatch_sends_enter_events_to_opted_in_entitled_users_only(opted_in_users, monkeypatch):
    s = opted_in_users
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "send_discord", send)
    monkeypatch.setattr(config.settings, "PUBLIC_APP_URL", "http://vela.test/")

    fired = await alerts.dispatch_radar_entries([ENTER_A, ENTER_B, EXIT_C], session=AsyncOverSync(s))

    assert fired == 4   # users 1 and 2 x two ENTER events; the EXIT is never alerted
    assert _alert_keys(s) == {(uid, f"radar_entry:{e['id']}") for uid in (1, 2) for e in (ENTER_A, ENTER_B)}
    assert send.await_count == 2   # only user 1 has a Discord webhook
    hook, line = send.await_args_list[0].args
    assert hook == "https://discord.test/hook"
    assert line == "📡 **NVDA** racing ↑ — +2.9σ vs market in 15 min (intensity 81)\nhttp://vela.test/radar"
    assert "racing ↓" in send.await_args_list[1].args[1]
    row = s.execute(select(AlertLog).where(AlertLog.user_id == 2)).scalars().first()
    assert row.alert_type == "radar_entry" and row.delivery_channel == "email_log_stub"
    assert row.payload["link"] == "http://vela.test/radar"


@pytest.mark.asyncio
async def test_dispatch_dedups_on_event_id(opted_in_users, monkeypatch):
    s = opted_in_users
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "send_discord", send)
    monkeypatch.setattr(config.settings, "PUBLIC_APP_URL", "")

    assert await alerts.dispatch_radar_entries([ENTER_A], session=AsyncOverSync(s)) == 2
    # A worker restart re-sends the same batch plus one new entry: only the new one goes out.
    assert await alerts.dispatch_radar_entries([ENTER_A, ENTER_B], session=AsyncOverSync(s)) == 2
    assert send.await_count == 2
    assert len(_alert_keys(s)) == 4
    assert "\n" not in send.await_args_list[0].args[1]   # no PUBLIC_APP_URL, no link line


@pytest.mark.asyncio
async def test_dispatch_failure_on_one_alert_does_not_stop_the_rest(opted_in_users, monkeypatch):
    s = opted_in_users
    real = alerts._dispatch_alert
    calls = {"n": 0}

    async def flaky(session, user, cfg, alert_type, ticker, key, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real(session, user, cfg, alert_type, ticker, key, payload)

    monkeypatch.setattr(alerts, "_dispatch_alert", flaky)
    monkeypatch.setattr(alerts, "send_discord", AsyncMock(return_value=True))
    fired = await alerts.dispatch_radar_entries([ENTER_A, ENTER_B], session=AsyncOverSync(s))
    assert fired == 3
    assert (1, f"radar_entry:{ENTER_A['id']}") not in _alert_keys(s)
    assert (1, f"radar_entry:{ENTER_B['id']}") in _alert_keys(s)


@pytest.mark.asyncio
async def test_dispatch_without_enter_events_touches_nothing():
    class Boom:
        async def execute(self, *a, **k):
            raise AssertionError("no DB access expected")

    assert await alerts.dispatch_radar_entries([], session=Boom()) == 0
    assert await alerts.dispatch_radar_entries([EXIT_C, {"type": "ENTER"}], session=Boom()) == 0


def test_worker_style_calls_use_a_fresh_session_per_event_loop(opted_in_users, monkeypatch):
    """The worker calls asyncio.run(...) after every tick; each call must get its own session."""
    s = opted_in_users
    opened: list[int] = []

    @asynccontextmanager
    async def fake_session():
        opened.append(id(asyncio.get_running_loop()))
        yield AsyncOverSync(s)

    monkeypatch.setattr(alerts, "_loop_local_session", fake_session)
    monkeypatch.setattr(alerts, "send_discord", AsyncMock(return_value=True))
    assert asyncio.run(alerts.dispatch_radar_entries([ENTER_A])) == 2
    assert asyncio.run(alerts.dispatch_radar_entries([ENTER_B])) == 2
    assert len(opened) == 2


@pytest.mark.asyncio
async def test_loop_local_session_uses_an_unpooled_engine():
    async with alerts._loop_local_session() as session:
        assert isinstance(session.bind.sync_engine.pool, NullPool)
        assert session.sync_session.expire_on_commit is False


@pytest.mark.asyncio
async def test_periodic_alerts_scan_never_scans_radar_entries(sync_session):
    s = sync_session
    _user(s, 1, role="ADMIN", cfg={"radar_entry": True, "rec_change": False,
                                   "earnings_proximity": False})
    s.commit()
    counts = await alerts._scan_for_user(AsyncOverSync(s), s.get(User, 1))
    assert "radar_entry" not in counts


# ── Stale computation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("now, tick, expected", [
    (_z(2026, 10, 7, 14, 10), _z(2026, 10, 7, 14, 0), False),     # 9 min since the scan was written
    (_z(2026, 10, 7, 14, 14), _z(2026, 10, 7, 14, 0), True),      # 13 min: past stale_warning_min (12)
    (_z(2026, 10, 7, 13, 33), _z(2026, 10, 6, 20, 0), False),     # before the first scan is due
    (_z(2026, 10, 7, 13, 47), _z(2026, 10, 6, 20, 0), False),     # first scan due 13:35:50, 11 min late
    (_z(2026, 10, 7, 13, 48), _z(2026, 10, 6, 20, 0), True),      # ... 12 min late: no scan yet today
    (_z(2026, 10, 7, 20, 5), _z(2026, 10, 7, 19, 40), True),      # final tick still due (close + 50 s + grace)
    (_z(2026, 10, 7, 20, 6), _z(2026, 10, 7, 19, 40), False),     # after the scan window
    (_z(2026, 10, 10, 15, 0), _z(2026, 10, 9, 20, 0), False),     # Saturday
    (_z(2026, 11, 26, 15, 0), _z(2026, 11, 25, 20, 0), False),    # Thanksgiving
])
def test_radar_is_stale(now, tick, expected):
    session_date = tick.date().isoformat()
    state = _state(tick, session_date=session_date, open_=f"{session_date}T13:30:00Z",
                   close=f"{session_date}T20:00:00Z")
    assert routes.radar_is_stale(state, now) is expected


def test_stale_uses_generated_at_and_the_scanners_session_times():
    # A failed tick rewrites state.json (newer generated_at, same tick_id): the radar is not silent.
    state = _state(_z(2026, 10, 7, 14, 0), generated=_z(2026, 10, 7, 14, 6))
    assert routes.radar_is_stale(state, _z(2026, 10, 7, 14, 15)) is False
    # Half day (2026-11-27 closes 18:00Z): no scans are due at 19:00Z even with an old snapshot.
    half = _state(_z(2026, 11, 27, 17, 55), session_date="2026-11-27", open_="2026-11-27T14:30:00Z",
                  close="2026-11-27T18:00:00Z")
    assert routes.radar_is_stale(half, _z(2026, 11, 27, 19, 0)) is False
    assert routes.radar_is_stale(half, _z(2026, 11, 27, 18, 5)) is False
    # An unscheduled early close written by the scanner wins over the local calendar.
    early = _state(_z(2026, 10, 7, 16, 55), close="2026-10-07T17:00:00Z")
    assert routes.radar_is_stale(early, _z(2026, 10, 7, 17, 30)) is False


# ── Routes, called directly ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_returns_no_data_before_the_first_write():
    out = await routes.radar_snapshot(store=FakeStore(), user=MEMBER)
    assert out == {"status": "no_data", "stale": False, "message": routes._RADAR_NO_DATA_MESSAGE}
    assert "schema" not in out and "tick_id" not in out   # the frontend's isRadarSnapshot() relies on it


@pytest.mark.asyncio
async def test_snapshot_passes_the_state_through_with_stale(monkeypatch):
    state = _state(_z(2026, 10, 7, 14, 0))
    seen = {}

    def fake_stale(st, now):
        seen["now"] = now
        return True

    monkeypatch.setattr(routes, "radar_is_stale", fake_stale)
    out = await routes.radar_snapshot(store=FakeStore(state=state), user=MEMBER)
    assert out["stale"] is True
    assert {k: v for k, v in out.items() if k != "stale"} == state
    assert seen["now"].tzinfo is not None


@pytest.mark.asyncio
async def test_events_newest_first_with_window_ticker_and_limit():
    evs = [{"id": "a", "ts": "2026-10-07T14:00:00Z"}, {"id": "c", "ts": "2026-10-07T15:10:00Z"},
           {"id": "b", "ts": "2026-10-07T14:35:00Z"}]
    store = FakeStore(events=evs)
    before = datetime.now(UTC)
    out = await routes.radar_events(days=2, ticker=" nvda ", store=store, user=MEMBER)
    assert [e["id"] for e in out] == ["c", "b", "a"]
    _, since, ticker, limit = store.calls[0]
    assert ticker == "NVDA" and limit == 500
    assert timedelta(days=2) - timedelta(seconds=5) <= before - since <= timedelta(days=2) + timedelta(seconds=5)

    await routes.radar_events(days=1, ticker="", store=store, user=MEMBER)
    assert store.calls[-1][2] is None
    with pytest.raises(HTTPException) as err:
        await routes.radar_events(days=1, ticker="NV DA;", store=store, user=MEMBER)
    assert err.value.status_code == 400


@pytest.mark.asyncio
async def test_ticker_ticks_for_a_session_and_the_default_session():
    ticks = [{"tick": "2026-10-07T14:20:00Z", "ticker": "NVDA"}, {"tick": "2026-10-07T14:15:00Z", "ticker": "NVDA"}]
    store = FakeStore(ticks=ticks)
    out = await routes.radar_ticker_ticks("nvda", session="2026-10-07", store=store, user=MEMBER)
    assert out["ticker"] == "NVDA" and out["session"] == "2026-10-07"
    assert [t["tick"] for t in out["ticks"]] == ["2026-10-07T14:15:00Z", "2026-10-07T14:20:00Z"]
    assert store.calls == [("member_ticks", "NVDA", "2026-10-07")]

    # No session: the ET day of the latest processed bar (the closed heartbeat already names the next session).
    closed = {**_state(_z(2026, 10, 10, 1, 0), session_date="2026-10-12"), "last_bar": "2026-10-09T20:00:00Z"}
    store = FakeStore(state=closed, ticks=ticks)
    out = await routes.radar_ticker_ticks("NVDA", session=None, store=store, user=MEMBER)
    assert out["session"] == "2026-10-09"

    with pytest.raises(HTTPException) as err:
        await routes.radar_ticker_ticks("NVDA", session="10/07/2026", store=store, user=MEMBER)
    assert err.value.status_code == 400


def _ops_rows(s: Session) -> None:
    """Two housekeeping runs (the newest a dry-run), their metrics and three manifest partitions."""
    Base.metadata.create_all(s.get_bind(), tables=[
        RadarHousekeepingRun.__table__, RadarTableMetric.__table__, RadarBackupManifest.__table__])
    s.add_all([
        RadarHousekeepingRun(run_id="hk-1", started_at=_z(2026, 10, 6, 7, 30), trigger="schedule",
                             mode="apply", status="ok", report={"tables": {"radar_events": {"status": "OK"}}}),
        RadarHousekeepingRun(run_id="hk-2", started_at=_z(2026, 10, 7, 7, 30), trigger="manual",
                             mode="dry-run", status="ok", report={"tables": {}}),
    ])
    s.add_all([
        RadarTableMetric(run_id="hk-1", measured_at=_z(2026, 10, 6, 7, 30), table_name="radar_scan_log",
                         status="OK", reasons=[], lookup_ms={}),
        RadarTableMetric(run_id="hk-1", measured_at=_z(2026, 10, 6, 7, 30), table_name="radar_events",
                         status="WARN", reasons=["rows 82% of max_rows"], rows=164_000,
                         lookup_ms={"rows_on_day": 4.1}, lookup_max_ms=4.1),
        RadarTableMetric(run_id="hk-0", measured_at=_z(2026, 10, 5, 7, 30), table_name="radar_events",
                         status="OK", reasons=[], lookup_ms={}),
    ])
    s.add_all([
        RadarBackupManifest(table_name="radar_member_ticks", month="2026-09", path="radar_member_ticks/2026-09.jsonl.gz",
                            rows=10, writes=1),
        RadarBackupManifest(table_name="radar_events", month="2026-09", path="radar_events/2026-09.jsonl.gz",
                            rows=2, writes=1, min_ts=_z(2026, 9, 1, 13, 35)),
        RadarBackupManifest(table_name="radar_events", month="2026-08", path="radar_events/2026-08.jsonl.gz",
                            rows=1, writes=2),
    ])
    s.commit()


@pytest.mark.asyncio
async def test_health_reports_scan_log_latest_run_metrics_and_manifest(sync_session):
    s = sync_session
    _ops_rows(s)
    store = FakeStore(scan_log=[{"tick": "2026-10-07T14:00:00Z", "status": "ok"}])

    out = await routes.radar_health(db=AsyncOverSync(s), store=store, user=MEMBER)
    assert store.calls == [("scan_log_tail", 50)]
    assert out["scan_log"] == [{"tick": "2026-10-07T14:00:00Z", "status": "ok"}]
    assert out["housekeeping"]["run_id"] == "hk-2" and out["housekeeping"]["mode"] == "dry-run"
    # The latest measurement set (the dry-run recorded none), JSON columns decoded, datetimes as ISO.
    assert [(m["run_id"], m["table_name"], m["status"]) for m in out["table_metrics"]] == [
        ("hk-1", "radar_events", "WARN"), ("hk-1", "radar_scan_log", "OK")]
    assert out["table_metrics"][0]["reasons"] == ["rows 82% of max_rows"]
    assert out["table_metrics"][0]["lookup_ms"] == {"rows_on_day": 4.1}
    assert [(m["table_name"], m["month"]) for m in out["backup_manifest"]] == [
        ("radar_events", "2026-08"), ("radar_events", "2026-09"), ("radar_member_ticks", "2026-09")]
    assert out["backup_manifest"][1]["min_ts"].startswith("2026-09-01T13:35:00")
    assert out["manual_housekeeping"]["running"] is False


@pytest.mark.asyncio
async def test_health_on_empty_ops_tables(sync_session):
    Base.metadata.create_all(sync_session.get_bind(), tables=[
        RadarHousekeepingRun.__table__, RadarTableMetric.__table__, RadarBackupManifest.__table__])
    out = await routes.radar_health(db=AsyncOverSync(sync_session), store=FakeStore(), user=MEMBER)
    assert out["housekeeping"] is None and out["table_metrics"] == [] and out["backup_manifest"] == []
    assert out["scan_log"] == []


# ── Routes on the real RadarStore (SQLite) ─────────────────────────────────

def _commit_sample_tick(store) -> tuple[datetime, str]:
    """One committed tick an hour ago (the routes read relative to the real clock): an NVDA entry on
    the previous bar and its exit on this one, two member rows and the scan_log row."""
    from radar.calendar_nyse import ET

    now = datetime.now(UTC)
    tick = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0) - timedelta(hours=1)
    day = tick.astimezone(ET).date().isoformat()
    signals = {"z3": 2.9, "z6": 3.1, "zday": 2.2, "rvol3": 3.4, "rvolc": 1.8, "dvwap": 0.012, "er6": 0.6, "acc": 0.4}

    def member(t: datetime, slot: int) -> dict:
        return {"v": 1, "tick": _iso(t), "session": day, "slot": slot, "ticker": "NVDA", "role": "member",
                "dir": "up", "state": "racing", "price": 181.2, "chg_day_pct": 4.2, "chg_5m_pct": 0.4,
                "move_since_entry_pct": 1.1, "vol_5m": 120_000, "intensity": 81.0, "signals": signals}

    def event(t: datetime, kind: str, slot: int) -> dict:
        return {"v": 1, "id": f"{t:%Y%m%dT%H%MZ}-NVDA-{kind}-1", "ts": _iso(t), "session": day, "slot": slot,
                "ticker": "NVDA", "type": kind, "dir": "up", "price": 181.2, "intensity": 81.0,
                "reason": "ENTRY" if kind == "ENTER" else "FADE", "detail": "+2.9σ vs market in 15 min",
                "episode": 1, "held_min": None, "move_since_entry_pct": None, "late": False,
                "signals": signals, "params_version": "radar-sm-1"}

    store.commit_tick(
        state=_state(tick, session_date=day, last_bar=_iso(tick)), engine_doc={"schema": 1, "session": day},
        member_rows=[member(tick, 10), member(tick - timedelta(minutes=5), 9)],
        events=[event(tick - timedelta(minutes=5), "ENTER", 9), event(tick, "EXIT", 10)],
        scan_row={"v": 1, "tick": _iso(tick + timedelta(seconds=50)), "run_id": "local",
                  "written_at": _iso(tick + timedelta(seconds=55)), "session": day, "phase": "regular",
                  "status": "ok", "ms": {"write": 40}},
    )
    return tick, day


@pytest.mark.asyncio
async def test_routes_read_what_the_store_committed(radar_store):
    """Contract check with radar/store.py: rows written by commit_tick come back in the API's shapes."""
    tick, day = _commit_sample_tick(radar_store)

    snap = await routes.radar_snapshot(store=radar_store, user=MEMBER)
    assert snap["schema"] == 1 and snap["tick_id"] == _iso(tick) and isinstance(snap["stale"], bool)

    events = await routes.radar_events(days=1, ticker="nvda", store=radar_store, user=MEMBER)
    assert [e["type"] for e in events] == ["EXIT", "ENTER"]   # newest first
    assert events[0]["ts"] == _iso(tick) and events[0]["signals"]["z3"] == 2.9
    assert await routes.radar_events(days=1, ticker="AMD", store=radar_store, user=MEMBER) == []

    ticks = await routes.radar_ticker_ticks("NVDA", session=None, store=radar_store, user=MEMBER)
    assert ticks["session"] == day   # ET day of last_bar
    assert [t["slot"] for t in ticks["ticks"]] == [9, 10]


@pytest.mark.asyncio
async def test_health_end_to_end_on_the_store_database(radar_store):
    _commit_sample_tick(radar_store)
    with Session(radar_store.engine) as s:
        out = await routes.radar_health(db=AsyncOverSync(s), store=radar_store, user=MEMBER)
    assert len(out["scan_log"]) == 1 and out["scan_log"][0]["ms"] == {"write": 40}
    assert out["housekeeping"] is None and out["table_metrics"] == [] and out["backup_manifest"] == []


# ── Housekeeping trigger (admin only) ──────────────────────────────────────

@pytest.fixture
def backup_dir(tmp_path, monkeypatch) -> str:
    """radar.config.BACKUP_DIR -> an existing, empty, writable directory (the mounted radar_backups volume)."""
    path = tmp_path / "backups"
    path.mkdir()
    monkeypatch.setattr(radar.config, "BACKUP_DIR", str(path))
    return str(path)


def _archive_like_the_worker(store) -> dict:
    """A real apply pass, called the way the worker calls it (radar/worker.py `_run_housekeeping`: no
    backup_dir, so radar.config.BACKUP_DIR): rows past hot_days of member_ticks (14 days) and events
    (45 days) are archived. Afterwards the manifest lists live partitions and their files are on disk."""
    from db.models import RadarEvent, RadarMemberTick
    from radar import housekeeping as hk
    from radar.calendar_nyse import ET

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    signals = {"z3": 2.9, "rvol3": 3.4}
    ticks, events = [], []
    for age in (20, 21, 50):     # all inside the 3-month retention, all past member_ticks' hot_days
        t = now - timedelta(days=age)
        day = t.astimezone(ET).date()
        ticks.append({"tick": t, "session_date": day, "slot": 10, "ticker": "NVDA", "role": "member",
                      "dir": "up", "state": "racing", "price": 181.2, "chg_day_pct": 4.2, "chg_5m_pct": 0.4,
                      "move_since_entry_pct": 1.1, "vol_5m": 120_000, "intensity": 81.0, "signals": signals})
        events.append({"id": f"{t:%Y%m%dT%H%MZ}-NVDA-ENTER-1", "ts": t, "session_date": day, "slot": 10,
                       "ticker": "NVDA", "type": "ENTER", "dir": "up", "price": 181.2, "intensity": 81.0,
                       "reason": "ENTRY", "detail": "+2.9σ vs market in 15 min", "episode": 1, "held_min": None,
                       "move_since_entry_pct": None, "late": False, "signals": signals,
                       "params_version": "radar-sm-1"})
    with store.engine.begin() as c:
        c.execute(RadarMemberTick.__table__.insert(), ticks)
        c.execute(RadarEvent.__table__.insert(), events)
    report = hk.run(mode="apply", trigger="schedule", store=store, force_tables=["member_ticks", "events"],
                    repeats=1, log_pipeline=False)
    assert report["status"] == "ok" and report["backup_dir"] == radar.config.BACKUP_DIR, report
    assert {p.split("/")[0] for p in report["partitions_written"]} == {"radar_member_ticks", "radar_events"}
    return report


def _tree(root: str) -> dict[str, bytes]:
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def _run_rows(store) -> list[dict]:
    with store.engine.connect() as c:
        return [dict(r) for r in c.execute(select(RadarHousekeepingRun.__table__)).mappings()]


@pytest.mark.asyncio
async def test_housekeeping_trigger_is_admin_only(radar_store, backup_dir):
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as err:
        await routes.radar_housekeeping_trigger(tasks, mode="dry-run", store=radar_store, user=MEMBER)
    assert err.value.status_code == 403
    assert tasks.tasks == [] and routes._radar_hk_state["running"] is False


@pytest.mark.asyncio
async def test_housekeeping_trigger_queues_one_run_and_refuses_overlap(radar_store, backup_dir):
    tasks = BackgroundTasks()
    out = await routes.radar_housekeeping_trigger(tasks, mode="dry-run", store=radar_store, user=ADMIN)
    assert out["status"] == "accepted" and out["mode"] == "dry-run"
    assert re.fullmatch(r"hk-\d{8}T\d{6}Z-[0-9a-f]{6}", out["run_id"])   # housekeeping.new_run_id format
    # The run gets the API's store and the very directory that was checked (radar.config.BACKUP_DIR).
    assert len(tasks.tasks) == 1 and tasks.tasks[0].args == (out["run_id"], "dry-run", radar_store, backup_dir)
    assert routes._radar_hk_state["running"] is True and routes._radar_hk_state["run_id"] == out["run_id"]

    with pytest.raises(HTTPException) as err:
        await routes.radar_housekeeping_trigger(BackgroundTasks(), mode="dry-run", store=radar_store, user=ADMIN)
    assert err.value.status_code == 409 and err.value.detail["run_id"] == out["run_id"]


def _unavailable(tmp_path, monkeypatch, cause: str) -> str:
    """Point radar.config.BACKUP_DIR at a directory this container cannot use."""
    path = tmp_path / "radar-backups"
    if cause == "file":
        path.write_bytes(b"")
    elif cause == "read_only":
        path.mkdir()
        real_access = os.access
        monkeypatch.setattr(routes.os, "access",
                            lambda p, m, *a, **k: False if os.fspath(p) == str(path) else real_access(p, m, *a, **k))
    monkeypatch.setattr(radar.config, "BACKUP_DIR", str(path))
    return str(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["dry-run", "apply"])
@pytest.mark.parametrize("cause, reason", [("missing", "missing"), ("file", "missing"),
                                           ("read_only", "not_writable")])
async def test_both_modes_refuse_a_backup_dir_that_is_not_a_writable_directory(
        radar_store, tmp_path, monkeypatch, mode, cause, reason):
    path = _unavailable(tmp_path, monkeypatch, cause)
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as err:
        await routes.radar_housekeeping_trigger(tasks, mode=mode, store=radar_store, user=ADMIN)
    assert err.value.status_code == 503
    assert err.value.detail["error"] == "backup_dir_unavailable" and err.value.detail["reason"] == reason
    assert err.value.detail["backup_dir"] == path and path in err.value.detail["message"]
    assert tasks.tasks == [] and routes._radar_hk_state["running"] is False
    assert _run_rows(radar_store) == []   # nothing recorded: no false ERROR run on /radar/health


@pytest.mark.parametrize("mode", ["dry-run", "apply"])
def test_http_both_modes_refuse_a_fresh_dir_when_the_manifest_lists_partitions(
        radar_store, backup_dir, client_for, tmp_path, monkeypatch, mode):
    """The worker archived into its radar_backups volume; this container sees an empty directory instead.
    Before the fix a dry-run here was accepted and recorded an ERROR run (every partition "missing")."""
    _archive_like_the_worker(radar_store)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setattr(radar.config, "BACKUP_DIR", str(fresh))
    runs_before = _run_rows(radar_store)

    r = client_for(radar_store).post(f"/api/radar/housekeeping?mode={mode}")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "backup_dir_unavailable" and detail["reason"] == "manifest_partitions_absent"
    assert detail["backup_dir"] == str(fresh) and "radar_backups" in detail["message"]
    assert _run_rows(radar_store) == runs_before and routes._radar_hk_state["running"] is False
    assert _tree(str(fresh)) == {}


def test_http_dry_run_on_the_dir_a_real_apply_run_populated_reports_no_error(radar_store, backup_dir, client_for):
    _archive_like_the_worker(radar_store)
    files = _tree(backup_dir)
    assert files

    r = client_for(radar_store).post("/api/radar/housekeeping?mode=dry-run")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    # TestClient ran the background task: a real housekeeping dry-run on the store and the worker's dir.
    st = routes._radar_hk_state
    assert st["running"] is False and st["status"] == "ok" and st["error"] is None, st
    row = next(x for x in _run_rows(radar_store) if x["run_id"] == run_id)
    assert (row["mode"], row["trigger"], row["status"]) == ("dry-run", "manual", "ok")
    report = row["report"]
    assert report["partition_errors"] == {} and report["backup_dir"] == backup_dir
    assert all(t["status"] != "ERROR" for t in report["tables"].values()), report["tables"]
    assert _tree(backup_dir) == files   # a dry-run writes nothing


def test_backup_dir_check_leaves_real_damage_and_expired_entries_to_housekeeping(radar_store, backup_dir):
    """Only a directory holding none of the live partitions is refused. One partition missing out of
    several is real damage that housekeeping must report; expired entries are dropped by its retention."""
    from radar import housekeeping as hk

    now = datetime.now(UTC)
    assert routes._radar_backup_dir_problem(radar_store, backup_dir, now) is None   # empty manifest, fresh dir
    with radar_store.engine.begin() as c:
        c.execute(RadarBackupManifest.__table__.insert(), [
            {"table_name": "radar_events", "month": "2020-01", "path": "radar_events/2020-01.jsonl.gz",
             "rows": 1, "writes": 1}])
    assert routes._radar_backup_dir_problem(radar_store, backup_dir, now) is None   # only an expired entry

    report = _archive_like_the_worker(radar_store)
    assert routes._radar_backup_dir_problem(radar_store, backup_dir, now) is None
    first = report["partitions_written"][0]
    os.remove(hk.partition_path(backup_dir, *first.split("/")))
    assert routes._radar_backup_dir_problem(radar_store, backup_dir, now) is None   # partial loss: let it report

    problem = routes._radar_backup_dir_problem(radar_store, str(Path(backup_dir).parent), now)
    assert problem["reason"] == "manifest_partitions_absent"


def _fake_housekeeping(monkeypatch, run):
    """Stand in for radar.housekeeping so no test runs a real pass from the API."""
    import radar.housekeeping as real

    mod = types.ModuleType("radar.housekeeping")
    mod.run = run
    mod.new_run_id = real.new_run_id
    mod.retention_cutoff = real.retention_cutoff
    mod.month_bounds = real.month_bounds
    mod.partition_path = real.partition_path
    mod.POLICIES = real.POLICIES
    monkeypatch.setitem(sys.modules, "radar.housekeeping", mod)
    monkeypatch.setattr(radar, "housekeeping", mod)


def test_housekeeping_task_passes_the_promised_run_id_and_records_the_outcome(monkeypatch):
    seen = {}

    def run(**kwargs):
        seen.update(kwargs)
        return {"run_id": kwargs["run_id"], "status": "ok"}

    _fake_housekeeping(monkeypatch, run)
    store = FakeStore()
    routes._radar_hk_state.update(running=True, run_id="hk-x", status="running")
    routes._radar_housekeeping_task("hk-x", "dry-run", store, "/backups/radar")
    assert seen == {"mode": "dry-run", "trigger": "manual", "run_id": "hk-x", "record_dry_run": True,
                    "store": store, "backup_dir": "/backups/radar"}
    st = routes._radar_hk_state
    assert st["running"] is False and st["status"] == "ok" and st["finished_at"] is not None


def test_housekeeping_task_reports_busy_and_failures(monkeypatch):
    _fake_housekeeping(monkeypatch, lambda **_: {"status": "busy"})
    routes._radar_housekeeping_task("hk-x", "apply", FakeStore(), "/backups/radar")
    assert routes._radar_hk_state["status"] == "busy"

    def broken(**_):
        raise RuntimeError("disk full")

    _fake_housekeeping(monkeypatch, broken)
    routes._radar_hk_state.update(running=True)
    routes._radar_housekeeping_task("hk-y", "apply", FakeStore(), "/backups/radar")
    st = routes._radar_hk_state
    assert st["running"] is False and st["status"] == "error" and "disk full" in st["error"]


# ── HTTP layer (TestClient + dependency overrides) ─────────────────────────

@pytest.fixture
def client_for():
    def make(store: FakeStore, user=ADMIN) -> TestClient:
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes.get_current_user] = lambda: user
        app.dependency_overrides[routes.get_radar_store] = lambda: store
        return TestClient(app)
    return make


def test_http_snapshot_events_and_validation(client_for):
    client = client_for(FakeStore())
    r = client.get("/api/radar")
    assert r.status_code == 200 and r.json()["status"] == "no_data"
    assert client.get("/api/radar/events?days=0").status_code == 422
    assert client.get("/api/radar/events?days=1&ticker=BRK-B").status_code == 200
    assert client.get("/api/radar/ticker/NVDA?session=2026-13-01").status_code == 400


def test_http_housekeeping_202_403_422_and_background_run(client_for, radar_store, backup_dir, monkeypatch):
    runs = []
    _fake_housekeeping(monkeypatch, lambda **kw: runs.append(kw["run_id"]) or {"status": "ok"})

    assert client_for(radar_store, user=MEMBER).post("/api/radar/housekeeping").status_code == 403
    client = client_for(radar_store)
    assert client.post("/api/radar/housekeeping?mode=purge").status_code == 422
    r = client.post("/api/radar/housekeeping")   # default mode: dry-run
    assert r.status_code == 202 and r.json()["mode"] == "dry-run"
    # TestClient runs the background task after the response: the run is finished now.
    assert runs == [r.json()["run_id"]]
    assert routes._radar_hk_state["status"] == "ok" and routes._radar_hk_state["running"] is False
