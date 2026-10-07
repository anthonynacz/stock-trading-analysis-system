"""radar_settings

radar_runtime.settings: the scan sensitivity levels set from the radar page
(PUT /api/radar/settings, radar.config.SENSITIVITY). Idempotent like the
other radar migrations.

Revision ID: a8d4e6f2c913
Revises: f3a9c2d7e510
Create Date: 2026-10-07 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a8d4e6f2c913'
down_revision: Union[str, Sequence[str], None] = 'f3a9c2d7e510'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE radar_runtime ADD COLUMN IF NOT EXISTS settings JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE radar_runtime DROP COLUMN IF EXISTS settings")
