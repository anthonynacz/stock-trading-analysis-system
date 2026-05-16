"""recommendation_revision_reason

Adds revision_reason TEXT to recommendations so the intraday news
re-scoring path can record WHY a revision happened (e.g. the headline
that triggered the rescore). Existing daily/post-market revisions leave
it NULL.

Idempotent ADD COLUMN IF NOT EXISTS so it co-exists with
Base.metadata.create_all() on fresh DBs.

Revision ID: c4f2a89e1b30
Revises: b8d3e1f57c92
Create Date: 2026-05-16 23:55:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c4f2a89e1b30'
down_revision: Union[str, Sequence[str], None] = 'b8d3e1f57c92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS revision_reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE recommendations DROP COLUMN IF EXISTS revision_reason")
