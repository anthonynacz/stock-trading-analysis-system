"""watchlist_weak_days

watchlist.weak_days: consecutive automatic rotation runs on which an active
member ranked worse than config.EXIT_RANK in its sector (rotation hysteresis,
services/watchlist_manager.py). Existing rows start at 0.

Idempotent ADD COLUMN IF NOT EXISTS so it co-exists with
Base.metadata.create_all() on fresh DBs.

Revision ID: c9e4b7a2d5f1
Revises: b2c7e9f4a816
Create Date: 2026-10-23 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c9e4b7a2d5f1'
down_revision: Union[str, Sequence[str], None] = 'b2c7e9f4a816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS weak_days INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    # Nothing is dropped on downgrade: the column is harmless to older code,
    # and a later upgrade re-uses it (ADD COLUMN IF NOT EXISTS).
    pass
