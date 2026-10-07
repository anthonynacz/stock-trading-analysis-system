"""radar.store on SQLite (PORT_SPEC section 4): every pinned method, the one-transaction and idempotency
guarantees a re-run tick relies on, and drift checks between db/models.py and the Alembic migration.

The Postgres-only paths (advisory lock, dialect SQL) are checked with a fake connection and by compiling
the statements for the postgresql dialect; no test needs a live Postgres.
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, func, inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, StatementError

from db.models import Base
from radar import store as store_mod
from radar.store import RADAR_TABLES, RadarStore, sync_url

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "d6e2a4c8b1f9_radar_tables.py"

SESSION = "2026-09-28"
TICK = "2026-09-28T13:35:00Z"
NEXT_TICK = "2026-09-28T13:40:00Z"
SIGNALS = {"z3": 2.81, "z6": 3.4, "zday": 2.6, "rvol3": 3.1, "rvolc": 1.9, "dvwap": 0.012, "er6": 0.61, "acc": 0.2}


# ---------------------------------------------------------------- github-spec section 6 shaped documents

def member_row(ticker: str = "NVDA", tick: str = TICK, slot: int = 0, role: str = "member", **kw: Any) -> dict:
    row = {"v": 1, "tick": tick, "session": SESSION, "slot": slot, "ticker": ticker, "role": role, "dir": "up",
           "state": "racing" if role == "member" else "heating", "price": 181.2345, "chg_day_pct": 3.21,
           "chg_5m_pct": 0.84, "move_since_entry_pct": 1.05 if role == "member" else None, "vol_5m": 1_234_567,
           "intensity": 71.4, "signals": dict(SIGNALS)}
    row.update(kw)
    return row


def event(ticker: str = "NVDA", ts: str = TICK, kind: str = "ENTER", episode: int = 1, **kw: Any) -> dict:
    stamp = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%dT%H%MZ")
    enter = kind == "ENTER"
    ev = {"v": 1, "id": f"{stamp}-{ticker}-{kind}-{episode}", "ts": ts, "session": SESSION, "slot": 0,
          "ticker": ticker, "type": kind, "dir": "up", "price": 181.2345, "intensity": 71.4,
          "reason": "ENTRY" if enter else "FADE", "detail": "+2.8σ vs market in 15 min" if enter else "move faded",
          "episode": episode, "held_min": None if enter else 25, "move_since_entry_pct": None if enter else 1.2,
          "late": False, "signals": dict(SIGNALS), "params_version": "radar-sm-1"}
    ev.update(kw)
    return ev


def scan_row(tick: str = TICK, run_id: str = "local", status: str = "ok", **kw: Any) -> dict:
    row = {"v": 1, "tick": tick, "run_id": run_id, "written_at": tick.replace(":00Z", ":52Z"), "session": SESSION,
           "phase": "regular", "status": status, "lag_s": 50, "universe": 503, "stage_b": 41, "quotes_ok": 503,
           "quotes_err": 0, "bars_ok": 41, "bars_err": 0, "members": 1, "heating": 1, "entered": ["NVDA"],
           "exited": [], "processed_slots": [0], "source": "yahoo",
           "ms": {"fetch_quotes": 900, "fetch_movers": 300, "fetch_bars": 1200, "baselines": 0, "compute": 80,
                  "write": 12, "total": 2600},
           "git_prev": {"stage": 0, "commit": 0, "push": 0, "attempts": 0, "status": "none"},
           "hot_bytes": {"member_ticks": 0, "events": 0, "scan_log": 0, "state": 0}, "errors": []}
    row.update(kw)
    return row


def state_doc(tick: str = TICK, status: str = "ok", members: list[str] = ("NVDA",)) -> dict:
    return {"schema": 1, "generated_at": tick, "tick_id": tick, "last_bar": tick, "status": status, "message": "",
            "session": {"date": SESSION, "phase": "regular", "open": "2026-09-28T13:30:00Z",
                        "close": "2026-09-28T20:00:00Z", "half_day": False},
            "next_tick_at": None, "params_version": "radar-sm-1",
            "source": {"name": "yahoo", "status": "ok", "consecutive_failures": 0, "last_ok_at": tick},
            "market": {"mode": "normal", "dir": None, "spy_chg_day_pct": 0.4, "spy_z30": 0.3, "breadth30": 0.5},
            "counts": {"universe": 503, "stage_b": 41, "members": len(members), "heating": 0, "entered_today": 1,
                       "exited_today": 0},
            "members": [{"ticker": t, "name": f"{t} Corp", "sector": "Technology", "direction": "up",
                         "intensity": 71, "reasons": ["volume 3.4× normal for this time"],
                         "spark": {"t0": tick, "step_s": 300, "entry_i": 0, "p": [180.1, 181.2]}} for t in members],
            "heating": [], "recent_exits": [], "sector_banners": [],
            "health": {"ticks_today": 1, "ticks_skipped": 0, "last_tick_ms": 2600, "loop_run_id": "vela",
                       "loop_started_at": None, "published_late": 0},
            "disclaimer": "Educational analysis of what is moving now, not a forecast and not financial advice."}


def engine_doc(session: str = SESSION, failures: int = 0) -> dict:
    return {"schema": 1, "session": session, "engine": {"last_slot": 0, "members": {"NVDA": {"episode": 1}}},
            "source_health": {"name": "yahoo", "status": "ok", "consecutive_failures": failures,
                              "last_ok_at": TICK, "families": {"chart": {"consecutive_failures": failures},
                                                               "crumb": {"consecutive_failures": 0}}},
            "dynamic_adds": ["XYZ"], "pack_gaps": {"session": session, "retries": 1},
            "ops": {"hk_dispatched_at": None, "hk_request": None}, "loop": {"ticks_today": 1}}


def pack(session: str = SESSION, params_version: str = "radar-sm-1", n: int = 3) -> tuple[str, str, bytes]:
    doc = {"asof": session, "params_version": params_version, "symbols": {f"S{i}": {"medbar_usd": i} for i in range(n)}}
    return session, params_version, gzip.compress(json.dumps(doc).encode(), mtime=0)


def commit(store: RadarStore, *, tick: str = TICK, members: list[dict] | None = None,
           events: list[dict] | None = None, **kw: Any) -> None:
    store.commit_tick(state=kw.pop("state", state_doc(tick)), engine_doc=kw.pop("engine_doc", engine_doc()),
                      member_rows=[member_row(tick=tick), member_row("AMD", tick=tick, role="heating")]
                      if members is None else members,
                      events=[event(ts=tick)] if events is None else events,
                      scan_row=kw.pop("scan_row", scan_row(tick)), **kw)


def count(store: RadarStore, tbl: Any) -> int:
    with store.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(tbl)).scalar()


def table(name: str) -> Any:
    return Base.metadata.tables[name]


# ---------------------------------------------------------------- reads on an empty store

def test_empty_store_reads(radar_store: RadarStore) -> None:
    assert radar_store.load_state() == {}
    assert radar_store.load_engine_doc() == {}
    assert radar_store.load_live_health() is None
    assert radar_store.load_pack(SESSION, "main") == (None, None)
    assert radar_store.recent_events() == []
    assert radar_store.member_ticks("NVDA", SESSION) == []
    assert radar_store.scan_log_tail() == []
    assert radar_store.get_alert_cursor() is None


# ---------------------------------------------------------------- commit_tick

def test_commit_tick_round_trips_every_document(radar_store: RadarStore) -> None:
    rows = [member_row(), member_row("AMD", role="heating")]
    evs = [event()]
    srow = scan_row()
    commit(radar_store, members=rows, events=evs, scan_row=srow, packs={"main": pack(), "extra": pack(n=1)})

    assert radar_store.load_state() == state_doc()
    assert radar_store.load_engine_doc() == engine_doc()
    assert radar_store.member_ticks("NVDA", SESSION) == [rows[0]]
    assert radar_store.member_ticks("AMD", SESSION) == [rows[1]]       # heating row: move_since_entry_pct None
    assert radar_store.recent_events() == evs
    assert radar_store.scan_log_tail() == [srow]
    assert radar_store.load_pack(SESSION, "main") == pack()[1:]
    assert radar_store.load_pack(SESSION, "extra") == pack(n=1)[1:]


def test_recommitting_the_same_tick_is_idempotent(radar_store: RadarStore) -> None:
    commit(radar_store, packs={"main": pack()})
    before = {t.name: count(radar_store, t) for t in RADAR_TABLES}
    commit(radar_store, packs={"main": pack()})
    assert {t.name: count(radar_store, t) for t in RADAR_TABLES} == before
    assert before["radar_member_ticks"] == 2 and before["radar_events"] == 1 and before["radar_scan_log"] == 1
    assert radar_store.load_state() == state_doc()


def test_rerun_tick_keeps_the_first_copy_of_each_row(radar_store: RadarStore) -> None:
    commit(radar_store)
    commit(radar_store, members=[member_row(price=999.0)], events=[event(price=999.0)],
           scan_row=scan_row(status="degraded"))
    assert radar_store.member_ticks("NVDA", SESSION)[0]["price"] == 181.2345
    assert radar_store.recent_events()[0]["price"] == 181.2345
    assert radar_store.scan_log_tail()[0]["status"] == "ok"


def test_a_new_tick_appends_and_replaces_the_snapshot(radar_store: RadarStore) -> None:
    commit(radar_store)
    nxt = state_doc(NEXT_TICK, members=["NVDA", "TSLA"])
    commit(radar_store, tick=NEXT_TICK, state=nxt, members=[member_row(tick=NEXT_TICK, slot=1)],
           events=[event("TSLA", ts=NEXT_TICK)])
    assert radar_store.load_state() == nxt
    assert [r["tick"] for r in radar_store.member_ticks("NVDA", SESSION)] == [TICK, NEXT_TICK]
    assert [e["ticker"] for e in radar_store.recent_events()] == ["TSLA", "NVDA"]


def test_commit_is_one_transaction(radar_store: RadarStore) -> None:
    """A failure inside the transaction (here a NaN, which Postgres JSON rejects) leaves nothing behind,
    not even the snapshot that was written first."""
    with pytest.raises((ValueError, StatementError), match="JSON compliant"):
        commit(radar_store, events=[event(signals={"z3": float("nan")})])
    assert radar_store.load_state() == {}
    assert radar_store.load_engine_doc() == {}
    assert all(count(radar_store, t) == 0 for t in RADAR_TABLES)


def test_malformed_rows_fail_before_writing(radar_store: RadarStore) -> None:
    bad = member_row()
    del bad["tick"]
    with pytest.raises(ValueError, match="tick"):
        commit(radar_store, members=[bad])
    with pytest.raises(ValueError, match="unknown baseline pack kind"):
        commit(radar_store, packs={"baselines": pack()})
    assert radar_store.load_state() == {}
    assert count(radar_store, table("radar_scan_log")) == 0


def test_non_finite_and_numpy_values_are_stored_as_plain_values(radar_store: RadarStore) -> None:
    np = pytest.importorskip("numpy")
    row = member_row(price=np.float64(12.5), chg_5m_pct=float("nan"), vol_5m=np.int64(42),
                     intensity=float("inf"), signals={"z3": np.float64(1.5)})
    commit(radar_store, members=[row], events=[event(episode=np.int64(2), late=np.bool_(True))])
    got = radar_store.member_ticks("NVDA", SESSION)[0]
    assert (got["price"], got["chg_5m_pct"], got["vol_5m"], got["intensity"]) == (12.5, None, 42, None)
    assert got["signals"] == {"z3": 1.5}
    ev = radar_store.recent_events()[0]
    assert ev["episode"] == 2 and ev["late"] is True


# ---------------------------------------------------------------- live health, failures, alert cursor

def test_live_health_is_written_alone_and_cleared_by_a_commit(radar_store: RadarStore) -> None:
    health = {"name": "yahoo", "status": "degraded", "consecutive_failures": 2, "families": {}}
    radar_store.write_live_health(health)                 # no runtime row yet: the insert path
    assert radar_store.load_live_health() == health
    assert radar_store.load_engine_doc() == {}

    commit(radar_store)
    assert radar_store.load_live_health() is None
    radar_store.write_live_health({**health, "consecutive_failures": 3})
    assert radar_store.load_live_health()["consecutive_failures"] == 3
    assert radar_store.load_engine_doc() == engine_doc()   # a live-health write never touches the engine doc


def test_write_failure_records_error_state_and_merged_health(radar_store: RadarStore) -> None:
    commit(radar_store)
    radar_store.write_live_health({"name": "yahoo", "status": "down", "consecutive_failures": 5})
    err_state = {**state_doc(NEXT_TICK), "status": "error", "message": "The last scan timed out."}
    merged = engine_doc(failures=6)
    radar_store.write_failure(state=err_state, engine_doc=merged,
                              scan_row=scan_row(NEXT_TICK, status="error", errors=["timeout: killed"]))

    assert radar_store.load_state() == err_state
    assert radar_store.load_engine_doc() == merged
    assert radar_store.load_live_health() is None          # merged into the engine doc by the worker
    tail = radar_store.scan_log_tail()
    assert [r["status"] for r in tail] == ["error", "ok"]
    assert tail[0]["errors"] == ["timeout: killed"]
    assert [e["id"] for e in radar_store.recent_events()] == [event()["id"]]   # failures add no events

    # A repeated failure write of the same tick is idempotent for the scan_log row.
    radar_store.write_failure(state=err_state, engine_doc=merged, scan_row=scan_row(NEXT_TICK, status="error"))
    assert count(radar_store, table("radar_scan_log")) == 2


def test_write_failure_with_empty_documents_only_logs_the_scan(radar_store: RadarStore) -> None:
    commit(radar_store)
    radar_store.write_live_health({"status": "down"})
    radar_store.write_failure(state={}, engine_doc={}, scan_row=scan_row("2026-09-28T13:10:00Z", status="error"))
    assert radar_store.load_state() == state_doc()          # the warmup path does not republish the snapshot
    assert radar_store.load_engine_doc() == engine_doc()
    assert radar_store.load_live_health() == {"status": "down"}
    assert count(radar_store, table("radar_scan_log")) == 2


def test_failure_after_a_committed_scan_keeps_the_ok_scan_row(radar_store: RadarStore) -> None:
    commit(radar_store)
    radar_store.write_failure(state=state_doc(status="error"), engine_doc=engine_doc(),
                              scan_row=scan_row(status="error"))
    assert [r["status"] for r in radar_store.scan_log_tail()] == ["ok"]


def test_alert_cursor_survives_every_other_write(radar_store: RadarStore) -> None:
    radar_store.set_alert_cursor("20260928T1335Z-NVDA-ENTER-1")   # before any runtime row exists
    assert radar_store.get_alert_cursor() == "20260928T1335Z-NVDA-ENTER-1"
    commit(radar_store)
    radar_store.write_live_health({"status": "ok"})
    radar_store.write_failure(state=state_doc(), engine_doc=engine_doc(), scan_row=scan_row(NEXT_TICK))
    assert radar_store.get_alert_cursor() == "20260928T1335Z-NVDA-ENTER-1"
    assert radar_store.load_engine_doc() == engine_doc()           # and setting it kept the engine doc
    radar_store.set_alert_cursor("20260928T1340Z-TSLA-ENTER-1")
    assert radar_store.get_alert_cursor() == "20260928T1340Z-TSLA-ENTER-1"
    assert radar_store.load_engine_doc() == engine_doc()


# ---------------------------------------------------------------- baseline packs

def test_packs_upsert_per_session_and_kind(radar_store: RadarStore) -> None:
    commit(radar_store, packs={"main": pack()})
    commit(radar_store, tick=NEXT_TICK, packs={"main": pack(params_version="radar-sm-2", n=5)})
    pv, gz = radar_store.load_pack(SESSION, "main")
    assert pv == "radar-sm-2"
    assert len(json.loads(gzip.decompress(gz))["symbols"]) == 5
    assert radar_store.load_pack(SESSION, "extra") == (None, None)
    assert count(radar_store, table("radar_baselines")) == 1


def test_old_packs_are_pruned_when_a_new_one_is_written(radar_store: RadarStore) -> None:
    old = (datetime(2026, 9, 28) - timedelta(days=store_mod.PACK_KEEP_DAYS + 1)).date().isoformat()
    kept = (datetime(2026, 9, 28) - timedelta(days=store_mod.PACK_KEEP_DAYS)).date().isoformat()
    commit(radar_store, packs={"main": pack(old), "extra": pack(old, n=1)})
    commit(radar_store, packs={"main": pack(kept)})
    commit(radar_store, packs={"main": pack(SESSION)})
    assert radar_store.load_pack(old, "main") == (None, None)
    assert radar_store.load_pack(old, "extra") == (None, None)
    assert radar_store.load_pack(kept, "main")[0] == "radar-sm-1"
    assert radar_store.load_pack(SESSION, "main")[0] == "radar-sm-1"


def test_commit_without_packs_leaves_them_alone(radar_store: RadarStore) -> None:
    commit(radar_store, packs={"main": pack()})
    commit(radar_store, tick=NEXT_TICK)
    assert radar_store.load_pack(SESSION, "main") == pack()[1:]


# ---------------------------------------------------------------- read filters and ordering

def test_recent_events_filters_and_orders_newest_first(radar_store: RadarStore) -> None:
    t3 = "2026-09-28T13:45:00Z"
    commit(radar_store, events=[event("NVDA"), event("AMD")])
    commit(radar_store, tick=NEXT_TICK, events=[event("NVDA", ts=NEXT_TICK, kind="EXIT")])
    commit(radar_store, tick=t3, events=[event("TSLA", ts=t3)])

    ids = [e["id"] for e in radar_store.recent_events()]
    assert ids == ["20260928T1345Z-TSLA-ENTER-1", "20260928T1340Z-NVDA-EXIT-1",
                   "20260928T1335Z-NVDA-ENTER-1", "20260928T1335Z-AMD-ENTER-1"]
    since = datetime(2026, 9, 28, 13, 40, tzinfo=timezone.utc)                 # inclusive
    assert [e["ticker"] for e in radar_store.recent_events(since=since)] == ["TSLA", "NVDA"]
    assert [e["type"] for e in radar_store.recent_events(ticker="nvda")] == ["EXIT", "ENTER"]
    assert len(radar_store.recent_events(limit=2)) == 2
    assert radar_store.recent_events(limit=0) == []
    # A naive `since` is UTC; an aware one in another zone is converted.
    assert len(radar_store.recent_events(since=datetime(2026, 9, 28, 13, 40))) == 2
    et = timezone(timedelta(hours=-4))
    assert len(radar_store.recent_events(since=datetime(2026, 9, 28, 9, 45, tzinfo=et))) == 1


def test_member_ticks_are_one_ticker_and_session_oldest_first(radar_store: RadarStore) -> None:
    commit(radar_store, tick=NEXT_TICK, members=[member_row(tick=NEXT_TICK, slot=1)])
    commit(radar_store, members=[member_row(slot=0), member_row("AMD")])
    commit(radar_store, tick="2026-09-29T13:35:00Z",
           members=[member_row(tick="2026-09-29T13:35:00Z", session="2026-09-29")])
    assert [(r["tick"], r["slot"]) for r in radar_store.member_ticks("nvda", SESSION)] == [(TICK, 0), (NEXT_TICK, 1)]
    assert len(radar_store.member_ticks("NVDA", "2026-09-29")) == 1


def test_scan_log_tail_is_newest_first_and_limited(radar_store: RadarStore) -> None:
    ticks = [f"2026-09-28T13:{m:02d}:00Z" for m in (35, 40, 45, 50)]
    for t in ticks:
        commit(radar_store, tick=t)
    assert [r["tick"] for r in radar_store.scan_log_tail()] == ticks[::-1]
    assert [r["tick"] for r in radar_store.scan_log_tail(2)] == ticks[:1:-1]


def test_closed_heartbeat_scan_rows_have_no_session(radar_store: RadarStore) -> None:
    hb = scan_row("2026-09-28T02:00:00Z", status="closed", session=None, phase="closed")
    commit(radar_store, tick="2026-09-28T02:00:00Z", members=[], events=[], scan_row=hb)
    assert radar_store.scan_log_tail() == [hb]


# ---------------------------------------------------------------- worker support

def test_try_advisory_lock_is_a_no_op_on_sqlite(radar_store: RadarStore) -> None:
    assert radar_store.try_advisory_lock(771234) is True
    assert radar_store.try_advisory_lock(771234) is True


class _FakeConn:
    def __init__(self, engine: "_FakeEngine", grant: bool) -> None:
        self.engine, self.grant, self.closed, self.sql = engine, grant, False, []
        self.options: dict = {}

    def execution_options(self, **kw: Any) -> "_FakeConn":
        self.options.update(kw)
        return self

    def execute(self, stmt: Any, params: dict | None = None) -> Any:
        if self.engine.drop and self in self.engine.held:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        self.sql.append((str(stmt), params))
        value = self.grant if "pg_try_advisory_lock" in str(stmt) else 1
        if "pg_try_advisory_lock" in str(stmt) and self.grant:
            self.engine.held.append(self)
        return SimpleNamespace(scalar=lambda: value)

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, grants: list[bool]) -> None:
        self.grants, self.conns, self.held, self.drop = list(grants), [], [], False

    def connect(self) -> _FakeConn:
        grant = self.grants.pop(0)
        if isinstance(grant, Exception):
            raise grant
        conn = _FakeConn(self, grant)
        self.conns.append(conn)
        return conn

    def dispose(self) -> None:
        pass


def test_try_advisory_lock_on_postgres_keeps_a_dedicated_connection(radar_store: RadarStore) -> None:
    down = OperationalError("connect", {}, Exception("could not connect to server"))
    real, fake = radar_store.engine, _FakeEngine([down, False, True, True])
    radar_store.engine = fake
    try:
        _exercise_lock(radar_store, fake)
    finally:
        radar_store.engine = real


def _exercise_lock(radar_store: RadarStore, fake: _FakeEngine) -> None:
    assert radar_store.try_advisory_lock(771234) is False     # database not up yet: retry later, no raise
    assert fake.conns == []
    assert radar_store.try_advisory_lock(771234) is False     # another worker holds it
    assert fake.conns[0].closed

    assert radar_store.try_advisory_lock(771234) is True
    holder = fake.conns[1]
    assert not holder.closed and holder.options == {"isolation_level": "AUTOCOMMIT"}
    assert holder.sql[0][1] == {"key": 771234}

    assert radar_store.try_advisory_lock(771234) is True      # re-check on the same connection
    assert len(fake.conns) == 2 and holder.sql[-1][0] == "SELECT 1"

    fake.drop = True                                          # the holding session died: take the lock again
    assert radar_store.try_advisory_lock(771234) is True
    assert holder.closed and len(fake.conns) == 3

    radar_store.close()
    assert fake.conns[2].closed


def test_ping(radar_store: RadarStore, tmp_path: Path) -> None:
    assert radar_store.ping() is True
    broken = RadarStore(f"sqlite:///{(tmp_path / 'missing-dir' / 'x.db').as_posix()}")
    try:
        assert broken.ping() is False
    finally:
        broken.close()


def test_schema_ready(radar_store: RadarStore, tmp_path: Path) -> None:
    assert radar_store.schema_ready() is True
    empty = RadarStore(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    try:
        assert empty.schema_ready() is False
    finally:
        empty.close()


# ---------------------------------------------------------------- URLs

def test_sync_url_swaps_the_async_driver() -> None:
    assert sync_url("postgresql+asyncpg://u:p@db:5432/edgeflow") == "postgresql+psycopg2://u:p@db:5432/edgeflow"
    assert sync_url("postgresql://u:p@db/edgeflow") == "postgresql+psycopg2://u:p@db/edgeflow"
    assert sync_url("postgres://u:p@db/edgeflow") == "postgresql+psycopg2://u:p@db/edgeflow"
    assert sync_url("postgresql+psycopg://u:p@db/edgeflow") == "postgresql+psycopg://u:p@db/edgeflow"
    assert sync_url("sqlite:///x.db") == "sqlite:///x.db"


def test_an_async_url_is_made_sync() -> None:
    pytest.importorskip("psycopg2")
    store = RadarStore("postgresql+asyncpg://u:p@localhost:5432/edgeflow")    # no connection is opened
    try:
        assert (store.engine.dialect.name, store.engine.dialect.driver) == ("postgresql", "psycopg2")
        assert store.is_postgres
    finally:
        store.close()


def test_default_url_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import config
    monkeypatch.setattr(config.settings, "DATABASE_URL", "postgresql+asyncpg://edgeflow:s3cret@db:5432/edgeflow")
    assert store_mod.default_url() == "postgresql+psycopg2://edgeflow:s3cret@db:5432/edgeflow"


def test_unsupported_database_is_rejected() -> None:
    with pytest.raises(ValueError, match="postgresql and sqlite"):
        RadarStore("mysql://u:p@localhost/db")


# ---------------------------------------------------------------- Postgres SQL (compiled, no server)

class _Recorder:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, stmt: Any, params: Any = None) -> None:
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))


def test_postgres_statements_render_with_on_conflict(radar_store: RadarStore) -> None:
    radar_store._insert = postgresql.insert
    rec = _Recorder()
    now = datetime.now(timezone.utc)
    radar_store._put_state(rec, {"a": 1}, now)
    radar_store._put_runtime(rec, now, engine={"b": 2}, source_health_live=store_mod.null())
    radar_store._insert_new(rec, table("radar_member_ticks"), [{"tick": now}], ("tick", "ticker"))
    radar_store._insert_new(rec, table("radar_events"), [{"id": "x"}], ("id",))
    radar_store._insert_new(rec, table("radar_scan_log"), [{"tick": now}], ("tick", "run_id"))
    radar_store._put_packs(rec, store_mod._pack_values({"main": pack()}), now)
    snap, runtime, members, events, scans, packs, prune = rec.sql
    assert "ON CONFLICT (id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at" in snap
    assert ("ON CONFLICT (id) DO UPDATE SET engine = excluded.engine, source_health_live = "
            "excluded.source_health_live, updated_at = excluded.updated_at") in runtime
    assert "alert_cursor" not in runtime
    assert members.endswith("ON CONFLICT (tick, ticker) DO NOTHING")
    assert events.endswith("ON CONFLICT (id) DO NOTHING")
    assert scans.endswith("ON CONFLICT (tick, run_id) DO NOTHING")
    assert "ON CONFLICT (session_date, kind) DO UPDATE SET params_version = excluded.params_version" in packs
    assert prune.startswith("DELETE FROM radar_baselines WHERE radar_baselines.session_date <")


# ---------------------------------------------------------------- models vs migration
#
# A fresh DB gets the radar tables from the baseline's create_all(); production gets them from the
# d6e2a4c8b1f9 migration. Both must end with the same schema, or `alembic revision --autogenerate` on a
# migrated DB proposes radar changes (a separate CREATE UNIQUE INDEX instead of a named UNIQUE constraint
# shows up as remove_index + add_constraint, a DB default the model does not declare as modify_default).
# Three views of that: the migration's Postgres text against the models compiled for Postgres (types,
# nullability, names, defaults); the schema the migration really builds on SQLite, reflected against the
# models and passed through Alembic's own comparison; and the offline (`--sql`) rendering for Postgres.

RADAR_REVISION, PRIOR_REVISION = "d6e2a4c8b1f9", "a1f7d4e92c60"


def _migration_sql(*, raw: bool = False) -> list[str]:
    """The statements upgrade() executes, whitespace-collapsed (raw=True: as written)."""
    spec = importlib.util.spec_from_file_location("radar_tables_migration", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    executed: list[str] = []
    mod.op = SimpleNamespace(execute=lambda sql: executed.append(sql if raw else " ".join(sql.split())))
    mod.upgrade()
    return executed


PG_TYPE = {"SERIAL": "INTEGER", "BIGSERIAL": "BIGINT", "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
           "DOUBLE PRECISION": "FLOAT"}


def _model_default(col: Any, dialect: Any) -> str | None:
    """A column's server_default as `dialect` spells it in DDL, or None."""
    if col.server_default is None:
        return None
    arg = col.server_default.arg
    return arg if isinstance(arg, str) else str(arg.compile(dialect=dialect))


