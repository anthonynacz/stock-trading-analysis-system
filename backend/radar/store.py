"""Postgres storage for the Momentum Radar (backend/radar/PORT_SPEC.md section 4).

The GitHub version kept its tables as files on a git branch and wrote each tick as one delta. Here they
are the `radar_*` tables in Vela's Postgres, and this module is the only one that talks to them. It keeps
the delta's guarantees:

- **One transaction per write call.** A tick's snapshot, engine document, appended rows and baseline
  packs land together or not at all, so the page never shows a state whose events are missing.
- **Idempotent re-runs.** Appended rows go in with ON CONFLICT DO NOTHING on their unique keys
  ((tick, ticker), event id, (tick, run_id)), so committing the same tick twice changes nothing.
- **The documents keep their shapes.** state.json, engine.json and the JSONL rows are stored and handed
  back in the github-spec section 6 shapes (timestamps as "YYYY-MM-DDTHH:MM:SSZ"), because the engine,
  the API and the page consume them unchanged.

It is synchronous on purpose: the worker and the tick subprocess are plain blocking processes with no
event loop, so the backend's asyncpg engine does not fit them. Production uses psycopg2 with a URL derived
from settings.DATABASE_URL; tests pass any SQLAlchemy URL, normally SQLite.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import create_engine, delete, inspect, null, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.schema import Table

from db.models import (
    RadarBackupManifest,
    RadarBaseline,
    RadarEvent,
    RadarHousekeepingRun,
    RadarMemberTick,
    RadarOptionMetric,
    RadarRuntime,
    RadarScanLog,
    RadarSnapshot,
    RadarTableMetric,
)

logger = logging.getLogger(__name__)

SINGLETON_ID = 1
PACK_KINDS = ("main", "extra")
# The GitHub version replaced its one pack file per kind every day. Here packs are keyed by session, so
# older ones are pruned when a new one is written: a few days back covers the probe, which ranks thin names
# from "the stored pack", over a long weekend.
PACK_KEEP_DAYS = 10
CONNECT_TIMEOUT_S = 10   # a tick has a hard deadline; an unreachable database must fail fast, not hang

_SNAPSHOT: Table = RadarSnapshot.__table__
_RUNTIME: Table = RadarRuntime.__table__
_BASELINES: Table = RadarBaseline.__table__
_MEMBER_TICKS: Table = RadarMemberTick.__table__
_EVENTS: Table = RadarEvent.__table__
_SCAN_LOG: Table = RadarScanLog.__table__
_OPTIONS: Table = RadarOptionMetric.__table__

# Every radar table; the tests build their SQLite schema from this and schema_ready() checks it.
RADAR_TABLES: tuple[Table, ...] = tuple(m.__table__ for m in (
    RadarSnapshot, RadarRuntime, RadarBaseline, RadarMemberTick, RadarEvent, RadarScanLog,
    RadarBackupManifest, RadarHousekeepingRun, RadarTableMetric, RadarOptionMetric))


# ---------------------------------------------------------------- URL and value conversion

def sync_url(url: str) -> str:
    """The psycopg2 form of an asyncpg or driverless Postgres URL (settings.DATABASE_URL is the backend's
    asyncpg URL, which a sync engine cannot use); any other URL is returned unchanged."""
    u = make_url(url)
    if u.drivername in ("postgresql+asyncpg", "postgresql", "postgres"):
        u = u.set(drivername="postgresql+psycopg2")
    return u.render_as_string(hide_password=False)


def default_url() -> str:
    from config import settings   # lazy: tests and tools that pass a URL never need the app settings
    return sync_url(settings.DATABASE_URL)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "tolist"):     # numpy scalars and arrays
        return obj.tolist()
    raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")


def _json_dumps(doc: Any) -> str:
    # allow_nan=False: Postgres rejects NaN in a json value, so SQLite (the tests) must reject it too.
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=_json_default)


def _utc(value: Any) -> datetime | None:
    """An aware UTC datetime from a github-spec timestamp ("...Z") or a datetime (naive means UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"not a timestamp: {value!r}")
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """Back to the github-spec spelling. SQLite returns naive datetimes; everything stored is UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _float(value: Any) -> float | None:
    """Plain float, None for missing or non-finite values (the tick's jsonable() rule, and SQLite would
    store a NaN as NULL anyway)."""
    if value is None:
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return int(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required(doc: dict, key: str, what: str) -> Any:
    value = doc.get(key)
    if value is None or value == "":
        raise ValueError(f"{what} row without {key!r}: {str(doc)[:200]}")
    return value


# ---------------------------------------------------------------- row mapping (github-spec section 6)

def _member_values(row: dict) -> dict:
    return {"tick": _utc(_required(row, "tick", "member_ticks")),
            "session_date": _day(_required(row, "session", "member_ticks")),
            "slot": int(_required(row, "slot", "member_ticks")),
            "ticker": str(_required(row, "ticker", "member_ticks")),
            "role": row.get("role"), "dir": row.get("dir"), "state": row.get("state"),
            "price": _float(row.get("price")), "chg_day_pct": _float(row.get("chg_day_pct")),
            "chg_5m_pct": _float(row.get("chg_5m_pct")),
            "move_since_entry_pct": _float(row.get("move_since_entry_pct")),
            "vol_5m": _int(row.get("vol_5m")), "intensity": _float(row.get("intensity")),
            "signals": row.get("signals")}


def _member_dict(r: Any) -> dict:
    return {"v": 1, "tick": _iso(r.tick), "session": r.session_date.isoformat(), "slot": r.slot,
            "ticker": r.ticker, "role": r.role, "dir": r.dir, "state": r.state, "price": r.price,
            "chg_day_pct": r.chg_day_pct, "chg_5m_pct": r.chg_5m_pct,
            "move_since_entry_pct": r.move_since_entry_pct, "vol_5m": r.vol_5m, "intensity": r.intensity,
            "signals": r.signals}


def _event_values(ev: dict) -> dict:
    return {"id": str(_required(ev, "id", "events")),
            "ts": _utc(_required(ev, "ts", "events")),
            "session_date": _day(_required(ev, "session", "events")),
            "slot": int(_required(ev, "slot", "events")),
            "ticker": str(_required(ev, "ticker", "events")),
            "type": str(_required(ev, "type", "events")),
            "dir": ev.get("dir"), "price": _float(ev.get("price")), "intensity": _float(ev.get("intensity")),
            "reason": ev.get("reason"), "detail": ev.get("detail"), "episode": _int(ev.get("episode")),
            "held_min": _int(ev.get("held_min")), "move_since_entry_pct": _float(ev.get("move_since_entry_pct")),
            "late": bool(ev.get("late", False)), "signals": ev.get("signals"),
            "params_version": ev.get("params_version")}


def _event_dict(r: Any) -> dict:
    return {"v": 1, "id": r.id, "ts": _iso(r.ts), "session": r.session_date.isoformat(), "slot": r.slot,
            "ticker": r.ticker, "type": r.type, "dir": r.dir, "price": r.price, "intensity": r.intensity,
            "reason": r.reason, "detail": r.detail, "episode": r.episode, "held_min": r.held_min,
            "move_since_entry_pct": r.move_since_entry_pct, "late": bool(r.late), "signals": r.signals,
            "params_version": r.params_version}


def _scan_values(row: dict) -> dict:
    return {"tick": _utc(_required(row, "tick", "scan_log")),
            "run_id": str(_required(row, "run_id", "scan_log")),
            "written_at": _utc(row.get("written_at")), "session_date": _day(row.get("session")),
            "phase": row.get("phase"), "status": row.get("status"), "row": row}


def _scan_dict(r: Any) -> dict:
    if isinstance(r.row, dict):
        return r.row
    return {"v": 1, "tick": _iso(r.tick), "run_id": r.run_id, "written_at": _iso(r.written_at),
            "session": r.session_date.isoformat() if r.session_date else None, "phase": r.phase,
            "status": r.status}


def _pack_values(packs: dict[str, tuple[str, str, bytes]] | None) -> list[dict]:
    out = []
    for kind, (session_date, params_version, gz) in (packs or {}).items():
        if kind not in PACK_KINDS:
            raise ValueError(f"unknown baseline pack kind {kind!r}; expected one of {PACK_KINDS}")
        if not isinstance(gz, (bytes, bytearray, memoryview)) or not gz:
            raise ValueError(f"baseline pack {kind!r} is not gzip bytes")
        out.append({"session_date": _day(session_date), "kind": kind, "params_version": str(params_version),
                    "pack_gz": bytes(gz)})
    return out


# ---------------------------------------------------------------- the store

class RadarStore:
    """Every read and write of the radar tables (PORT_SPEC section 4, pinned API).

    Besides the pinned methods it exposes `engine` (for radar.housekeeping, which runs its own SQL on the
    housekeeping tables), `is_postgres`, `schema_ready()` and `close()`.
    """

    def __init__(self, url: str | None = None) -> None:
        url = sync_url(url) if url else default_url()
        backend = make_url(url).get_backend_name()
        if backend not in ("postgresql", "sqlite"):
            raise ValueError(f"RadarStore supports postgresql and sqlite URLs, not {backend!r}")
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "json_serializer": _json_dumps}
        if backend == "postgresql":
            # Small pools: the worker, each tick subprocess and the API each hold their own.
            kwargs.update(pool_size=3, max_overflow=4, pool_recycle=1800,
                          connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "application_name": "vela-radar"})
        self.engine: Engine = create_engine(url, **kwargs)
        self._insert: Callable[..., Any] = postgresql.insert if backend == "postgresql" else sqlite.insert
        self._lock_conns: dict[int, Connection] = {}
        self._lock_guard = threading.Lock()

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    # ------------------------------------------------------------ reads

    def load_state(self) -> dict:
        """The published state.json document; {} before the first tick."""
        with self.engine.connect() as conn:
            doc = conn.execute(select(_SNAPSHOT.c.state).where(_SNAPSHOT.c.id == SINGLETON_ID)).scalar()
        return doc if isinstance(doc, dict) else {}

    def load_engine_doc(self) -> dict:
        """The engine.json document the next tick resumes from; {} before the first tick."""
        with self.engine.connect() as conn:
            doc = conn.execute(select(_RUNTIME.c.engine).where(_RUNTIME.c.id == SINGLETON_ID)).scalar()
        return doc if isinstance(doc, dict) else {}

    def load_live_health(self) -> dict | None:
        """The source health a running (or killed) tick reached, or None when the last tick committed."""
        with self.engine.connect() as conn:
            doc = conn.execute(select(_RUNTIME.c.source_health_live)
                               .where(_RUNTIME.c.id == SINGLETON_ID)).scalar()
        return doc if isinstance(doc, dict) else None

    def load_pack(self, session_date: str, kind: str) -> tuple[str | None, bytes | None]:
        """(params_version, gzip bytes) of that session's pack, or (None, None). The caller decides
        whether the params_version is still current."""
        with self.engine.connect() as conn:
            row = conn.execute(select(_BASELINES.c.params_version, _BASELINES.c.pack_gz)
                               .where(_BASELINES.c.session_date == _day(session_date), _BASELINES.c.kind == kind)
                               ).first()
        if row is None:
            return None, None
        return row.params_version, bytes(row.pack_gz)

    def recent_events(self, *, since: datetime | None = None, ticker: str | None = None,
                      limit: int = 500, types: tuple[str, ...] | None = None) -> list[dict]:
        """Events newest first (ts, then id), optionally from `since` (inclusive), for one ticker and of
        some types (ENTER, EXIT)."""
        stmt = select(_EVENTS)
        if since is not None:
            stmt = stmt.where(_EVENTS.c.ts >= _utc(since))
        if ticker:
            stmt = stmt.where(_EVENTS.c.ticker == ticker.strip().upper())
        if types:
            stmt = stmt.where(_EVENTS.c.type.in_(list(types)))
        stmt = stmt.order_by(_EVENTS.c.ts.desc(), _EVENTS.c.id.desc()).limit(max(0, int(limit)))
        with self.engine.connect() as conn:
            return [_event_dict(r) for r in conn.execute(stmt)]

    def member_ticks(self, ticker: str, session_date: str) -> list[dict]:
        """One ticker's member/heating rows of a session, oldest first (the detail chart's series)."""
        stmt = (select(_MEMBER_TICKS)
                .where(_MEMBER_TICKS.c.ticker == ticker.strip().upper(),
                       _MEMBER_TICKS.c.session_date == _day(session_date))
                .order_by(_MEMBER_TICKS.c.tick))
        with self.engine.connect() as conn:
            return [_member_dict(r) for r in conn.execute(stmt)]

    def scan_log_tail(self, n: int = 50) -> list[dict]:
        """The last n scan_log rows (the full github-spec documents), newest first."""
        stmt = select(_SCAN_LOG).order_by(_SCAN_LOG.c.tick.desc(), _SCAN_LOG.c.id.desc()).limit(max(0, int(n)))
        with self.engine.connect() as conn:
            return [_scan_dict(r) for r in conn.execute(stmt)]

    # ------------------------------------------------------------ writes (one transaction per call)

    def commit_tick(self, *, state: dict, engine_doc: dict, member_rows: list[dict], events: list[dict],
                    scan_row: dict, packs: dict[str, tuple[str, str, bytes]] | None = None) -> None:
        """Publish one tick: snapshot and engine document replaced, rows appended (a re-run of the same
        tick inserts nothing twice), packs {kind: (session_date, params_version, gz)} upserted, and the
        live source health cleared because engine_doc now carries it."""
        # Map everything first, so a malformed row fails before the transaction starts.
        members = [_member_values(r) for r in member_rows]
        evs = [_event_values(e) for e in events]
        scan = _scan_values(scan_row)
        pack_rows = _pack_values(packs)
        now = _now()
        t0 = time.perf_counter()
        with self.engine.begin() as conn:
            self._put_state(conn, state, now)
            self._put_runtime(conn, now, engine=engine_doc, source_health_live=null())
            self._insert_new(conn, _MEMBER_TICKS, members, ("tick", "ticker"))
            self._insert_new(conn, _EVENTS, evs, ("id",))
            self._insert_new(conn, _SCAN_LOG, [scan], ("tick", "run_id"))
            self._put_packs(conn, pack_rows, now)
        logger.debug("radar tick %s committed: %d member rows, %d events, packs %s in %d ms",
                     scan_row.get("tick"), len(members), len(evs), [p["kind"] for p in pack_rows],
                     int((time.perf_counter() - t0) * 1000))

    def write_live_health(self, health: dict) -> None:
        """The Fetcher's on_health: its own short transaction after every fetch call, so a tick killed
        on timeout still leaves the breaker progress for the worker's write_failure."""
        with self.engine.begin() as conn:
            self._put_runtime(conn, _now(), source_health_live=health)

    def write_failure(self, *, state: dict, engine_doc: dict, scan_row: dict) -> None:
        """The worker's record of a failed or timed-out tick (port of Loop._write_failure's write): the
        error snapshot, the engine document with the live health merged in (which clears the live copy),
        and the scan_log row. An empty state or engine_doc leaves that document untouched, as the warmup
        path does not republish the snapshot."""
        scan = _scan_values(scan_row)
        now = _now()
        with self.engine.begin() as conn:
            if state:
                self._put_state(conn, state, now)
            if engine_doc:
                self._put_runtime(conn, now, engine=engine_doc, source_health_live=null())
            self._insert_new(conn, _SCAN_LOG, [scan], ("tick", "run_id"))

    def get_alert_cursor(self) -> str | None:
        """The last radar event id dispatched to alerts, or None before the first dispatch."""
        with self.engine.connect() as conn:
            return conn.execute(select(_RUNTIME.c.alert_cursor).where(_RUNTIME.c.id == SINGLETON_ID)).scalar()

    def set_alert_cursor(self, event_id: str) -> None:
        with self.engine.begin() as conn:
            self._put_runtime(conn, _now(), alert_cursor=str(event_id))

    # ------------------------------------------------------------ "Scan now" requests

    def get_scan_request(self) -> dict | None:
        """The latest "Scan now" request (pending, running, done, refused or error), or None."""
        with self.engine.connect() as conn:
            doc = conn.execute(select(_RUNTIME.c.scan_request).where(_RUNTIME.c.id == SINGLETON_ID)).scalar()
        return doc if isinstance(doc, dict) else None

    def put_scan_request(self, doc: dict) -> None:
        with self.engine.begin() as conn:
            self._put_runtime(conn, _now(), scan_request=doc)

    # ------------------------------------------------------------ option metrics (radar/options.py)

    def put_option_metrics(self, metrics: dict[str, dict], as_of: datetime, keep_days: int = 7) -> None:
        """Upsert one row per ticker and prune rows older than keep_days (the table only ever holds
        recent radar names)."""
        now = _now()
        with self.engine.begin() as conn:
            for ticker, doc in metrics.items():
                stmt = self._insert(_OPTIONS).values(ticker=ticker, as_of=as_of, metrics=doc, updated_at=now)
                conn.execute(stmt.on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={k: stmt.excluded[k] for k in ("as_of", "metrics", "updated_at")}))
            conn.execute(delete(_OPTIONS).where(_OPTIONS.c.as_of < now - timedelta(days=keep_days)))

    def load_option_metrics(self, tickers: list[str] | None = None) -> dict[str, dict]:
        """{ticker: metrics} for `tickers` (all rows when None); tickers without a row are left out."""
        q = select(_OPTIONS.c.ticker, _OPTIONS.c.metrics)
        if tickers is not None:
            if not tickers:
                return {}
            q = q.where(_OPTIONS.c.ticker.in_(list(tickers)))
        with self.engine.connect() as conn:
            return {r.ticker: r.metrics for r in conn.execute(q) if isinstance(r.metrics, dict)}

    # ------------------------------------------------------------ worker support

    def try_advisory_lock(self, key: int) -> bool:
        """Take the Postgres session-level advisory lock `key` on a dedicated connection that this
        store keeps open; the lock lives as long as that connection (released by close() or process
        exit). Calling again re-checks the connection: True while it is alive, and a dropped connection
        is replaced by a fresh attempt. False when another session holds the lock or the database cannot
        be reached (the worker retries either way). Always True on SQLite (tests, single process)."""
        if not self.is_postgres:
            return True
        key = int(key)
        with self._lock_guard:
            conn = self._lock_conns.get(key)
            if conn is not None:
                try:
                    conn.execute(text("SELECT 1"))
                    return True
                except SQLAlchemyError as e:
                    logger.warning("radar advisory lock %s: holding connection lost (%s); re-acquiring", key, e)
                    self._drop_lock_conn(key)
            conn, got = None, False
            try:
                # AUTOCOMMIT: the connection idles for hours; an open transaction would hold back VACUUM.
                conn = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
                got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
            except SQLAlchemyError as e:
                logger.warning("radar advisory lock %s: database unavailable (%s)", key, e)
            finally:
                if not got and conn is not None:
                    conn.close()
            if got:
                self._lock_conns[key] = conn
            return got

    def ping(self) -> bool:
        """True when the database answers; never raises."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:  # noqa: BLE001 - a health probe reports, it does not fail
            logger.warning("radar store ping failed: %s: %s", type(e).__name__, e)
            return False

    def schema_ready(self) -> bool:
        """True once every radar table exists (the backend container runs the migrations; the worker
        waits for them)."""
        try:
            names = set(inspect(self.engine).get_table_names())
        except SQLAlchemyError as e:
            logger.info("radar schema check failed: %s", e)
            return False
        return all(t.name in names for t in RADAR_TABLES)

    def close(self) -> None:
        """Release the advisory locks this store holds and dispose of the connection pool."""
        with self._lock_guard:
            for key in list(self._lock_conns):
                self._drop_lock_conn(key)
        self.engine.dispose()

    # ------------------------------------------------------------ statement helpers

    def _drop_lock_conn(self, key: int) -> None:
        conn = self._lock_conns.pop(key, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - the connection is already gone
                pass

    def _put_state(self, conn: Connection, state: dict, now: datetime) -> None:
        stmt = self._insert(_SNAPSHOT).values(id=SINGLETON_ID, state=state, updated_at=now)
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["id"], set_={"state": stmt.excluded.state, "updated_at": stmt.excluded.updated_at}))

    def _put_runtime(self, conn: Connection, now: datetime, **fields: Any) -> None:
        """Upsert the runtime singleton, changing only `fields` on an existing row (the alert cursor
        survives a tick commit, the engine document survives a live-health write)."""
        stmt = self._insert(_RUNTIME).values(id=SINGLETON_ID, updated_at=now, **fields)
        set_ = {k: stmt.excluded[k] for k in (*fields, "updated_at")}
        conn.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=set_))

    def _insert_new(self, conn: Connection, table: Table, rows: list[dict], key: tuple[str, ...]) -> None:
        if rows:
            conn.execute(self._insert(table).on_conflict_do_nothing(index_elements=list(key)), rows)

    def _put_packs(self, conn: Connection, rows: list[dict], now: datetime) -> None:
        if not rows:
            return
        for row in rows:
            stmt = self._insert(_BASELINES).values(**row, created_at=now)
            conn.execute(stmt.on_conflict_do_update(
                index_elements=["session_date", "kind"],
                set_={k: stmt.excluded[k] for k in ("params_version", "pack_gz", "created_at")}))
        cutoff = max(r["session_date"] for r in rows) - timedelta(days=PACK_KEEP_DAYS)
        conn.execute(delete(_BASELINES).where(_BASELINES.c.session_date < cutoff))
