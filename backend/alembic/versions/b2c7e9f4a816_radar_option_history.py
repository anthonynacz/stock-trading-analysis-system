"""radar_option_history

radar_option_history: every option-metrics reading of the radar names (radar/options.py refresh), kept
OPTIONS.history_days and pruned on write. Measures how stale Yahoo's ~15-minute-delayed option quotes are
(radar/option_lag.py) before paying for a live options feed. Idempotent like the other radar migrations.

Revision ID: b2c7e9f4a816
Revises: a8d4e6f2c913
Create Date: 2026-10-16 14:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b2c7e9f4a816'
down_revision: Union[str, Sequence[str], None] = 'a8d4e6f2c913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_option_history (
            id BIGSERIAL PRIMARY KEY,
            ticker VARCHAR(16) NOT NULL,
            as_of TIMESTAMPTZ NOT NULL,
            direction VARCHAR(4),
            metrics JSON,
            CONSTRAINT uq_radar_option_history_ticker_as_of UNIQUE (ticker, as_of)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_radar_option_history_as_of ON radar_option_history (as_of)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS radar_option_history")
