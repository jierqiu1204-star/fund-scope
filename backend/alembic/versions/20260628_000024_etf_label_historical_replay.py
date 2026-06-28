"""add ETF label historical replay validation

Revision ID: 20260628_000024
Revises: 20260627_000023
Create Date: 2026-06-28 00:24:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260628_000024"
down_revision = "20260627_000023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "etf_signal_validation_runs",
        sa.Column("validation_mode", sa.String(length=32), nullable=False, server_default="forward_live"),
    )
    op.create_table(
        "etf_label_replay_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("validation_run_id", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False, server_default="etf"),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("entry_timing_label", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False, server_default="label_validation_v1"),
        sa.Column("replay_date", sa.Date(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("horizon_end_date", sa.Date(), nullable=True),
        sa.Column("forward_return", sa.Float(), nullable=True),
        sa.Column("adverse_drawdown", sa.Float(), nullable=True),
        sa.Column("favorable_excursion", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["validation_run_id"], ["etf_signal_validation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "validation_run_id",
            "asset_code",
            "replay_date",
            "horizon_days",
            name="uq_etf_label_replay_sample",
        ),
    )
    op.create_index(
        "ix_etf_label_replay_sample_group",
        "etf_label_replay_samples",
        ["label", "entry_timing_label", "horizon_days", "status"],
        unique=False,
    )
    op.create_index(
        "ix_etf_label_replay_sample_asset_date",
        "etf_label_replay_samples",
        ["asset_code", "replay_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_etf_label_replay_sample_asset_date", table_name="etf_label_replay_samples")
    op.drop_index("ix_etf_label_replay_sample_group", table_name="etf_label_replay_samples")
    op.drop_table("etf_label_replay_samples")
    op.drop_column("etf_signal_validation_runs", "validation_mode")