def _model_keys(t: Any) -> tuple[dict, dict, dict]:
    """(unique constraints {name: cols}, indexes {name: (cols, unique)}, check constraints {name: sql})."""
    uniques, checks = {}, {}
    for c in t.constraints:
        if isinstance(c, UniqueConstraint):
            cols = tuple(col.name for col in c.columns)
            assert c.name, f"{t.name}: unnamed unique key {cols}; name it so the migration can declare it"
            uniques[c.name] = cols
        elif isinstance(c, CheckConstraint):
            checks[c.name] = str(c.sqltext)
    indexes = {ix.name: (tuple(col.name for col in ix.columns), bool(ix.unique)) for ix in t.indexes}
    return uniques, indexes, checks


def test_migration_creates_exactly_the_models_schema() -> None:
    """The migration's Postgres DDL against the models compiled for Postgres: columns, types, nullability,
    server defaults, and the names and columns of the unique constraints, check constraints and indexes."""
    statements = _migration_sql()
    assert all("IF NOT EXISTS" in s for s in statements)
    created: dict[str, dict] = {}
    uniques: dict[str, dict] = {}
    checks: dict[str, dict] = {}
    indexes: dict[str, dict] = {}
    for s in statements:
        m = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*)\)$", s)
        if m:
            tname, cols = m.group(1), {}
            uniques[tname], checks[tname] = {}, {}
            for part in re.split(r",\s(?![^()]*\))", m.group(2)):
                part = part.strip()
                c = re.match(r"CONSTRAINT (\w+) UNIQUE \(([^)]*)\)$", part)
                if c:
                    uniques[tname][c.group(1)] = tuple(x.strip() for x in c.group(2).split(","))
                    continue
                c = re.match(r"CONSTRAINT (\w+) CHECK \((.*)\)$", part)
                if c:
                    checks[tname][c.group(1)] = c.group(2)
                    continue
                assert not part.startswith("CONSTRAINT"), part
                name, rest = part.split(" ", 1)
                assert not re.search(r"\b(UNIQUE|REFERENCES)\b", rest), \
                    f"{tname}.{name}: declare keys as named table constraints, not inline"
                typ = re.match(r"(DOUBLE PRECISION|\w+(?:\(\d+\))?)", rest).group(1)
                default = re.search(r"\bDEFAULT (\w+(?:\(\))?)", rest)
                cols[name.strip('"')] = {"type": PG_TYPE.get(typ, typ),
                                         "nullable": "NOT NULL" not in rest and "PRIMARY KEY" not in rest,
                                         "default": default.group(1).lower() if default else None}
            created[tname] = cols
            continue
        m = re.match(r"CREATE (UNIQUE )?INDEX IF NOT EXISTS (\w+) ON (\w+) \(([^)]*)\)$", s)
        assert m, s
        indexes.setdefault(m.group(3), {})[m.group(2)] = (tuple(c.strip() for c in m.group(4).split(",")),
                                                          bool(m.group(1)))

    assert set(created) == {t.name for t in RADAR_TABLES}
    pg = postgresql.dialect()
    for t in RADAR_TABLES:
        model_cols = {}
        for c in t.columns:
            default = _model_default(c, pg)
            model_cols[c.name] = {"type": c.type.compile(dialect=pg), "nullable": c.nullable,
                                  "default": default.lower() if default else None}
        assert created[t.name] == model_cols, t.name
        model_uniques, model_indexes, model_checks = _model_keys(t)
        assert uniques[t.name] == model_uniques, t.name
        assert indexes.get(t.name, {}) == model_indexes, t.name
        assert checks[t.name] == model_checks, t.name


