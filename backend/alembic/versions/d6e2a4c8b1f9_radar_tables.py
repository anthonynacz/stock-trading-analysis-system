"""radar_tables

Momentum Radar storage (backend/radar/PORT_SPEC.md section 4): the
snapshot and runtime singletons, the daily baseline packs, the three hot
tables the 5-minute ticks append to (member_ticks, events, scan_log) and
the housekeeping bookkeeping (backup manifest, runs, table metrics).
radar/store.py is the only reader/writer of the first six;
radar/housekeeping.py owns the last three.

Idempotent: CREATE TABLE / INDEX all use IF NOT EXISTS so the migration
coexists with Base.metadata.create_all() on fresh DBs (the baseline
revision builds every model, these included). Both paths must end with
the same schema, so that `alembic revision --autogenerate` on a migrated
DB reports nothing for the radar tables:
- unique keys are named UNIQUE constraints declared inside CREATE TABLE,
  with the names of the models' UniqueConstraints (a separate CREATE
  UNIQUE INDEX would reflect as an index, which autogenerate reports as
  remove_index + add_constraint); being part of the CREATE TABLE they are
  covered by its IF NOT EXISTS;
- index and check-constraint names match db/models.py;
- every DB default here is a server_default on the model.
tests/radar/test_store.py checks all three. The unique constraints double
as the ON CONFLICT arbiters that make a re-run tick idempotent.

Revision ID: d6e2a4c8b1f9
Revises: a1f7d4e92c60
Create Date: 2026-10-07 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd6e2a4c8b1f9'
down_revision: Union[str, Sequence[str], None] = 'a1f7d4e92c60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Singletons (id is always 1): the published state.json and the engine's carry-over.
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_snapshot (
            id INTEGER PRIMARY KEY,
            state JSON,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_radar_snapshot_singleton CHECK (id = 1)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_runtime (
            id INTEGER PRIMARY KEY,
            engine JSON,
            source_health_live JSON,
            alert_cursor TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_radar_runtime_singleton CHECK (id = 1)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_baselines (
            id SERIAL PRIMARY KEY,
            session_date DATE NOT NULL,
            kind VARCHAR(10) NOT NULL,
            params_version VARCHAR(40) NOT NULL,
            pack_gz BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_radar_baselines_date_kind UNIQUE (session_date, kind)
        )
    """)

    # Hot tables (archived and trimmed by radar/housekeeping.py).
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_member_ticks (
            id BIGSERIAL PRIMARY KEY,
            tick TIMESTAMPTZ NOT NULL,
            session_date DATE NOT NULL,
            slot INTEGER NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            role VARCHAR(10),
            dir VARCHAR(4),
            state VARCHAR(10),
            price DOUBLE PRECISION,
            chg_day_pct DOUBLE PRECISION,
            chg_5m_pct DOUBLE PRECISION,
            move_since_entry_pct DOUBLE PRECISION,
            vol_5m BIGINT,
            intensity DOUBLE PRECISION,
            signals JSON,
            CONSTRAINT uq_radar_member_ticks_tick_ticker UNIQUE (tick, ticker)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_radar_member_ticks_ticker_tick "
        "ON radar_member_ticks (ticker, tick)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_radar_member_ticks_tick "
        "ON radar_member_ticks (tick)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_events (
            id VARCHAR(80) PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL,
            session_date DATE NOT NULL,
            slot INTEGER NOT NULL,
            ticker VARCHAR(16) NOT NULL,
            type VARCHAR(8) NOT NULL,
            dir VARCHAR(4),
            price DOUBLE PRECISION,
            intensity DOUBLE PRECISION,
            reason VARCHAR(20),
            detail TEXT,
            episode INTEGER,
            held_min INTEGER,
            move_since_entry_pct DOUBLE PRECISION,
            late BOOLEAN NOT NULL DEFAULT FALSE,
            signals JSON,
            params_version VARCHAR(40)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_radar_events_ts ON radar_events (ts)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_radar_events_ticker_ts "
        "ON radar_events (ticker, ts)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_scan_log (
            id BIGSERIAL PRIMARY KEY,
            tick TIMESTAMPTZ NOT NULL,
            run_id VARCHAR(64) NOT NULL,
            written_at TIMESTAMPTZ,
            session_date DATE,
            phase VARCHAR(16),
            status VARCHAR(16),
            "row" JSON,
            CONSTRAINT uq_radar_scan_log_tick_run UNIQUE (tick, run_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_radar_scan_log_tick ON radar_scan_log (tick)")

    # Housekeeping bookkeeping.
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_backup_manifest (
            id SERIAL PRIMARY KEY,
            table_name VARCHAR(40) NOT NULL,
            month VARCHAR(7) NOT NULL,
            path TEXT NOT NULL,
            rows BIGINT,
            bytes BIGINT,
            raw_bytes BIGINT,
            sha256 VARCHAR(64),
            content_sha256 VARCHAR(64),
            min_ts TIMESTAMPTZ,
            max_ts TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            writes INTEGER NOT NULL DEFAULT 0,
            last_run_id VARCHAR(64),
            CONSTRAINT uq_radar_backup_manifest_table_month UNIQUE (table_name, month)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_housekeeping_runs (
            id SERIAL PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            trigger VARCHAR(20),
            mode VARCHAR(10),
            status VARCHAR(20),
            report JSON,
            CONSTRAINT uq_radar_housekeeping_runs_run_id UNIQUE (run_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_table_metrics (
            id SERIAL PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            table_name VARCHAR(40) NOT NULL,
            status VARCHAR(10) NOT NULL,
            reasons JSON,
            rows BIGINT,
            bytes BIGINT,
            lookup_ms JSON,
            lookup_max_ms DOUBLE PRECISION,
            write_p95_ms DOUBLE PRECISION,
            action VARCHAR(40),
            archived_rows BIGINT,
            purged_rows BIGINT,
            rows_after BIGINT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_radar_table_metrics_table_measured "
        "ON radar_table_metrics (table_name, measured_at)"
    )


def downgrade() -> None:
    # Only the radar tables this revision creates, by name. DROP TABLE takes
    # the table's indexes and constraints with it.
    op.execute("DROP TABLE IF EXISTS radar_table_metrics")
    op.execute("DROP TABLE IF EXISTS radar_housekeeping_runs")
    op.execute("DROP TABLE IF EXISTS radar_backup_manifest")
    op.execute("DROP TABLE IF EXISTS radar_scan_log")
    op.execute("DROP TABLE IF EXISTS radar_events")
    op.execute("DROP TABLE IF EXISTS radar_member_ticks")
    op.execute("DROP TABLE IF EXISTS radar_baselines")
    op.execute("DROP TABLE IF EXISTS radar_runtime")
    op.execute("DROP TABLE IF EXISTS radar_snapshot")
