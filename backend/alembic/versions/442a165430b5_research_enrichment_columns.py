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
    op.add_column('research_results', sa.Column('news_summary', sa.Text(), nullable=True))
    op.add_column('research_results', sa.Column('news_clusters', sa.JSON(), nullable=True))
    op.add_column('research_results', sa.Column('sentiment_timeline', sa.JSON(), nullable=True))
    op.add_column('research_results', sa.Column('top_headlines', sa.JSON(), nullable=True))
    op.add_column('research_results', sa.Column('bull_case', sa.Text(), nullable=True))
    op.add_column('research_results', sa.Column('bear_case', sa.Text(), nullable=True))
    op.add_column('research_results', sa.Column('watch_text', sa.Text(), nullable=True))
    op.add_column('research_results', sa.Column('enrichment_status', sa.String(length=20), nullable=True))
    op.add_column('research_results', sa.Column('enrichment_error', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('research_results', 'enrichment_error')
    op.drop_column('research_results', 'enrichment_status')
    op.drop_column('research_results', 'watch_text')
    op.drop_column('research_results', 'bear_case')
    op.drop_column('research_results', 'bull_case')
    op.drop_column('research_results', 'top_headlines')
    op.drop_column('research_results', 'sentiment_timeline')
    op.drop_column('research_results', 'news_clusters')
    op.drop_column('research_results', 'news_summary')
