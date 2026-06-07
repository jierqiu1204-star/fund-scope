"""Add short ETF reliability tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260606_000011"
down_revision = "20260606_000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_data_health",
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("latest_price_date", sa.Date(), nullable=True),
        sa.Column("successful_rows", sa.Integer(), nullable=False),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "short_etf_reliability_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("sample_days", sa.Integer(), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("data_coverage_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "short_etf_reliability_evaluation_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "evaluation_id",
            sa.Integer(),
            sa.ForeignKey("short_etf_reliability_evaluations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank_order", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("baseline_metrics_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("short_etf_reliability_evaluation_items")
    op.drop_table("short_etf_reliability_evaluations")
    op.drop_table("etf_data_health")
