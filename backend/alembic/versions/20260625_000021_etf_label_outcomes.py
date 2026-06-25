"""add etf label outcomes

Revision ID: 20260625_000021
Revises: 20260622_000020
Create Date: 2026-06-25 00:21:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260625_000021"
down_revision = "20260622_000020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_label_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_item_id", sa.Integer(), nullable=False),
        sa.Column("signal_run_id", sa.Integer(), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("entry_timing_label", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("signal_price", sa.Float(), nullable=True),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("forward_return", sa.Float(), nullable=True),
        sa.Column("adverse_drawdown", sa.Float(), nullable=True),
        sa.Column("favorable_excursion", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["signal_item_id"], ["short_research_signal_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_run_id"], ["short_research_signal_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_item_id", "horizon_days", name="uq_etf_label_outcome_signal_horizon"),
    )
    op.create_index("ix_etf_label_outcomes_asset", "etf_label_outcomes", ["asset_code", "signal_date"])
    op.create_index(
        "ix_etf_label_outcomes_group",
        "etf_label_outcomes",
        ["label", "entry_timing_label", "horizon_days", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_label_outcomes_group", table_name="etf_label_outcomes")
    op.drop_index("ix_etf_label_outcomes_asset", table_name="etf_label_outcomes")
    op.drop_table("etf_label_outcomes")
