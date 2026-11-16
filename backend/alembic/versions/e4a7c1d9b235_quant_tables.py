"""quant tables

The quant layer (backend/quant/): daily bars, per-strike option chain snapshots, the model registry and the
volatility forecasts. Display only; written by quant/store.py. Idempotent like the radar migrations (a fresh
database gets these tables from the baseline's create_all, then this revision finds them in place).

Revision ID: e4a7c1d9b235
Revises: d1f6a3c8e502
Create Date: 2026-11-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e4a7c1d9b235'
down_revision: Union[str, Sequence[str], None] = 'd1f6a3c8e502'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS quant_daily_bars (
            ticker VARCHAR(16) NOT NULL,
            bar_date DATE NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            adj_close DOUBLE PRECISION,
            volume BIGINT,
            source VARCHAR(8) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, bar_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quant_daily_bars_date ON quant_daily_bars (bar_date)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS quant_chain_snapshots (
            ticker VARCHAR(16) NOT NULL,
            snapshot_date DATE NOT NULL,
            expiry DATE NOT NULL,
            snapshot_at TIMESTAMPTZ NOT NULL,
            underlying_price DOUBLE PRECISION,
            dte INTEGER NOT NULL,
            n_contracts INTEGER NOT NULL,
            atm_iv DOUBLE PRECISION,
            forward DOUBLE PRECISION,
            iv_method VARCHAR(20),
            earnings_date DATE,
            contracts JSONB NOT NULL,
            PRIMARY KEY (ticker, snapshot_date, expiry)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quant_chain_snapshots_date ON quant_chain_snapshots (snapshot_date)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS quant_model_runs (
            id SERIAL PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            model VARCHAR(40) NOT NULL,
            version VARCHAR(40) NOT NULL,
            params JSONB,
            window_start DATE,
            window_end DATE,
            n_obs INTEGER,
            n_tickers INTEGER,
            metrics JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quant_model_runs_kind_created ON quant_model_runs (kind, created_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS quant_vol_forecasts (
            ticker VARCHAR(16) NOT NULL,
            as_of_date DATE NOT NULL,
            model_version VARCHAR(40) NOT NULL,
            horizon INTEGER NOT NULL,
            price DOUBLE PRECISION,
            forecast_vol DOUBLE PRECISION NOT NULL,
            realized_vol_21d DOUBLE PRECISION,
            implied_vol_30d DOUBLE PRECISION,
            iv_source VARCHAR(24),
            iv_detail JSONB,
            iv_to_forecast DOUBLE PRECISION,
            ratio_pctl DOUBLE PRECISION,
            reading VARCHAR(24) NOT NULL,
            reading_basis VARCHAR(24),
            earnings_in_horizon BOOLEAN NOT NULL DEFAULT FALSE,
            earnings_date DATE,
            flags JSONB,
            model_run_id INTEGER,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ticker, as_of_date, model_version, horizon)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quant_vol_forecasts_date ON quant_vol_forecasts (as_of_date)")


def downgrade() -> None:
    for table in ("quant_vol_forecasts", "quant_model_runs", "quant_chain_snapshots", "quant_daily_bars"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
