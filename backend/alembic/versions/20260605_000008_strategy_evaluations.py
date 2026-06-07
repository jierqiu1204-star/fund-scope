"""Add strategy reliability evaluation tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_000008"
down_revision = "20260604_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategy_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("data_coverage_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_strategy_evaluations_strategy_id", "strategy_evaluations", ["strategy_id"])
    op.create_table(
        "strategy_evaluation_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("strategy_evaluations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank_order", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("in_sample_metrics_json", sa.JSON(), nullable=False),
        sa.Column("out_of_sample_metrics_json", sa.JSON(), nullable=False),
        sa.Column("rolling_windows_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_strategy_evaluation_items_evaluation_id", "strategy_evaluation_items", ["evaluation_id"])


def downgrade() -> None:
    op.drop_index("ix_strategy_evaluation_items_evaluation_id", table_name="strategy_evaluation_items")
    op.drop_table("strategy_evaluation_items")
    op.drop_index("ix_strategy_evaluations_strategy_id", table_name="strategy_evaluations")
    op.drop_table("strategy_evaluations")
