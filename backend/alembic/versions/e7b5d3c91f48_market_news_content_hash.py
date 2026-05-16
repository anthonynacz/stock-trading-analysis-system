"""market_news_content_hash

Adds content_hash (SHA-256 of normalized headline+summary) to market_news
as a sentiment-cache key. Lets scan_news() skip FinBERT for wire-story
reprints and verbatim aggregations that share text but differ in URL.

Idempotent (ADD COLUMN / CREATE INDEX IF NOT EXISTS) so it co-exists with
Base.metadata.create_all() on fresh DBs.

Revision ID: e7b5d3c91f48
Revises: c4f2a89e1b30
Create Date: 2026-05-16 12:25:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e7b5d3c91f48'
down_revision: Union[str, Sequence[str], None] = 'c4f2a89e1b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE market_news ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_news_content_hash ON market_news (content_hash)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_news_content_hash")
    op.execute("ALTER TABLE market_news DROP COLUMN IF EXISTS content_hash")
