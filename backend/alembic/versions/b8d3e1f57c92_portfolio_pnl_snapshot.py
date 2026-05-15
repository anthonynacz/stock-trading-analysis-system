"""portfolio_pnl_snapshot

Per-user daily snapshot of portfolio P&L (unrealized + realized-that-day),
written by the run_pnl_snapshot worker post-market-close. Powers the
"P&L by day / by month" rollup on the Positions page.

Idempotent: CREATE TABLE / INDEX / CONSTRAINT all use IF NOT EXISTS so
the migration coexists with Base.metadata.create_all() on fresh DBs.

Revision ID: b8d3e1f57c92
Revises: 7b1c4a9e2f3d
Create Date: 2026-05-16 23:25:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b8d3e1f57c92'
down_revision: Union[str, Sequence[str], None] = '7b1c4a9e2f3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_pnl_snapshot (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            snapshot_date DATE NOT NULL,
            unrealized_pnl NUMERIC(14, 2) NOT NULL DEFAULT 0,
            realized_pnl_today NUMERIC(14, 2) NOT NULL DEFAULT 0,
            realized_pnl_cumulative NUMERIC(14, 2) NOT NULL DEFAULT 0,
            open_count INTEGER NOT NULL DEFAULT 0,
            closed_count_today INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pnl_snapshot_user_date "
        "ON portfolio_pnl_snapshot (user_id, snapshot_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pnl_snapshot_user_date "
        "ON portfolio_pnl_snapshot (user_id, snapshot_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pnl_snapshot_user_date")
    op.execute("DROP INDEX IF EXISTS uq_pnl_snapshot_user_date")
    op.execute("DROP TABLE IF EXISTS portfolio_pnl_snapshot")
