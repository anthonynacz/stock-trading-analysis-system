"""research_enrichment_columns

Adds Tier 1 (deep news analysis) and Tier 3 (bull/bear/watch synthesis)
columns to research_results, plus an enrichment_status / error pair so the
caller can distinguish PARTIAL runs (LLM failed but news cluster succeeded)
from COMPLETE / FAILED.

Revision ID: 442a165430b5
Revises: 5de04647a468
Create Date: 2026-05-12 19:21:27.908154
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '442a165430b5'
down_revision: Union[str, Sequence[str], None] = '5de04647a468'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: the baseline migration uses Base.metadata.create_all(),
    # which on a fresh DB creates research_results with all current model
    # columns already present. Use raw IF NOT EXISTS so this migration is
    # a no-op in that case and only adds columns when stepping forward
    # from an actual pre-enrichment schema.
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS news_summary TEXT")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS news_clusters JSON")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS sentiment_timeline JSON")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS top_headlines JSON")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS bull_case TEXT")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS bear_case TEXT")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS watch_text TEXT")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS enrichment_status VARCHAR(20)")
    op.execute("ALTER TABLE research_results ADD COLUMN IF NOT EXISTS enrichment_error TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS enrichment_error")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS enrichment_status")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS watch_text")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS bear_case")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS bull_case")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS top_headlines")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS sentiment_timeline")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS news_clusters")
    op.execute("ALTER TABLE research_results DROP COLUMN IF EXISTS news_summary")
