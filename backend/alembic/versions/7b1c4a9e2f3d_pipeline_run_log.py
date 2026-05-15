"""pipeline_run_log

Append-only log of pipeline phase + worker invocations. Powers the
/schedule frontend page (past 72h runs, upcoming 24h from APScheduler).

Idempotent: uses CREATE TABLE / INDEX IF NOT EXISTS so the migration
co-exists with a fresh DB where Base.metadata.create_all() in the
baseline already materialized the table.

Revision ID: 7b1c4a9e2f3d
Revises: 442a165430b5
Create Date: 2026-05-16 22:55:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = '7b1c4a9e2f3d'
down_revision: Union[str, Sequence[str], None] = '442a165430b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_run_log (
            id SERIAL PRIMARY KEY,
            phase VARCHAR(50) NOT NULL,
            user_id INTEGER REFERENCES users(id),
            status VARCHAR(20) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            duration_ms INTEGER,
            error_message TEXT,
            meta JSON
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_run_log_started ON pipeline_run_log (started_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_run_log_user_started ON pipeline_run_log (user_id, started_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_run_log_phase_started ON pipeline_run_log (phase, started_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pipeline_run_log_phase_started")
    op.execute("DROP INDEX IF EXISTS ix_pipeline_run_log_user_started")
    op.execute("DROP INDEX IF EXISTS ix_pipeline_run_log_started")
    op.execute("DROP TABLE IF EXISTS pipeline_run_log")
