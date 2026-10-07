"""Momentum Radar housekeeping: measure -> classify -> archive to verified monthly gz backups -> delete ->
manifest -> retention -> VACUUM -> run records (PORT_SPEC section 6).

Ported from the GitHub version's ``radar/storage/housekeeping.py`` + ``tables.py``. The hot tables now live in
Postgres instead of JSONL files on a git branch; what is kept is the backup format and its safety contract:

* backup partitions (``BACKUP_DIR/<table>/<YYYY-MM>.jsonl.gz``) are canonical: rows sorted by (ts, key),
  de-duplicated by the table's natural key, one canonical JSON document per line, deterministic gzip. Archiving
  the same rows again therefore produces the same bytes: re-runs are idempotent and never duplicate a row;
* a partition is written to a temp file, fsynced, re-read and verified from disk, then renamed into place,
  BEFORE any of its rows is deleted from Postgres. A crash at any step leaves every row in the hot table, in a
  partition, or in both, never in neither, and the next run converges (identical re-archive, or heal);
* the files are the source of truth and ``radar_backup_manifest`` is an index that every run reconciles with
  them. A corrupt partition, or a missing one for a live month, freezes its table (status ERROR): nothing of
  that table is archived or deleted until a human looks;
* retention is 3 calendar months, row level, UTC, with month-end clamping (``retention_cutoff``). It runs on
  every apply run, degraded or not, over the hot rows, the partitions (whole months, the boundary month row by
  row), the manifest entries and the two ops tables.

The module reaches the radar tables through SQLAlchemy reflection, so it depends on the PORT_SPEC section 4 table
and column names only (not on ORM class names), and it runs on SQLite in tests. Postgres-only statements
(advisory lock, ``pg_total_relation_size``, ``VACUUM (ANALYZE)``) are guarded by the dialect.

CLI: ``python -m radar.housekeeping run|query|restore|verify ...``. Everything the radar produces is educational
analysis, not financial advice; nothing here places trades.
"""
from __future__ import annotations

import argparse
import calendar
import glob
import gzip
import hashlib
import io
import json
import logging
import math
import os
import re
import statistics
import sys
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from sqlalchemy import MetaData, Table, delete, func, insert, select, text, update
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError

if TYPE_CHECKING:  # pragma: no cover - typing only; radar.store is imported lazily by the caller
    from radar.store import RadarStore

logger = logging.getLogger(__name__)

UTC = timezone.utc
SCHEMA_VERSION = 1
KiB, MiB, GiB = 1024, 1024 ** 2, 1024 ** 3
TMP_SUFFIX = ".tmp-hk"
PARTITION_NAME = re.compile(r"^(\d{4}-\d{2})\.jsonl\.gz$")

WARN_RATIO = 0.8              # any metric at >= 80% of its limit -> WARN (no action)
LATENCY_MIN_ROW_RATIO = 0.2   # the latency rule counts only at >= 20% of max_rows (noise guard on small tables)
WRITE_P95_MAX_MS = 750.0      # p95 of the tick's DB write; volume-dependent part of the write path
WRITE_P95_MIN_BYTES_RATIO = 0.5  # ... charged only to per-tick tables at >= 50% of max_bytes (the big ones)
WRITE_WINDOW = 50             # newest scan_log rows used for the write p95
WRITE_MIN_SAMPLES = 10
LOCK_KEY = 771235             # pg advisory lock "radar.housekeeping" (the worker holds 771234)
VACUUM_MIN_ROWS = 10_000      # VACUUM (ANALYZE) after deleting at least this many rows ...
VACUUM_MIN_RATIO = 0.10       # ... or this share of the table, whichever is larger
PIPELINE_PHASE = "radar_housekeeping"
MODES = ("apply", "dry-run")
TRIGGERS = ("schedule", "manual", "worker_request")
STEPS = ("partitions_written", "rows_deleted", "manifest_written", "retention_done")

MANIFEST_TABLE = "radar_backup_manifest"
METRICS_TABLE = "radar_table_metrics"
RUNS_TABLE = "radar_housekeeping_runs"
SCAN_LOG_TABLE = "radar_scan_log"
# Ops tables get row-level retention only (they are small; their history is the housekeeping record itself).
RETENTION_ONLY: dict[str, str] = {METRICS_TABLE: "measured_at", RUNS_TABLE: "started_at"}


class HousekeepingError(RuntimeError):
    """Housekeeping cannot run safely (tables missing, backup directory not mounted, bad store)."""


class SimulatedCrash(RuntimeError):
    """Raised by tests from ``checkpoint`` to simulate a process dying between two steps."""


class PartitionCorrupt(RuntimeError):
    """A backup partition does not decompress, is not canonical, or lost a row it must contain."""


class PostConditionError(RuntimeError):
    """A delete did not remove exactly the rows that were backed up; the transaction is rolled back."""


class RestoreError(RuntimeError):
    """A partition cannot be restored because it is missing or differs from the manifest."""


def checkpoint(step: str) -> None:
    """Crash-injection point between the apply steps (STEPS); tests monkeypatch it."""


# ---------------------------------------------------------------- policy

@dataclass(frozen=True)
class TablePolicy:
    """Housekeeping policy of one hot table.

    max_rows / max_bytes / max_lookup_ms / hot_days are the PORT_SPEC section 6 defaults. min_hot_hours and
    target_ratio come from the GitHub ``tables.py``: once a table is archived, the squeeze keeps archiving the
    oldest rows until it is at ``target_ratio`` of its limits (hysteresis, so it does not trigger again the next
    night), but never touches rows newer than ``min_hot_hours`` (the current session and the warm start).
    """

    table: str
    ts: str                        # UTC timestamp column used for windows, months and retention
    key: tuple[str, ...]           # natural key: partitions are de-duplicated on it
    lookups: tuple[str, ...]       # standard lookups timed by measure (see _lookup_statements)
    max_rows: int
    max_bytes: int
    max_lookup_ms: float
    hot_days: float
    min_hot_hours: float
    target_ratio: float
    surrogate: str | None = "id"   # DB-only bigint id, left out of backups so a partition is a pure function of
                                   # row content; None when the id column is the natural key (events)
    per_tick: bool = True          # written on every tick: the write-p95 rule applies

    @property
    def short(self) -> str:
        return self.table.removeprefix("radar_")


_TICKER_LOOKUPS = ("latest_by_ticker", "series_ticker_session", "rows_on_day", "rows_last_24h")

POLICIES: dict[str, TablePolicy] = {p.table: p for p in (
    TablePolicy("radar_member_ticks", "tick", ("tick", "ticker"), _TICKER_LOOKUPS,
                max_rows=2_000_000, max_bytes=1 * GiB, max_lookup_ms=150, hot_days=14,
                min_hot_hours=20, target_ratio=0.4),
    TablePolicy("radar_events", "ts", ("id",), _TICKER_LOOKUPS,
                max_rows=200_000, max_bytes=256 * MiB, max_lookup_ms=100, hot_days=45,
                min_hot_hours=72, target_ratio=0.5, surrogate=None),
    TablePolicy("radar_scan_log", "tick", ("tick", "run_id"), ("latest_rows", "rows_on_day", "rows_last_24h"),
                max_rows=300_000, max_bytes=512 * MiB, max_lookup_ms=100, hot_days=14,
                min_hot_hours=20, target_ratio=0.5),
)}


def resolve_table(name: str, policies: Mapping[str, TablePolicy] | None = None) -> str:
    """Full table name for ``radar_events`` or its short form ``events``; ValueError when unknown."""
    policies = POLICIES if policies is None else policies
    for cand in (name, f"radar_{name}"):
        if cand in policies:
            return cand
    raise ValueError(f"unknown radar table {name!r}; expected one of {sorted(policies)}")


# ---------------------------------------------------------------- primitives

def canon(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def utc(dt: datetime) -> datetime:
    """Aware UTC datetime; naive values are UTC (SQLite returns timestamps without a zone)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def parse_ts(v: object) -> datetime | None:
    if isinstance(v, datetime):
        return utc(v)
    if not isinstance(v, str):
        return None
    try:
        return utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
    except ValueError:
        return None


def iso(dt: datetime) -> str:
    """Deterministic UTC text: seconds, plus microseconds only when there are any."""
    dt = utc(dt)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{dt.microsecond:06d}Z" if dt.microsecond else f"{base}Z"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def retention_cutoff(as_of: date, months: int = 3) -> datetime:
    """00:00Z of the same calendar day `months` months earlier, the day clamped to the month end.

    2026-09-27 -> 2026-06-27; 2026-05-31 -> 2026-02-28; 2024-05-31 -> 2024-02-29. A row is kept iff ts >= cutoff.
    Calendar months, not 90 days, because that is what "3 months" means to the user and it lines up with the
    monthly partitions.
    """
    y, m = as_of.year, as_of.month - months
    while m <= 0:
        m += 12
        y -= 1
    return datetime(y, m, min(as_of.day, calendar.monthrange(y, m)[1]), tzinfo=UTC)


def month_of(dt: datetime) -> str:
    dt = utc(dt)
    return f"{dt.year:04d}-{dt.month:02d}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    y, m = int(month[:4]), int(month[5:7])
    start = datetime(y, m, 1, tzinfo=UTC)
    return start, datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=UTC)


def gz_deterministic(content: bytes) -> bytes:
    """gzip with a zero mtime and no file name, so equal content gives equal bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0, filename="") as g:
        g.write(content)
    return buf.getvalue()


