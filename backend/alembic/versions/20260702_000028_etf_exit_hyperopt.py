"""add etf exit hyperopt evidence tables

Revision ID: 20260702_000028
Revises: 20260630_000027
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260702_000028"
down_revision: str | None = "20260630_000027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_exit_hyperopt_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("objective", sa.String(length=64), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("train_range_json", sa.JSON(), nullable=False),
        sa.Column("out_of_sample_range_json", sa.JSON(), nullable=False),
        sa.Column("search_space_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_exit_hyperopt_runs_status_started",
        "etf_exit_hyperopt_runs",
        ["status", "started_at"],
    )
    op.create_index(
        "ix_etf_exit_hyperopt_runs_finished",
        "etf_exit_hyperopt_runs",
        ["finished_at"],
    )

    op.create_table(
        "etf_exit_hyperopt_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("bucket_type", sa.String(length=32), nullable=False),
        sa.Column("bucket_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("parameter_json", sa.JSON(), nullable=False),
        sa.Column("train_metrics_json", sa.JSON(), nullable=False),
        sa.Column("out_of_sample_metrics_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["etf_exit_hyperopt_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_exit_hyperopt_items_run_bucket",
        "etf_exit_hyperopt_items",
        ["run_id", "bucket_type", "bucket_key"],
    )
    op.create_index(
        "ix_etf_exit_hyperopt_items_run_score",
        "etf_exit_hyperopt_items",
        ["run_id", "score"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_exit_hyperopt_items_run_score", table_name="etf_exit_hyperopt_items")
    op.drop_index("ix_etf_exit_hyperopt_items_run_bucket", table_name="etf_exit_hyperopt_items")
    op.drop_table("etf_exit_hyperopt_items")
    op.drop_index("ix_etf_exit_hyperopt_runs_finished", table_name="etf_exit_hyperopt_runs")
    op.drop_index("ix_etf_exit_hyperopt_runs_status_started", table_name="etf_exit_hyperopt_runs")
    op.drop_table("etf_exit_hyperopt_runs")
