"""radar_runtime_extended

radar_runtime.extended: the latest extended-hours movers scan (radar/extended.py),
pre-market and after hours, for the Movers view of the radar page. Display only.

Idempotent ADD COLUMN IF NOT EXISTS so it co-exists with
Base.metadata.create_all() on fresh DBs.

Revision ID: d1f6a3c8e502
Revises: c9e4b7a2d5f1
Create Date: 2026-11-11 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd1f6a3c8e502'
down_revision: Union[str, Sequence[str], None] = 'c9e4b7a2d5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE radar_runtime ADD COLUMN IF NOT EXISTS extended JSON")


def downgrade() -> None:
    # Nothing is dropped on downgrade: the column is harmless to older code,
    # and a later upgrade re-uses it (ADD COLUMN IF NOT EXISTS).
    pass
