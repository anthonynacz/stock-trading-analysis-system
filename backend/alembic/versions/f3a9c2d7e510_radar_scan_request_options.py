"""radar_scan_request_options

Momentum Radar additions:
- radar_runtime.scan_request: the latest "Scan now" request from the radar
  page (POST /api/radar/scan writes it, the radar worker runs it);
- radar_option_metrics: the latest option-chain and liquidity metrics per
  radar name (radar/options.py), one row per ticker, for the page's
  call-option filters.

Idempotent like d6e2a4c8b1f9 (ADD COLUMN / CREATE TABLE IF NOT EXISTS), so it
coexists with Base.metadata.create_all() on fresh DBs and autogenerate
reports nothing afterwards.

Revision ID: f3a9c2d7e510
Revises: d6e2a4c8b1f9
Create Date: 2026-10-07 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f3a9c2d7e510'
down_revision: Union[str, Sequence[str], None] = 'd6e2a4c8b1f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE radar_runtime ADD COLUMN IF NOT EXISTS scan_request JSON")
    op.execute("""
        CREATE TABLE IF NOT EXISTS radar_option_metrics (
            ticker VARCHAR(16) PRIMARY KEY,
            as_of TIMESTAMPTZ NOT NULL,
            metrics JSON,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS radar_option_metrics")
    op.execute("ALTER TABLE radar_runtime DROP COLUMN IF EXISTS scan_request")
