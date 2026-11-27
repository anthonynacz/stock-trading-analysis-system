"""iv_daily and iv_score

iv_daily: one clean constant-maturity IV30 reading per ticker per trading day (services/iv_collector.py),
the history behind IV rank / percentile and the IV cost score. options_snapshots.iv_score and
deep_options_analyses.iv_score hold the score object (utils/iv_score.py). Idempotent like the radar
migrations.

Revision ID: c9e4b2a7d315
Revises: e4a7c1d9b235
Create Date: 2026-10-23 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c9e4b2a7d315'
down_revision: Union[str, Sequence[str], None] = 'e4a7c1d9b235'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS iv_daily (
            ticker VARCHAR(16) NOT NULL,
            day DATE NOT NULL,
            iv30 NUMERIC(6, 4),
            iv_front NUMERIC(6, 4),
            front_dte INTEGER,
            hv20 NUMERIC(6, 4),
            hv60 NUMERIC(6, 4),
            spot NUMERIC(12, 4),
            source VARCHAR(8) NOT NULL DEFAULT 'yahoo',
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, day)
        )
    """)
    op.execute("ALTER TABLE options_snapshots ADD COLUMN IF NOT EXISTS iv_score JSON")
    op.execute("ALTER TABLE deep_options_analyses ADD COLUMN IF NOT EXISTS iv_score JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE deep_options_analyses DROP COLUMN IF EXISTS iv_score")
    op.execute("ALTER TABLE options_snapshots DROP COLUMN IF EXISTS iv_score")
    op.execute("DROP TABLE IF EXISTS iv_daily")
