"""add frozen ETF action validation contract registry

Revision ID: 20260714_000043
Revises: 20260714_000042
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_000043"
down_revision: str | None = "20260714_000042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_action_validation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_key", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("candidate_registry_json", sa.JSON(), nullable=False),
        sa.Column("candidate_registry_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_contract_json", sa.JSON(), nullable=False),
        sa.Column("validation_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("sealed_at", sa.DateTime(), nullable=False),
        sa.Column("development_outcomes_calculated_at", sa.DateTime(), nullable=True),
        sa.Column("development_gate_artifact_json", sa.JSON(), nullable=True),
        sa.Column("development_gate_artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("holdout_first_consumed_at", sa.DateTime(), nullable=True),
        sa.Column("holdout_input_snapshot_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_key", name="uq_etf_action_validation_run_key"),
    )
    op.create_index(
        "ix_etf_action_validation_runs_policy",
        "etf_action_validation_runs",
        ["policy_version", "sealed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_action_validation_runs_policy",
        table_name="etf_action_validation_runs",
    )
    op.drop_table("etf_action_validation_runs")