# The migration's Postgres-only spellings and their SQLite equivalents. Everything else (constraint and
# index names, key columns, NOT NULL, the other defaults, IF NOT EXISTS) runs on SQLite exactly as written.
SQLITE_SPELLINGS = ((r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY"),   # SQLite auto-increments only this
                    (r"\bSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY"),
                    (r"\bDEFAULT NOW\(\)", "DEFAULT CURRENT_TIMESTAMP"))


@pytest.fixture
def migrated_sqlite(tmp_path: Path) -> Iterator[Engine]:
    """A SQLite database whose radar tables were built by the migration's own statements, not by
    create_all(). They run twice: the second run must be a no-op (IF NOT EXISTS)."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}")
    statements = _migration_sql(raw=True)
    for pattern, replacement in SQLITE_SPELLINGS:
        statements = [re.sub(pattern, replacement, s) for s in statements]
    for _ in range(2):
        with engine.begin() as conn:
            for s in statements:
                conn.execute(text(s))
    try:
        yield engine
    finally:
        engine.dispose()


def _same_default(conn: Any, a: str | None, b: str | None) -> bool:
    """Two DDL default expressions give the same value, evaluated by the database as Alembic does on
    Postgres (on SQLite, FALSE = 0)."""
    if a is None or b is None:
        return a is None and b is None
    return bool(conn.execute(text(f"SELECT ({a}) = ({b})")).scalar())


def test_migrated_sqlite_schema_matches_the_models(migrated_sqlite: Engine) -> None:
    """Reflect what the migration built: the same unique-constraint, index and check-constraint names on
    the same columns, and the same columns, primary keys, nullability and server defaults as db/models.py."""
    insp = inspect(migrated_sqlite)
    assert {t.name for t in RADAR_TABLES} <= set(insp.get_table_names())
    lite = sqlite.dialect()
    with migrated_sqlite.connect() as conn:
        for t in RADAR_TABLES:
            model_uniques, model_indexes, model_checks = _model_keys(t)
            got_uniques = {u["name"]: tuple(u["column_names"]) for u in insp.get_unique_constraints(t.name)}
            assert got_uniques == model_uniques, t.name
            got_indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]))
                           for i in insp.get_indexes(t.name)}
            assert got_indexes == model_indexes, t.name        # no stray unique index beside a constraint
            assert {c["name"] for c in insp.get_check_constraints(t.name)} == set(model_checks), t.name
            assert insp.get_pk_constraint(t.name)["constrained_columns"] == [c.name for c in t.primary_key]
            got_cols = insp.get_columns(t.name)
            assert [c["name"] for c in got_cols] == list(t.columns.keys()), t.name
            for col in got_cols:
                mcol = t.columns[col["name"]]
                if not mcol.primary_key:                       # SQLite reports an INTEGER PRIMARY KEY as nullable
                    assert col["nullable"] == mcol.nullable, f"{t.name}.{col['name']}"
                assert _same_default(conn, col["default"], _model_default(mcol, lite)), (
                    f"{t.name}.{col['name']}: migration DEFAULT {col['default']!r}, "
                    f"model server_default {_model_default(mcol, lite)!r}")


def test_autogenerate_finds_no_radar_changes_on_the_migrated_schema(migrated_sqlite: Engine) -> None:
    """What `alembic revision --autogenerate` proposes for the radar tables after this migration: nothing.
    Types are left to the Postgres-text test above (SQLite reflects TIMESTAMPTZ and the like by affinity);
    tables, columns, keys, indexes, nullability and server defaults are compared here."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    radar_names = {t.name for t in RADAR_TABLES}

    def compare_default(context: Any, insp_col: Any, meta_col: Any, insp_default: Any, meta_default: Any,
                        rendered_meta_default: str | None) -> bool:
        return not _same_default(context.connection, insp_default, rendered_meta_default)

    with migrated_sqlite.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={
            "compare_type": False, "compare_server_default": compare_default,
            "include_object": lambda obj, name, type_, reflected, compare_to:
                type_ != "table" or name in radar_names})
        ops = [op for diff in compare_metadata(ctx, Base.metadata)
               for op in (diff if isinstance(diff, list) else [diff])]

    def sqlite_pk_artifact(op: tuple) -> bool:
        # SQLite reflects every PRIMARY KEY column as nullable (a PRIMARY KEY does not imply NOT NULL
        # there); Postgres does, and the Postgres-text test checks it. Anything else is real drift.
        return op[0] == "modify_nullable" and Base.metadata.tables[op[2]].c[op[3]].primary_key

    assert [op for op in ops if not sqlite_pk_artifact(op)] == []