def read_file(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def jsonl_lines(text_: str) -> list[str]:
    """JSONL lines split on "\\n" only, never str.splitlines(): rows are written with ensure_ascii=False, so
    U+2028, U+2029 and U+0085 occur literally inside rows (SPEC 12.4). The empty tail after the last newline is
    dropped; any other blank line is kept so validation can reject it."""
    lines = text_.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def fmt_bytes(n: float) -> str:
    if n >= GiB:
        return f"{n / GiB:.2f} GiB"
    return f"{n / MiB:.2f} MiB" if n >= MiB else f"{n / KiB:.0f} KiB"


def _fsync_dir(path: str) -> None:
    """Make a rename durable before rows are deleted elsewhere (POSIX only; Windows cannot open directories)."""
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + TMP_SUFFIX
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def json_value(v: Any) -> Any:
    """A DB value as JSON: timestamps as UTC ISO text, dates as YYYY-MM-DD, Decimal as float."""
    if isinstance(v, datetime):
        return iso(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def row_key(pol: TablePolicy, row: Mapping[str, Any]) -> str | None:
    vals = [row.get(k) for k in pol.key]
    return None if any(v is None for v in vals) else "|".join(str(v) for v in vals)


def sort_key(pol: TablePolicy, row: Mapping[str, Any]) -> tuple[datetime, str]:
    return parse_ts(row[pol.ts]), row_key(pol, row)


def where_matches(value: object, want: str) -> bool:
    """``--where k=v``: v may be the Python or the JSON spelling of the value (False/false, None/null,
    NVDA/"NVDA"), SPEC 12.4. A missing field matches null."""
    return want == str(value) or want == json.dumps(value, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- database access

def _config_defaults() -> tuple[str, int]:
    """(BACKUP_DIR, retention_months) from radar.config, imported only when the caller did not pass them."""
    from radar import config as radar_config

    return radar_config.BACKUP_DIR, int(radar_config.HOUSEKEEPING.get("retention_months", 3))


def _engine_for(store: Any) -> tuple[Engine, Callable[[], None]]:
    """(engine, close). Housekeeping runs its SQL on the store's sync engine (RadarStore.engine). Without a store
    it opens a RadarStore on the app database (URL derived by radar.store) and closes it when done, so the
    nightly run and the API need not pass one."""
    if store is None:
        from radar.store import RadarStore

        own = RadarStore()
        return own.engine, own.close
    eng = getattr(store, "engine", None)
    if not isinstance(eng, Engine):
        raise HousekeepingError(f"{type(store).__name__} exposes no sync SQLAlchemy Engine as .engine")
    return eng, lambda: None


def _bind(col: Any, v: Any) -> Any:
    """Coerce a JSON-side value to the column's type (ISO text <-> datetime), whatever STORE chose."""
    if v is None:
        return None
    if isinstance(col.type, sqltypes.DateTime) and isinstance(v, str):
        return parse_ts(v)
    if isinstance(col.type, sqltypes.Date) and isinstance(v, str):
        return date.fromisoformat(v[:10])
    if isinstance(col.type, sqltypes.String) and isinstance(v, datetime):
        return iso(v)
    return v


def _values(t: Table, d: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _bind(t.c[k], v) for k, v in d.items() if k in t.c}


class _Db:
    """The radar tables as they exist in the database. Reflection keeps this module tied to the PORT_SPEC table
    and column names only, not to the ORM classes, and makes it work unchanged on SQLite in tests."""

    def __init__(self, engine: Engine, policies: Mapping[str, TablePolicy]) -> None:
        self.engine = engine
        self.dialect = engine.dialect.name
        names = sorted(set(policies) | {SCAN_LOG_TABLE, MANIFEST_TABLE, METRICS_TABLE, RUNS_TABLE})
        md = MetaData()
        try:
            md.reflect(bind=engine, only=names)
        except InvalidRequestError as e:   # a listed table does not exist
            raise HousekeepingError(f"radar tables missing ({e}); run `alembic upgrade head` first") from e
        self.tables = md.tables

    def t(self, name: str) -> Table:
        return self.tables[name]

    def quote(self, name: str) -> str:
        return self.engine.dialect.identifier_preparer.quote(name)


def id_column(t: Table) -> Any:
    """The primary-key column rows are deleted by (the bigint surrogate, or events' natural id)."""
    return list(t.primary_key.columns)[0]


def db_row(t: Table, pol: TablePolicy, r: Mapping[str, Any]) -> dict[str, Any]:
    """A hot row in its backup shape: every column but the surrogate id, as JSON values."""
    return {c.name: json_value(r[c.name]) for c in t.columns if c.name != pol.surrogate}


def _fetch(conn: Connection, t: Table, pol: TablePolicy, lo: datetime, hi: datetime) -> list[tuple[Any, dict]]:
    """[(delete id, backup row)] for lo <= ts < hi."""
    tsc, idc = t.c[pol.ts], id_column(t)
    rows = conn.execute(select(t).where(tsc >= lo, tsc < hi).order_by(tsc)).mappings()
    return [(r[idc.name], db_row(t, pol, r)) for r in rows]


def _count(conn: Connection, t: Table, *where: Any) -> int:
    return int(conn.execute(select(func.count()).select_from(t).where(*where)).scalar() or 0)


def _table_bytes(conn: Connection, db: _Db, name: str) -> int | None:
    """Total bytes of the table with its indexes and TOAST (pg_total_relation_size); SQLite via dbstat when the
    build has it, else None (the bytes rule then does not apply)."""
    try:
        if db.dialect == "postgresql":
            return int(conn.execute(text("SELECT pg_total_relation_size(CAST(:t AS regclass))"), {"t": name}).scalar())
        if db.dialect == "sqlite":
            v = conn.execute(text("SELECT SUM(pgsize) FROM dbstat WHERE name IN "
                                  "(SELECT name FROM sqlite_master WHERE tbl_name = :t)"), {"t": name}).scalar()
            return int(v) if v is not None else None
    except SQLAlchemyError:
        conn.rollback()
    return None


def _live_row_bytes(conn: Connection, db: _Db, t: Table, pol: TablePolicy, nbytes: int | None,
                    rows: int) -> float | None:
    """Bytes per live row, used by the squeeze and the bloat hint.

    On Postgres the relation keeps its size after DELETE + VACUUM (freed space is reused, not returned), so
    total bytes / rows would grow every night after an archive and the squeeze would run away toward the
    min_hot_hours floor. The live estimate is the average tuple size of the newest rows, scaled by the
    table's total/heap ratio (indexes and TOAST bloat roughly like the heap)."""
    if not rows or not nbytes:
        return None
    if db.dialect == "postgresql":
        try:
            avg_tuple = conn.execute(text(
                f"SELECT avg(pg_column_size(s.*)) FROM (SELECT * FROM {db.quote(pol.table)} "
                f"ORDER BY {db.quote(pol.ts)} DESC LIMIT 1000) s")).scalar()
            heap = conn.execute(text("SELECT pg_relation_size(CAST(:t AS regclass))"), {"t": pol.table}).scalar()
            if avg_tuple and heap:
                return (float(avg_tuple) + 4.0) * nbytes / float(heap)   # + 4 B line pointer
        except SQLAlchemyError:
            conn.rollback()
    return nbytes / rows


def _timed_query(conn: Connection, stmt: Any) -> float:
    """One cold lookup (execute + fetch every row), in ms. Tests monkeypatch it to simulate slow lookups."""
    t0 = time.perf_counter()
    conn.execute(stmt).fetchall()
    return (time.perf_counter() - t0) * 1000


def _lookup_statements(conn: Connection, t: Table, pol: TablePolicy) -> dict[str, Any]:
    """The standard lookups the API and engine run, parameterised from the newest row (its ticker and day, and
    the 24 hours before it), as in the GitHub storage design section 2.1."""
    tsc = t.c[pol.ts]
    newest = conn.execute(select(t).order_by(tsc.desc()).limit(1)).mappings().first()
    if newest is None:
        return {}
    last = utc(newest[pol.ts])
    day0 = datetime(last.year, last.month, last.day, tzinfo=UTC)
    day_range = (tsc >= day0, tsc < day0 + timedelta(days=1))
    ticker = newest.get("ticker")
    out: dict[str, Any] = {}
    for name in pol.lookups:
        if name in ("latest_by_ticker", "series_ticker_session") and "ticker" not in t.c:
            continue
        if name == "latest_by_ticker":
            out[name] = select(t).where(t.c.ticker == ticker).order_by(tsc.desc()).limit(20)
        elif name == "series_ticker_session":
            sess = newest.get("session_date")
            cond = (t.c.session_date == sess,) if "session_date" in t.c and sess is not None else day_range
            out[name] = select(t).where(t.c.ticker == ticker, *cond).order_by(tsc)
        elif name == "rows_on_day":
            out[name] = select(t).where(*day_range).order_by(tsc)
        elif name == "rows_last_24h":
            out[name] = select(t).where(tsc >= last - timedelta(hours=24)).order_by(tsc)
        elif name == "latest_rows":
            out[name] = select(t).order_by(tsc.desc()).limit(50)
        else:
            raise KeyError(name)
    return out


def write_p95_ms(scan_rows: Iterable[Any]) -> float | None:
    """p95 of ``row.ms.write`` (the tick's DB write time) over the given scan_log rows (newest 50).

    Fewer than WRITE_MIN_SAMPLES timed rows gives None (first ticks after a deploy carry no timing)."""
    vals = []
    for row in scan_rows:
        if isinstance(row, str):
            try:
                row = json.loads(row)
            except json.JSONDecodeError:
                continue
        ms = row.get("ms") if isinstance(row, dict) else None
        w = ms.get("write") if isinstance(ms, dict) else None
        if isinstance(w, (int, float)) and not isinstance(w, bool):
            vals.append(float(w))
    if len(vals) < WRITE_MIN_SAMPLES:
        return None
    vals.sort()
    return vals[round(0.95 * (len(vals) - 1))]


# ---------------------------------------------------------------- partitions + manifest

def partition_rel(table: str, month: str) -> str:
    return f"{table}/{month}.jsonl.gz"


def partition_path(backup_dir: str, table: str, month: str) -> str:
    return os.path.join(backup_dir, table, f"{month}.jsonl.gz")


def read_partition(path: str, pol: TablePolicy) -> list[dict]:
    """Decompress and validate: every line canonical JSON with key and ts, strictly increasing (ts, key)."""
    try:
        with open(path, "rb") as f:
            text_ = gzip.decompress(f.read()).decode("utf-8")
    except (OSError, EOFError, zlib.error, UnicodeDecodeError) as e:   # BadGzipFile is an OSError
        raise PartitionCorrupt(f"{path}: {e}") from e
    rows: list[dict] = []
    prev = None
    for n, line in enumerate(jsonl_lines(text_), 1):    # a blank line in the middle is still "bad JSON"
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            raise PartitionCorrupt(f"{path}:{n}: bad JSON") from e
        if not isinstance(r, dict) or canon(r) != line or row_key(pol, r) is None or parse_ts(r.get(pol.ts)) is None:
            raise PartitionCorrupt(f"{path}:{n}: not canonical / missing key or ts")
        k = sort_key(pol, r)
        if prev is not None and k <= prev:
            raise PartitionCorrupt(f"{path}:{n}: not strictly sorted / unique")
        prev = k
        rows.append(r)
    return rows


def partition_entry(pol: TablePolicy, month: str, rows: list[dict], blob: bytes, content: bytes,
                    now: datetime, run_id: str, prev: Mapping[str, Any] | None) -> dict:
    """The manifest row of one partition (radar_backup_manifest columns)."""
    prev = prev or {}
    return {
        "table_name": pol.table, "month": month, "path": partition_rel(pol.table, month),
        "rows": len(rows), "bytes": len(blob), "raw_bytes": len(content),
        "sha256": sha256(blob), "content_sha256": sha256(content),
        "min_ts": rows[0][pol.ts] if rows else None, "max_ts": rows[-1][pol.ts] if rows else None,
        "created_at": prev.get("created_at") or iso(now), "updated_at": iso(now),
        "writes": int(prev.get("writes") or 0) + 1, "last_run_id": run_id,
    }


def write_partition_verified(backup_dir: str, pol: TablePolicy, month: str, rows: list[dict],
                             must_contain: Iterable[dict], now: datetime, run_id: str,
                             prev: Mapping[str, Any] | None) -> tuple[dict, bool]:
    """Write a canonical partition via a temp file; re-read and verify it from disk before the atomic rename.

    Returns (manifest entry, changed). Identical content is not rewritten: that is the idempotency point.
    """
    path = partition_path(backup_dir, pol.table, month)
    content = "".join(canon(r) + "\n" for r in rows).encode("utf-8")
    if prev and prev.get("content_sha256") == sha256(content):
        current = read_file(path)
        if current is not None and sha256(current) == prev.get("sha256"):
            return dict(prev), False
    blob = gz_deterministic(content)
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = path + TMP_SUFFIX
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    try:
        back = read_partition(tmp, pol)
        if len(back) != len(rows):
            raise PartitionCorrupt(f"{tmp}: row count {len(back)} != {len(rows)}")
        index = {row_key(pol, r): canon(r) for r in back}
        for r in must_contain:
            if index.get(row_key(pol, r)) != canon(r):
                raise PartitionCorrupt(f"{tmp}: archived row {row_key(pol, r)} missing or different after write")
    except PartitionCorrupt:
        os.remove(tmp)
        raise
    os.replace(tmp, path)
    _fsync_dir(folder)
    return partition_entry(pol, month, rows, blob, content, now, run_id, prev), True


def load_manifest(conn: Connection, db: _Db) -> dict[str, dict]:
    """radar_backup_manifest as {"<table>/<YYYY-MM>": entry} with JSON values."""
    out: dict[str, dict] = {}
    for r in conn.execute(select(db.t(MANIFEST_TABLE))).mappings():
        e = {k: json_value(v) for k, v in r.items() if k != "id"}
        out[f"{e['table_name']}/{e['month']}"] = e
    return out


def save_manifest(conn: Connection, db: _Db, manifest: Mapping[str, dict], upserts: Iterable[str],
                  deletes: Iterable[str]) -> None:
    """Upsert the given keys from `manifest` and delete the others, inside the caller's transaction."""
    t = db.t(MANIFEST_TABLE)
    for key in sorted(set(deletes)):
        table, month = key.split("/")
        conn.execute(delete(t).where(t.c.table_name == table, t.c.month == month))
    for key in sorted(set(upserts) - set(deletes)):
        e = manifest[key]
        vals = _values(t, e)
        res = conn.execute(update(t).where(t.c.table_name == e["table_name"], t.c.month == e["month"]).values(**vals))
        if not res.rowcount:
            conn.execute(insert(t).values(**vals))


def partitions_on_disk(backup_dir: str) -> tuple[dict[str, str], list[str]]:
    """({"<table>/<YYYY-MM>": path}, stray *.jsonl.gz files that are not month partitions)."""
    found: dict[str, str] = {}
    stray: list[str] = []
    for p in sorted(glob.glob(os.path.join(backup_dir, "*", "*.jsonl.gz"))):
        table, name = os.path.basename(os.path.dirname(p)), os.path.basename(p)
        m = PARTITION_NAME.match(name)
        if m:
            found[f"{table}/{m.group(1)}"] = p
        else:
            stray.append(f"{table}/{name}")
    return found, stray


@dataclass
class Reconciled:
    repairs: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)   # table -> problem (that table is frozen)
    upserts: set[str] = field(default_factory=set)
    deletes: set[str] = field(default_factory=set)


def reconcile_manifest(backup_dir: str, manifest: dict[str, dict], policies: Mapping[str, TablePolicy],
                       now: datetime, run_id: str, cutoff: datetime) -> Reconciled:
    """Make the in-memory manifest agree with the files; the files are the source of truth.

    sha mismatch but a valid file -> rebuild the entry (an interrupted run wrote it); file not in the manifest ->
    add it; entry whose file is missing but whose month is past retention -> drop it (an interrupted purge);
    anything else (corrupt file, missing live month, stray file) -> error, which freezes that table.
    """
    rec = Reconciled()
    on_disk, stray = partitions_on_disk(backup_dir)
    for rel in stray:
        rec.errors.setdefault(rel.split("/")[0], f"unexpected file {rel}")
    for key in sorted(set(manifest) | set(on_disk)):
        table, month = key.split("/")
        pol = policies.get(table)
        if pol is None:
            if key in on_disk:
                rec.errors.setdefault(table, f"unknown table directory {table}/")
            continue
        if key not in on_disk:
            if month_bounds(month)[1] <= cutoff:
                manifest.pop(key)
                rec.deletes.add(key)
                rec.repairs.append(f"{key}: expired partition already deleted, entry dropped")
            else:
                rec.errors[table] = f"partition {key} is in the manifest but the file is missing"
            continue
        blob = read_file(on_disk[key]) or b""
        entry = manifest.get(key)
        if entry and entry.get("sha256") == sha256(blob):
            continue
        try:
            rows = read_partition(on_disk[key], pol)
        except PartitionCorrupt as e:
            rec.errors[table] = str(e)
            continue
        manifest[key] = partition_entry(pol, month, rows, blob, gzip.decompress(blob), now, run_id, entry)
        rec.upserts.add(key)
        rec.repairs.append(f"{key}: manifest entry {'rebuilt' if entry else 'added'} from file")
    return rec


def _cleanup_tmp(backup_dir: str) -> None:
    for p in glob.glob(os.path.join(backup_dir, "**", "*" + TMP_SUFFIX), recursive=True):
        os.remove(p)


# ---------------------------------------------------------------- measure / classify / plan

def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def classify(pol: TablePolicy, m: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """(status, DEGRADED reasons, WARN notes) per the GitHub storage design section 2.3 rules."""
    reasons: list[str] = []
    warns: list[str] = []

    def chk(label: str, val: float | None, lim: float | None, fmt: Callable[[float], str] = _fmt_num) -> None:
        if val is None or lim is None:
            return
        if val > lim:
            reasons.append(f"{label} {fmt(val)} > {fmt(lim)}")
        elif val >= WARN_RATIO * lim:
            warns.append(f"{label} {fmt(val)} >= {int(WARN_RATIO * 100)}% of {fmt(lim)}")

    chk("bytes", m["bytes"], pol.max_bytes, fmt_bytes)
    chk("rows", m["rows"], pol.max_rows)
    if m["rows"] >= LATENCY_MIN_ROW_RATIO * pol.max_rows:
        chk("lookup_ms", m["lookup_max_ms"], pol.max_lookup_ms)
    if (pol.per_tick and m["write_p95_ms"] is not None and m["bytes"] is not None
            and m["bytes"] >= WRITE_P95_MIN_BYTES_RATIO * pol.max_bytes):
        chk("write_p95_ms", m["write_p95_ms"], WRITE_P95_MAX_MS)
    live = m.get("bytes_live_est")
    if m["bytes"] is not None and m["bytes"] > pol.max_bytes and live is not None and live <= pol.max_bytes:
        warns.append(f"relation size includes free space left by earlier deletes (live data about "
                     f"{fmt_bytes(live)}); a manual VACUUM FULL returns it")
    return ("DEGRADED" if reasons else "WARN" if warns else "OK"), reasons, warns


@dataclass
class TablePlan:
    policy: TablePolicy
    metrics: dict
    status: str
    reasons: list[str]
    warnings: list[str]
    forced: bool = False
    error: str | None = None
    archive_before: datetime | None = None              # rows with cutoff <= ts < archive_before are archived
    archive_months: dict[str, int] = field(default_factory=dict)
    heal_ranges: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    archive_rows: int = 0
    heal_rows: int = 0
    purge_rows: int = 0
    rows_after: int | None = None
    archive_ids: list[Any] = field(default_factory=list)
    heal_ids: list[Any] = field(default_factory=list)
    vacuum: str | None = None

    @property
    def frozen(self) -> bool:
        return self.error is not None

    def freeze(self, error: str) -> None:
        """Never archive, heal or purge a table whose backups are unsafe."""
        self.error, self.status = error, "ERROR"
        self.archive_before, self.archive_months, self.heal_ranges = None, {}, {}
        self.archive_rows = self.heal_rows = self.purge_rows = 0

    def action(self) -> str:
        if self.frozen:
            return "frozen"
        parts = [a for a, n in (("archive", self.archive_rows), ("heal", self.heal_rows),
                                ("purge", self.purge_rows)) if n]
        return "+".join(parts) or "none"


class _Run:
    """One housekeeping pass. The step order is what makes a crash at any point recoverable."""

    def __init__(self, db: _Db, *, mode: str, trigger: str, now: datetime, cutoff: datetime, backup_dir: str,
                 policies: Mapping[str, TablePolicy], forced: set[str], run_id: str, repeats: int,
                 retention_months: int) -> None:
        self.db, self.engine = db, db.engine
        self.mode, self.trigger, self.now, self.cutoff = mode, trigger, now, cutoff
        self.backup_dir, self.policies, self.forced = backup_dir, policies, forced
        self.run_id, self.repeats, self.retention_months = run_id, repeats, retention_months
        self.manifest: dict[str, dict] = {}
        self.dirty: set[str] = set()
        self.removed: set[str] = set()
        self.rec = Reconciled()
        self.plans: dict[str, TablePlan] = {}
        self.ops_purge: dict[str, int] = {}

    # ---- planning (read only)

    def _write_p95(self, conn: Connection) -> float | None:
        t = self.db.t(SCAN_LOG_TABLE)
        if "row" not in t.c:
            return None
        rows = conn.execute(select(t.c["row"]).order_by(t.c.tick.desc()).limit(WRITE_WINDOW)).scalars().all()
        return write_p95_ms(rows)

    def _measure(self, conn: Connection, pol: TablePolicy, write_p95: float | None) -> dict:
        t = self.db.t(pol.table)
        tsc = t.c[pol.ts]
        rows = _count(conn, t)
        nbytes = _table_bytes(conn, self.db, pol.table)
        bpr = _live_row_bytes(conn, self.db, t, pol, nbytes, rows)
        lo, hi = conn.execute(select(func.min(tsc), func.max(tsc))).one()
        lookups: dict[str, float] = {}
        if rows:
            repeats = 1 if rows > 4 * pol.max_rows else max(1, self.repeats)
            stmts = _lookup_statements(conn, t, pol)
            samples: dict[str, list[float]] = {name: [] for name in stmts}
            # Rounds interleave the lookups, so no sample directly repeats the statement it follows (the
            # nearest a client gets to a cold run); the median of the rounds is kept, as on GitHub.
            for _ in range(repeats):
                for name, stmt in stmts.items():
                    samples[name].append(_timed_query(conn, stmt))
            lookups = {name: round(statistics.median(v), 3) for name, v in samples.items()}
        return {"rows": rows, "bytes": nbytes, "bytes_per_row": round(bpr, 1) if bpr else None,
                "bytes_live_est": int(bpr * rows) if bpr else None,
                "min_ts": iso(lo) if lo is not None else None, "max_ts": iso(hi) if hi is not None else None,
                "lookup_ms": lookups, "lookup_max_ms": max(lookups.values()) if lookups else 0.0,
                "write_p95_ms": write_p95 if pol.per_tick else None}

    def _month_counts(self, conn: Connection, t: Table, pol: TablePolicy, lo: datetime, hi: datetime) -> dict[str, int]:
        tsc = t.c[pol.ts]
        out: dict[str, int] = {}
        if lo >= hi:
            return out
        first = conn.execute(select(func.min(tsc)).where(tsc >= lo, tsc < hi)).scalar()
        if first is None:
            return out
        month = month_of(utc(first))
        while True:
            ms, me = month_bounds(month)
            if ms >= hi:
                return out
            n = _count(conn, t, tsc >= max(ms, lo), tsc < min(me, hi))
            if n:
                out[month] = n
            month = month_of(me)

    def _plan_table(self, conn: Connection, pol: TablePolicy, write_p95: float | None) -> TablePlan:
        t = self.db.t(pol.table)
        tsc = t.c[pol.ts]
        m = self._measure(conn, pol, write_p95)
        status, reasons, warns = classify(pol, m)
        plan = TablePlan(pol, m, status, reasons, warns, forced=pol.table in self.forced)
        if pol.table in self.rec.errors:
            plan.freeze(self.rec.errors[pol.table])
            return plan
        plan.purge_rows = _count(conn, t, tsc < self.cutoff)
        age_cut = self.now - timedelta(days=pol.hot_days)
        if status == "DEGRADED" or plan.forced:
            archive_before = age_cut
            n_archive = _count(conn, t, tsc >= self.cutoff, tsc < age_cut)
            remaining = m["rows"] - plan.purge_rows - n_archive
            limit = pol.target_ratio * pol.max_rows
            if m["bytes_per_row"]:
                limit = min(limit, pol.target_ratio * pol.max_bytes / m["bytes_per_row"])
            floor = self.now - timedelta(hours=pol.min_hot_hours)
            if remaining > limit and floor > age_cut:
                # squeeze: archive whole ticks, oldest first, until within target; never newer than the floor
                extra = math.ceil(remaining - limit)
                lo = max(age_cut, self.cutoff)
                b = conn.execute(select(tsc).where(tsc >= lo, tsc < floor).order_by(tsc)
                                 .offset(extra - 1).limit(1)).scalar()
                if b is None:
                    archive_before = floor
                else:
                    nxt = conn.execute(select(func.min(tsc)).where(tsc > b)).scalar()
                    archive_before = min(utc(nxt), floor) if nxt is not None else floor
            plan.archive_before = max(archive_before, age_cut)
            plan.archive_months = self._month_counts(conn, t, pol, self.cutoff, plan.archive_before)
            plan.archive_rows = sum(plan.archive_months.values())
        else:
            # heal: rows past hot_days that an interrupted run already put, identical, into a partition. A cheap
            # range check against the manifest comes first; the content comparison happens in apply.
            for _key, e in sorted(self.manifest.items()):
                if e["table_name"] != pol.table or not e.get("min_ts"):
                    continue
                lo = max(parse_ts(e["min_ts"]), self.cutoff)
                hi = min(parse_ts(e["max_ts"]) + timedelta(microseconds=1), age_cut)
                if lo < hi and _count(conn, t, tsc >= lo, tsc < hi):
                    plan.heal_ranges[e["month"]] = (lo, hi)
            if plan.heal_ranges:
                plan.heal_rows = sum(_count(conn, t, tsc >= lo, tsc < hi) for lo, hi in plan.heal_ranges.values())
        return plan

    def _partition_retention(self) -> list[tuple[str, str, TablePolicy, str, list[dict]]]:
        """(key, "delete"|"filter", policy, month, kept rows): months ending at or before the cutoff go whole,
        the boundary month is filtered row by row (deleted when nothing is left)."""
        out = []
        for key in sorted(self.manifest):
            e = self.manifest[key]
            pol = self.policies.get(e["table_name"])
            if pol is None or pol.table in self.rec.errors:
                continue
            start, end = month_bounds(e["month"])
            if end <= self.cutoff:
                out.append((key, "delete", pol, e["month"], []))
            elif start < self.cutoff:
                oldest = parse_ts(e.get("min_ts"))
                if oldest is not None and oldest >= self.cutoff:
                    continue
                rows = read_partition(partition_path(self.backup_dir, pol.table, e["month"]), pol)
                kept = [r for r in rows if parse_ts(r[pol.ts]) >= self.cutoff]
                if len(kept) != len(rows):
                    out.append((key, "filter" if kept else "delete", pol, e["month"], kept))
        return out

    # ---- apply steps

    def _archive_and_collect(self) -> list[str]:
        """Step 1: merge each archived month into its partition (verified on disk); collect the ids to delete."""
        written: list[str] = []
        with self.engine.connect() as conn:
            for plan in self.plans.values():
                if plan.frozen:
                    continue
                pol = plan.policy
                t = self.db.t(pol.table)
                if plan.archive_before is not None:
                    plan.archive_rows = 0
                    for month in sorted(plan.archive_months):
                        ms, me = month_bounds(month)
                        fetched = _fetch(conn, t, pol, max(ms, self.cutoff), min(me, plan.archive_before))
                        if not fetched:
                            continue
                        key = f"{pol.table}/{month}"
                        prev = self.manifest.get(key)
                        path = partition_path(self.backup_dir, pol.table, month)
                        existing = read_partition(path, pol) if prev and os.path.exists(path) else []
                        merged = {row_key(pol, r): r for r in existing}
                        for _, r in fetched:                     # the hot copy wins
                            merged[row_key(pol, r)] = r
                        rows = sorted((r for r in merged.values() if parse_ts(r[pol.ts]) >= self.cutoff),
                                      key=lambda r: sort_key(pol, r))
                        entry, changed = write_partition_verified(self.backup_dir, pol, month, rows,
                                                                  [r for _, r in fetched], self.now, self.run_id, prev)
                        self.manifest[key] = entry
                        if changed:
                            self.dirty.add(key)
                            written.append(key)
                        plan.archive_ids += [i for i, _ in fetched]
                    plan.archive_rows = len(plan.archive_ids)
                for month, (lo, hi) in sorted(plan.heal_ranges.items()):
                    index = {row_key(pol, r): canon(r)
                             for r in read_partition(partition_path(self.backup_dir, pol.table, month), pol)}
                    plan.heal_ids += [i for i, r in _fetch(conn, t, pol, lo, hi) if index.get(row_key(pol, r)) == canon(r)]
                plan.heal_rows = len(plan.heal_ids)
        return written

    def _delete_backed_up(self) -> None:
        """Step 2: delete archived and healed rows, in one transaction, only by the ids read in step 1."""
        chunk = 5000 if self.db.dialect == "postgresql" else 500
        with self.engine.begin() as conn:
            for plan in self.plans.values():
                ids = plan.archive_ids + plan.heal_ids
                if not ids:
                    continue
                t = self.db.t(plan.policy.table)
                idc = id_column(t)
                n = 0
                for i in range(0, len(ids), chunk):
                    n += conn.execute(delete(t).where(idc.in_(ids[i:i + chunk]))).rowcount
                if n != len(ids):   # rows vanished since step 1: roll back, the next run re-plans
                    raise PostConditionError(f"{plan.policy.table}: deleted {n} rows, expected {len(ids)}")

    def _retention(self) -> tuple[list[str], list[str]]:
        """Step 4: hot rows past the cutoff (one transaction), then partitions and their manifest entries."""
        with self.engine.begin() as conn:
            for plan in self.plans.values():
                if plan.frozen:
                    continue
                t = self.db.t(plan.policy.table)
                plan.purge_rows = conn.execute(delete(t).where(t.c[plan.policy.ts] < self.cutoff)).rowcount
            for name, col in RETENTION_ONLY.items():
                t = self.db.t(name)
                self.ops_purge[name] = conn.execute(delete(t).where(t.c[col] < self.cutoff)).rowcount
        deleted, filtered = [], []
        for key, action, pol, month, kept in self._partition_retention():
            if action == "delete":
                path = partition_path(self.backup_dir, pol.table, month)
                if os.path.exists(path):
                    os.remove(path)
                self.manifest.pop(key, None)
                self.removed.add(key)
                deleted.append(key)
            else:
                entry, _ = write_partition_verified(self.backup_dir, pol, month, kept, kept, self.now, self.run_id,
                                                    self.manifest.get(key))
                self.manifest[key] = entry
                self.dirty.add(key)
                filtered.append(key)
        with self.engine.begin() as conn:
            save_manifest(conn, self.db, self.manifest, self.dirty, self.removed)
        self.dirty.clear()
        self.removed.clear()
        return deleted, filtered

    def _vacuum(self) -> None:
        """Step 5: VACUUM (ANALYZE) tables that lost many rows (Postgres only, outside any transaction)."""
        for plan in self.plans.values():
            gone = plan.archive_rows + plan.heal_rows + plan.purge_rows
            if plan.frozen or gone < max(VACUUM_MIN_ROWS, VACUUM_MIN_RATIO * plan.metrics["rows"]):
                continue
            if self.db.dialect != "postgresql":
                plan.vacuum = f"skipped ({self.db.dialect})"
                continue
            try:
                with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(f"VACUUM (ANALYZE) {self.db.quote(plan.policy.table)}"))
                plan.vacuum = "done"
            except SQLAlchemyError as e:   # space is reclaimed by autovacuum anyway; report and go on
                plan.vacuum = f"failed: {e.__class__.__name__}"
                logger.warning("VACUUM (ANALYZE) %s failed", plan.policy.table, exc_info=True)

    def _record_metrics(self) -> None:
        """Step 6: one radar_table_metrics row per hot table, with the rows left after this run."""
        t = self.db.t(METRICS_TABLE)
        with self.engine.begin() as conn:
            for plan in self.plans.values():
                tt = self.db.t(plan.policy.table)
                plan.rows_after = _count(conn, tt)
                m = plan.metrics
                conn.execute(insert(t).values(**_values(t, {
                    "run_id": self.run_id, "measured_at": self.now, "table_name": plan.policy.table,
                    "status": plan.status, "reasons": plan.reasons + plan.warnings + ([plan.error] if plan.error else []),
                    "rows": m["rows"], "bytes": m["bytes"], "lookup_ms": m["lookup_ms"],
                    "lookup_max_ms": m["lookup_max_ms"], "write_p95_ms": m["write_p95_ms"], "action": plan.action(),
                    "archived_rows": plan.archive_rows + plan.heal_rows, "purged_rows": plan.purge_rows,
                    "rows_after": plan.rows_after})))

    # ---- driver

    def execute(self) -> dict:
        t0 = time.perf_counter()
        apply = self.mode == "apply"
        if apply:
            if not os.path.isdir(self.backup_dir):
                # Never created implicitly: a container without the radar_backups volume would otherwise archive
                # into its own ephemeral disk and then delete the rows from Postgres. Raised here, inside the
                # recorded run, so the refusal shows on /api/radar/health and the /schedule page, not only in logs.
                raise HousekeepingError(f"backup directory {self.backup_dir} does not exist; "
                                        "mount the radar_backups volume")
            _cleanup_tmp(self.backup_dir)
        with self.engine.connect() as conn:
            self.manifest = load_manifest(conn, self.db)
            self.rec = reconcile_manifest(self.backup_dir, self.manifest, self.policies, self.now, self.run_id,
                                          self.cutoff)
            self.dirty |= self.rec.upserts
            self.removed |= self.rec.deletes
            write_p95 = self._write_p95(conn)
            for pol in self.policies.values():
                self.plans[pol.table] = self._plan_table(conn, pol, write_p95)
            for name, col in RETENTION_ONLY.items():
                t = self.db.t(name)
                self.ops_purge[name] = _count(conn, t, t.c[col] < self.cutoff)
        ret = self._partition_retention()
        planned = {
            "partitions_write": [f"{p.policy.table}/{mo}" for p in self.plans.values() for mo in sorted(p.archive_months)],
            "partitions_delete": [k for k, a, *_ in ret if a == "delete"],
            "partitions_filter": [k for k, a, *_ in ret if a == "filter"],
            "rows_archive": {p.policy.table: p.archive_rows for p in self.plans.values() if p.archive_rows},
            "rows_heal": {p.policy.table: p.heal_rows for p in self.plans.values() if p.heal_rows},
            "rows_purge": {p.policy.table: p.purge_rows for p in self.plans.values() if p.purge_rows},
        }
        if not apply:
            for plan in self.plans.values():
                plan.rows_after = plan.metrics["rows"] - plan.archive_rows - plan.heal_rows - plan.purge_rows
            return self._report(planned, [], [], [], t0)

        written = self._archive_and_collect()
        checkpoint("partitions_written")
        self._delete_backed_up()
        checkpoint("rows_deleted")
        with self.engine.begin() as conn:
            save_manifest(conn, self.db, self.manifest, self.dirty, self.removed)
        self.dirty.clear()
        self.removed.clear()
        checkpoint("manifest_written")
        deleted, filtered = self._retention()
        checkpoint("retention_done")
        self._vacuum()
        self._record_metrics()
        return self._report(planned, written, deleted, filtered, t0)

    def _report(self, planned: dict, written: list[str], deleted: list[str], filtered: list[str],
                t0: float) -> dict:
        tables = {}
        for name, p in self.plans.items():
            tables[name] = {
                "status": p.status, "reasons": p.reasons, "warnings": p.warnings, "error": p.error,
                **p.metrics, "forced": p.forced,
                "archive_before": iso(p.archive_before) if p.archive_before else None,
                "archive_rows": p.archive_rows, "heal_rows": p.heal_rows, "purge_rows": p.purge_rows,
                "rows_after": p.rows_after, "action": p.action(), "vacuum": p.vacuum,
            }
        part_errors = dict(self.rec.errors)
        manifest = self.manifest.values()
        return {
            "v": SCHEMA_VERSION, "run_id": self.run_id, "trigger": self.trigger, "mode": self.mode,
            "status": "error" if part_errors else "ok",
            "as_of": iso(self.now), "retention_cutoff": iso(self.cutoff),
            "retention": f"{self.retention_months} calendar months, row level, UTC",
            "backup_dir": self.backup_dir, "dialect": self.db.dialect,
            "tables": tables, "retention_only": dict(self.ops_purge),
            "manifest_repairs": list(self.rec.repairs), "partition_errors": part_errors, "planned": planned,
            "partitions_written": written, "partitions_deleted": deleted, "partitions_filtered": filtered,
            "backups": {"partitions": len(self.manifest), "rows": sum(int(e.get("rows") or 0) for e in manifest),
                        "bytes": sum(int(e.get("bytes") or 0) for e in manifest)},
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }


# ---------------------------------------------------------------- run records

def new_run_id(now: datetime | None = None) -> str:
    """hk-<UTC stamp>-<6 hex>: unique (radar_housekeeping_runs.run_id is unique) and sortable by time."""
    return f"hk-{utc(now or datetime.now(UTC)):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def _record_run_start(db: _Db, run_id: str, started: datetime, trigger: str, mode: str) -> None:
    t = db.t(RUNS_TABLE)
    with db.engine.begin() as conn:
        conn.execute(insert(t).values(**_values(t, {"run_id": run_id, "started_at": started, "trigger": trigger,
                                                    "mode": mode, "status": "running", "report": None})))


def _record_run_finish(db: _Db, run_id: str, status: str, report: dict) -> None:
    t = db.t(RUNS_TABLE)
    try:
        with db.engine.begin() as conn:
            conn.execute(update(t).where(t.c.run_id == run_id).values(**_values(t, {
                "finished_at": datetime.now(UTC), "status": status, "report": report})))
    except SQLAlchemyError:   # never mask the run's own outcome
        logger.warning("could not record the end of housekeeping run %s", run_id, exc_info=True)


def _pipeline_start(engine: Engine, run_id: str, mode: str, trigger: str, started: datetime) -> int | None:
    """pipeline_run_log row (phase radar_housekeeping) so the /schedule page shows the run.

    Written with the sync engine, not services.run_log: run() is sync and may run in a worker thread of the API
    process, where asyncio.run() on the app's shared asyncpg pool would cross event loops. Like run_log, it never
    breaks the caller."""
    try:
        from db.models import PipelineRunLog

        t = PipelineRunLog.__table__
        with engine.begin() as conn:
            res = conn.execute(insert(t).values(phase=PIPELINE_PHASE, user_id=None, status="RUNNING",
                                                started_at=started,
                                                meta={"run_id": run_id, "mode": mode, "trigger": trigger}))
            return res.inserted_primary_key[0]
    except Exception:  # noqa: BLE001 - observability must never break housekeeping
        logger.warning("pipeline_run_log start failed for %s", run_id, exc_info=True)
        return None


def _pipeline_finish(engine: Engine, row_id: int | None, started: datetime, status: str, error: str | None,
                     meta: dict) -> None:
    if row_id is None:
        return
    try:
        from db.models import PipelineRunLog

        t = PipelineRunLog.__table__
        finished = datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(update(t).where(t.c.id == row_id).values(
                status=status, finished_at=finished, duration_ms=int((finished - started).total_seconds() * 1000),
                error_message=error, meta=meta))
    except Exception:  # noqa: BLE001
        logger.warning("pipeline_run_log finish failed for id=%s", row_id, exc_info=True)


def _summary(report: dict) -> dict:
    """Compact meta for pipeline_run_log."""
    tables = report.get("tables", {})
    return {"run_id": report["run_id"], "mode": report["mode"], "trigger": report["trigger"],
            "status": report["status"], "tables": {n: t["status"] for n, t in tables.items()},
            "archived": sum(t["archive_rows"] + t["heal_rows"] for t in tables.values()),
            "purged": sum(t["purge_rows"] for t in tables.values()),
            "partition_errors": report.get("partition_errors", {})}


def _pg_try_lock(engine: Engine) -> tuple[Connection | None, bool]:
    """Session-level advisory lock on a dedicated AUTOCOMMIT connection (no idle transaction held for the
    whole run). (None, True) on SQLite: tests and local runs rely on the in-process lock only."""
    if engine.dialect.name != "postgresql":
        return None, True
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        if conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return conn, True
    except BaseException:
        conn.close()
        raise
    conn.close()
    return None, False


def _pg_unlock(conn: Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    finally:
        conn.close()


_LOCAL_LOCK = threading.Lock()


# ---------------------------------------------------------------- run

def run(*, mode: str = "apply", trigger: str = "manual", force_tables: list[str] | None = None,
        now: datetime | None = None, store: RadarStore | None = None, backup_dir: str | None = None,
        retention_months: int | None = None, policies: Mapping[str, TablePolicy] | None = None,
        run_id: str | None = None, repeats: int = 3, record_dry_run: bool = False,
        log_pipeline: bool = True) -> dict:
    """One housekeeping pass; returns the report (also stored in radar_housekeeping_runs.report).

    Apply order: measure + classify every hot table -> write the archive partitions (verified on disk) ->
    delete exactly those rows (one transaction) -> upsert the manifest -> retention (hot rows, partitions,
    manifest, ops tables) -> VACUUM (ANALYZE) on Postgres -> metrics, run row, pipeline_run_log row.

    `dry-run` writes nothing (no partitions, rows, manifest, metrics or run rows) and returns the plan;
    `record_dry_run=True` lets the API keep a radar_housekeeping_runs row of the plan (nothing else). Apply runs
    also write a pipeline_run_log row (phase radar_housekeeping) unless `log_pipeline=False`. Runs are
    serialized by a Postgres advisory lock (LOCK_KEY) plus an in-process lock: a concurrent run returns status
    "busy" (recorded like any run). Report status: ok, error (partition errors froze a table), busy; a run
    that raises is recorded as failed and the exception propagates. That includes an apply run whose
    backup directory does not exist (HousekeepingError): it is never created implicitly.
    The keyword-only extras (backup_dir, retention_months, policies, run_id, repeats) default to radar.config
    and the PORT_SPEC defaults; tests pass them to stay off the real config and thresholds.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}, not {trigger!r}")
    policies = dict(POLICIES if policies is None else policies)
    forced = {resolve_table(n, policies) for n in force_tables or ()}
    if backup_dir is None or retention_months is None:
        cfg_dir, cfg_months = _config_defaults()
        backup_dir = cfg_dir if backup_dir is None else backup_dir
        retention_months = cfg_months if retention_months is None else retention_months
    now = utc(now or datetime.now(UTC)).replace(microsecond=0)
    cutoff = retention_cutoff(now.date(), retention_months)
    run_id = run_id or new_run_id(now)
    record = mode == "apply" or record_dry_run

    engine, close = _engine_for(store)

    def busy(why: str) -> dict:
        rep = _busy(run_id, mode, trigger, now, why)
        if record:   # visible on GET /api/radar/health, e.g. after a manual run clashed with the nightly one
            try:
                db = _Db(engine, policies)
                _record_run_start(db, run_id, datetime.now(UTC), trigger, mode)
                _record_run_finish(db, run_id, "busy", rep)
            except (HousekeepingError, SQLAlchemyError):
                logger.warning("could not record busy housekeeping run %s", run_id, exc_info=True)
        return rep

    try:
        if not _LOCAL_LOCK.acquire(blocking=False):
            return busy("another housekeeping run is active in this process")
        try:
            lock_conn, got = _pg_try_lock(engine)
            if not got:
                return busy("another housekeeping run holds the advisory lock")
            try:
                return _run_locked(engine, mode=mode, trigger=trigger, now=now, cutoff=cutoff,
                                   backup_dir=backup_dir, policies=policies, forced=forced, run_id=run_id,
                                   repeats=repeats, retention_months=retention_months, record=record,
                                   log_pipeline=log_pipeline)
            finally:
                _pg_unlock(lock_conn)
        finally:
            _LOCAL_LOCK.release()
    finally:
        close()


def _run_locked(engine: Engine, *, mode: str, trigger: str, now: datetime, cutoff: datetime, backup_dir: str,
                policies: Mapping[str, TablePolicy], forced: set[str], run_id: str, repeats: int,
                retention_months: int, record: bool, log_pipeline: bool) -> dict:
    db = _Db(engine, policies)
    started = datetime.now(UTC)
    if record:
        _record_run_start(db, run_id, started, trigger, mode)
    # pipeline_run_log lists work that ran (the /schedule page), so a recorded dry-run plan stays off it
    plog = _pipeline_start(engine, run_id, mode, trigger, started) if mode == "apply" and log_pipeline else None
    try:
        report = _Run(db, mode=mode, trigger=trigger, now=now, cutoff=cutoff, backup_dir=backup_dir,
                      policies=policies, forced=forced, run_id=run_id, repeats=repeats,
                      retention_months=retention_months).execute()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception("radar housekeeping %s failed", run_id)
        if record:
            _record_run_finish(db, run_id, "failed", {"run_id": run_id, "mode": mode, "trigger": trigger,
                                                      "as_of": iso(now), "status": "failed", "error": err})
        _pipeline_finish(engine, plog, started, "FAILED", err[:2000],
                         {"run_id": run_id, "mode": mode, "trigger": trigger, "status": "failed"})
        raise
    if record:
        _record_run_finish(db, run_id, report["status"], report)
    errors = report["partition_errors"]
    _pipeline_finish(engine, plog, started, "FAILED" if errors else "SUCCESS",
                     ("partition errors (tables frozen): " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
                     if errors else None, _summary(report))
    log = logger.warning if errors else logger.info
    log("radar housekeeping %s (%s, %s): %s", run_id, mode, trigger,
        ", ".join(f"{n} {t['status']} {t['action']}" for n, t in report["tables"].items()))
    return report


def _busy(run_id: str, mode: str, trigger: str, now: datetime, why: str) -> dict:
    logger.warning("radar housekeeping %s skipped: %s", run_id, why)
    return {"v": SCHEMA_VERSION, "run_id": run_id, "trigger": trigger, "mode": mode, "status": "busy",
            "as_of": iso(now), "detail": why, "tables": {}, "partition_errors": {}}


# ---------------------------------------------------------------- query / restore / verify

def _open(store: Any, backup_dir: str | None, policies: Mapping[str, TablePolicy] | None
          ) -> tuple[Engine, Callable[[], None], _Db, str]:
    engine, close = _engine_for(store)
    try:
        db = _Db(engine, POLICIES if policies is None else policies)
    except Exception:
        close()
        raise
    return engine, close, db, backup_dir if backup_dir is not None else _config_defaults()[0]


def query(table: str, start: datetime, end: datetime, where: Mapping[str, str] | None = None, *,
          store: Any = None, backup_dir: str | None = None,
          policies: Mapping[str, TablePolicy] | None = None) -> list[dict]:
    """Rows with start <= ts < end from the backups plus the hot table, de-duplicated by key (the hot copy
    wins), sorted by (ts, key). The partition files are read directly: they are the source of truth."""
    policies = POLICIES if policies is None else policies
    pol = policies[resolve_table(table, policies)]
    start, end = utc(start), utc(end)
    engine, close, db, backup_dir = _open(store, backup_dir, policies)
    try:
        out: dict[str, dict] = {}
        on_disk, _ = partitions_on_disk(backup_dir)
        for key, path in sorted(on_disk.items()):
            tbl, month = key.split("/")
            ms, me = month_bounds(month)
            if tbl == pol.table and me > start and ms < end:
                for r in read_partition(path, pol):
                    out[row_key(pol, r)] = r
        with engine.connect() as conn:
            for _, r in _fetch(conn, db.t(pol.table), pol, start, end):
                out[row_key(pol, r)] = r
    finally:
        close()
    rows = [r for r in out.values() if (t := parse_ts(r.get(pol.ts))) and start <= t < end
            and all(where_matches(r.get(k), v) for k, v in (where or {}).items())]
    return sorted(rows, key=lambda r: sort_key(pol, r))


def restore_month(table: str, month: str, out_path: str, *, store: Any = None, backup_dir: str | None = None,
                  policies: Mapping[str, TablePolicy] | None = None) -> dict:
    """Write one month (its verified partition merged with the hot rows of that month, hot copy wins) to a JSONL
    file. The partition's sha256 and row count must match the manifest. Rows are never pushed back into a hot
    table: that would re-trigger the degradation it was archived for."""
    policies = POLICIES if policies is None else policies
    pol = policies[resolve_table(table, policies)]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise RestoreError(f"month must be YYYY-MM, not {month!r}")
    engine, close, db, backup_dir = _open(store, backup_dir, policies)
    try:
        with engine.connect() as conn:
            e = load_manifest(conn, db).get(f"{pol.table}/{month}")
            if not e:
                raise RestoreError(f"no backup partition {pol.table}/{month}")
            path = os.path.join(backup_dir, e["path"])
            blob = read_file(path)
            if blob is None or sha256(blob) != e["sha256"]:
                raise RestoreError(f"{e['path']}: missing or sha256 differs from the manifest")
            try:
                backup = read_partition(path, pol)
            except PartitionCorrupt as x:
                raise RestoreError(str(x)) from x
            if len(backup) != int(e["rows"]):
                raise RestoreError(f"{e['path']}: {len(backup)} rows, manifest says {e['rows']}")
            merged = {row_key(pol, r): r for r in backup}
            ms, me = month_bounds(month)
            hot = _fetch(conn, db.t(pol.table), pol, ms, me)
    finally:
        close()
    for _, r in hot:
        merged[row_key(pol, r)] = r
    rows = sorted(merged.values(), key=lambda r: sort_key(pol, r))
    atomic_write(out_path, "".join(canon(r) + "\n" for r in rows).encode("utf-8"))
    return {"table": pol.table, "month": month, "rows": len(rows), "backup_rows": len(backup), "hot_rows": len(hot),
            "path": out_path, "min_ts": rows[0][pol.ts] if rows else None,
            "max_ts": rows[-1][pol.ts] if rows else None}


def verify(*, store: Any = None, backup_dir: str | None = None,
           policies: Mapping[str, TablePolicy] | None = None) -> dict:
    """Re-hash and re-validate every partition against the manifest; files missing from it count too."""
    policies = POLICIES if policies is None else policies
    engine, close, db, backup_dir = _open(store, backup_dir, policies)
    try:
        with engine.connect() as conn:
            manifest = load_manifest(conn, db)
    finally:
        close()
    on_disk, stray = partitions_on_disk(backup_dir)
    problems = [f"{rel}: unexpected file" for rel in stray]
    problems += [f"{key}: file not in the manifest" for key in sorted(set(on_disk) - set(manifest))]
    for key, e in sorted(manifest.items()):
        pol = policies.get(e["table_name"])
        path = os.path.join(backup_dir, e["path"])
        blob = read_file(path)
        if pol is None or blob is None:
            problems.append(f"{key}: {'unknown table' if pol is None else 'file missing'}")
            continue
        if sha256(blob) != e["sha256"]:
            problems.append(f"{key}: sha256 mismatch")
        try:
            n = len(read_partition(path, pol))
            if n != int(e["rows"]):
                problems.append(f"{key}: rows {n} != manifest {e['rows']}")
        except PartitionCorrupt as x:
            problems.append(f"{key}: {x}")
    return {"partitions": len(manifest), "rows": sum(int(e.get("rows") or 0) for e in manifest.values()),
            "problems": problems}


# ---------------------------------------------------------------- CLI

def render_markdown(report: dict) -> str:
    lines = [f"### Radar housekeeping {report['run_id']} ({report['mode']}, trigger: {report['trigger']}): "
             f"{report['status']}"]
    if report["status"] == "busy":
        return "\n".join(lines + [report.get("detail", "")]) + "\n"
    lines += [f"As of {report['as_of']}, retention cutoff {report['retention_cutoff']} ({report['retention']})", "",
              "| Table | Status | Rows | Size | Lookup max ms | Write p95 ms | Archive | Heal | Purge | Rows after | Notes |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for n, t in report["tables"].items():
        notes = t["reasons"] + t["warnings"] + ([t["error"]] if t["error"] else [])
        size = fmt_bytes(t["bytes"]) if t["bytes"] is not None else "n/a"
        lines.append(f"| {n} | {t['status']} | {t['rows']} | {size} | {t['lookup_max_ms']} | "
                     f"{t['write_p95_ms'] if t['write_p95_ms'] is not None else '-'} | {t['archive_rows']} | "
                     f"{t['heal_rows']} | {t['purge_rows']} | {t['rows_after']} | {'; '.join(notes) or '-'} |")
    planned = report.get("planned", {})
    lines += ["", f"Partitions to write: {', '.join(planned.get('partitions_write', [])) or 'none'}",
              f"Partitions to delete: {', '.join(planned.get('partitions_delete', [])) or 'none'}",
              f"Partitions to filter: {', '.join(planned.get('partitions_filter', [])) or 'none'}"]
    ops = [f"{k} {v}" for k, v in report.get("retention_only", {}).items() if v]
    if ops:
        lines.append(f"Ops rows past retention: {'; '.join(ops)}")
    b = report.get("backups") or {}
    lines.append(f"Backups: {b.get('partitions', 0)} partitions, {b.get('rows', 0)} rows, "
                 f"{fmt_bytes(b.get('bytes', 0))}")
    if report["manifest_repairs"]:
        lines.append(f"Manifest repairs: {'; '.join(report['manifest_repairs'])}")
    if report["partition_errors"]:
        lines.append("Partition errors (tables frozen): "
                     + "; ".join(f"{k}: {v}" for k, v in report["partition_errors"].items()))
    return "\n".join(lines) + "\n"


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backup-dir", help="default: radar.config.BACKUP_DIR (RADAR_BACKUP_DIR, /backups/radar)")
    p.add_argument("--db-url", help="SQLAlchemy URL (postgresql or sqlite); default: the app DATABASE_URL")


def _table_arg(name: str) -> str:
    try:
        return resolve_table(name)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m radar.housekeeping",
                                 description="Momentum Radar table housekeeping: archive to monthly backups, "
                                             "3-month retention, query and restore.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one housekeeping pass (exit 0 ok, 1 busy, 2 partition errors or "
                                   "refused, e.g. the backup directory is not mounted)")
    r.add_argument("--dry-run", action="store_true", help="measure and plan only; writes nothing")
    r.add_argument("--force", nargs="*", default=[], type=_table_arg, metavar="TABLE",
                   help="archive these tables even when not DEGRADED (member_ticks, events, scan_log)")
    r.add_argument("--as-of", help="UTC ISO timestamp, default now")
    r.add_argument("--trigger", choices=TRIGGERS, default="manual")
    r.add_argument("--run-id")
    r.add_argument("--report", help="also write the JSON report here")
    _add_common(r)
    q = sub.add_parser("query", help="rows from backups + hot, de-duplicated (hot copy wins), as JSONL "
                                     "(exit 2 on a corrupt partition)")
    q.add_argument("--table", required=True, type=_table_arg)
    q.add_argument("--from", dest="start", required=True)
    q.add_argument("--to", dest="end", required=True)
    q.add_argument("--where", nargs="*", default=[], metavar="FIELD=VALUE",
                   help="keep rows whose FIELD equals VALUE (all must match). VALUE is compared with the JSON "
                        "spelling of the field and with the Python one, so late=false and late=False, "
                        "held_min=null and held_min=None, ticker=NVDA all work. A missing field matches null")
    _add_common(q)
    s = sub.add_parser("restore", help="one month (verified backup + hot rows) to a JSONL file")
    s.add_argument("--table", required=True, type=_table_arg)
    s.add_argument("--month", required=True, help="YYYY-MM")
    s.add_argument("--out", required=True)
    _add_common(s)
    v = sub.add_parser("verify", help="re-hash and re-validate every partition against the manifest")
    _add_common(v)
    a = ap.parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    store = None
    if a.db_url:
        from radar.store import RadarStore

        store = RadarStore(a.db_url)
    try:
        if a.cmd == "run":
            now = parse_ts(a.as_of) if a.as_of else None
            if a.as_of and now is None:
                ap.error("--as-of must be an ISO timestamp")
            try:
                rep = run(mode="dry-run" if a.dry_run else "apply", trigger=a.trigger, force_tables=a.force,
                          now=now, store=store, backup_dir=a.backup_dir, run_id=a.run_id)
            except HousekeepingError as e:
                sys.stderr.write(f"housekeeping refused: {e}\n")
                return 2
            sys.stdout.write(render_markdown(rep))
            if a.report:
                with open(a.report, "w", encoding="utf-8") as f:
                    json.dump(rep, f, indent=2)
            return 1 if rep["status"] == "busy" else 2 if rep["partition_errors"] else 0
        if a.cmd == "query":
            start, end = parse_ts(a.start), parse_ts(a.end)
            if start is None or end is None:
                ap.error("--from/--to must be ISO timestamps")
            bad = [w for w in a.where if "=" not in w]
            if bad:
                ap.error(f"--where expects FIELD=VALUE, got {bad}")
            try:
                rows = query(a.table, start, end, dict(w.split("=", 1) for w in a.where), store=store,
                             backup_dir=a.backup_dir)
            except PartitionCorrupt as e:
                sys.stderr.write(f"query failed: {e}\n")
                return 2
            sys.stdout.write("".join(canon(row) + "\n" for row in rows))
            return 0
        if a.cmd == "restore":
            try:
                info = restore_month(a.table, a.month, a.out, store=store, backup_dir=a.backup_dir)
            except RestoreError as e:
                sys.stderr.write(f"restore failed: {e}\n")
                return 2
            sys.stdout.write(json.dumps(info) + "\n")
            return 0
        res = verify(store=store, backup_dir=a.backup_dir)
        sys.stdout.write(json.dumps(res, indent=2) + "\n")
        return 2 if res["problems"] else 0
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
