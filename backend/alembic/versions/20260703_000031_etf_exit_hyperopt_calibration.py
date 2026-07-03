"""extend etf exit hyperopt calibration evidence

Revision ID: 20260703_000031
Revises: 20260703_000030
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260703_000031"
down_revision: str | None = "20260703_000030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("etf_exit_hyperopt_runs", sa.Column("calibration_rule_version", sa.String(64), nullable=True))
    op.add_column("etf_exit_hyperopt_runs", sa.Column("execution_model", sa.String(32), nullable=True))
    op.add_column("etf_exit_hyperopt_runs", sa.Column("contract_hash", sa.String(128), nullable=True))
    op.add_column("etf_exit_hyperopt_runs", sa.Column("data_cutoff", sa.DateTime(), nullable=True))
    op.add_column("etf_exit_hyperopt_runs", sa.Column("bucket_summary_json", sa.JSON(), nullable=True))

    op.add_column("etf_exit_hyperopt_items", sa.Column("rolling_metrics_json", sa.JSON(), nullable=True))
    op.add_column("etf_exit_hyperopt_items", sa.Column("confidence_json", sa.JSON(), nullable=True))
    op.add_column("etf_exit_hyperopt_items", sa.Column("source_reliability", sa.String(32), nullable=True))
    op.add_column("etf_exit_hyperopt_items", sa.Column("approved_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_etf_exit_hyperopt_items_status_bucket_score",
        "etf_exit_hyperopt_items",
        ["status", "bucket_type", "bucket_key", "score"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_exit_hyperopt_items_status_bucket_score", table_name="etf_exit_hyperopt_items")
    op.drop_column("etf_exit_hyperopt_items", "approved_at")
    op.drop_column("etf_exit_hyperopt_items", "source_reliability")
    op.drop_column("etf_exit_hyperopt_items", "confidence_json")
    op.drop_column("etf_exit_hyperopt_items", "rolling_metrics_json")

    op.drop_column("etf_exit_hyperopt_runs", "bucket_summary_json")
    op.drop_column("etf_exit_hyperopt_runs", "data_cutoff")
    op.drop_column("etf_exit_hyperopt_runs", "contract_hash")
    op.drop_column("etf_exit_hyperopt_runs", "execution_model")
    op.drop_column("etf_exit_hyperopt_runs", "calibration_rule_version")
