"""recommendation_outcomes

Forward-return scoring of recommendations (T+1/5/20 trading days), written
by the nightly outcome_scoring worker. Also adds positions.recommendation_id
so a position opened from a rec is hard-linked for realized-P&L attribution.

Idempotent: CREATE TABLE / INDEX / ADD COLUMN all use IF NOT EXISTS so the
migration coexists with Base.metadata.create_all() on fresh DBs.

Revision ID: a1f7d4e92c60
Revises: e7b5d3c91f48
Create Date: 2026-08-03 20:10:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1f7d4e92c60'
down_revision: Union[str, Sequence[str], None] = 'e7b5d3c91f48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_outcomes (
            id SERIAL PRIMARY KEY,
            recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
            recommendation_date DATE NOT NULL,
            ticker VARCHAR(10) NOT NULL,
            action VARCHAR(20) NOT NULL,
            conviction_score NUMERIC(5, 2),
            entry_price NUMERIC(10, 2),
            price_t1 NUMERIC(10, 2),
            return_t1_pct NUMERIC(8, 2),
            hit_t1 BOOLEAN,
            price_t5 NUMERIC(10, 2),
            return_t5_pct NUMERIC(8, 2),
            hit_t5 BOOLEAN,
            price_t20 NUMERIC(10, 2),
            return_t20_pct NUMERIC(8, 2),
            hit_t20 BOOLEAN,
            matured BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_outcome_rec "
        "ON recommendation_outcomes (recommendation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outcome_date "
        "ON recommendation_outcomes (recommendation_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outcome_ticker "
        "ON recommendation_outcomes (ticker)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outcome_matured "
        "ON recommendation_outcomes (matured)"
    )
    op.execute(
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS "
        "recommendation_id INTEGER REFERENCES recommendations(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS recommendation_id")
    op.execute("DROP INDEX IF EXISTS ix_outcome_matured")
    op.execute("DROP INDEX IF EXISTS ix_outcome_ticker")
    op.execute("DROP INDEX IF EXISTS ix_outcome_date")
    op.execute("DROP INDEX IF EXISTS uq_outcome_rec")
    op.execute("DROP TABLE IF EXISTS recommendation_outcomes")