def test_the_store_works_on_the_migrated_schema(migrated_sqlite: Engine) -> None:
    """The migration's unique constraints are the ON CONFLICT arbiters of a re-run tick."""
    store = RadarStore(migrated_sqlite.url.render_as_string(hide_password=False))
    try:
        commit(store, packs={"main": pack()})
        before = {t.name: count(store, t) for t in RADAR_TABLES}
        commit(store, packs={"main": pack(params_version="radar-sm-2")})
        assert {t.name: count(store, t) for t in RADAR_TABLES} == before
        assert store.load_pack(SESSION, "main")[0] == "radar-sm-2"
        assert store.recent_events() == [event()] and store.scan_log_tail() == [scan_row()]
    finally:
        store.close()


def test_migration_renders_offline_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """`alembic upgrade a1f7d4e92c60:d6e2a4c8b1f9 --sql` through the project's env.py: exactly upgrade()'s
    statements and the version bump, in one transaction, with every unique key a named constraint."""
    import config
    from alembic import command
    from alembic.config import Config

    monkeypatch.setattr(config.settings, "DATABASE_URL", "postgresql+asyncpg://edgeflow:x@db:5432/edgeflow")
    out = io.StringIO()
    cfg = Config(output_buffer=out)            # no ini file, so env.py leaves the test run's logging alone
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, f"{PRIOR_REVISION}:{RADAR_REVISION}", sql=True)

    sql = out.getvalue()
    lines = [ln for ln in sql.splitlines() if not ln.startswith("--")]
    rendered = [" ".join(s.split()) for s in "\n".join(lines).split(";")]
    rendered = [s for s in rendered if s]
    assert rendered[0] == "BEGIN" and rendered[-1] == "COMMIT"
    assert rendered[1:-2] == _migration_sql()
    assert rendered[-2] == (f"UPDATE alembic_version SET version_num='{RADAR_REVISION}' "
                            f"WHERE alembic_version.version_num = '{PRIOR_REVISION}'")
    assert f"-- Running upgrade {PRIOR_REVISION} -> {RADAR_REVISION}" in sql
    assert sql.count(" UNIQUE (") == 5 and "UNIQUE INDEX" not in sql


def test_migration_is_the_single_head_after_recommendation_outcomes() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()                             # only the script location matters here
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    scripts = ScriptDirectory.from_config(cfg)
    rev = scripts.get_revision(RADAR_REVISION)
    assert rev.down_revision == PRIOR_REVISION
    assert len(scripts.get_heads()) == 1
