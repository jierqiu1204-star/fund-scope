"""add ETF factor evidence family identity

Revision ID: 20260731_000058
Revises: 20260729_000057
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260731_000058"
down_revision = "20260729_000057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "etf_factor_experiment_evidence",
        sa.Column("experiment_family", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "etf_factor_experiment_evidence",
        sa.Column("hypothesis_registry_hash", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_etf_factor_evidence_family_latest",
        "etf_factor_experiment_evidence",
        ["experiment_family", "created_at"],
    )
    op.create_index(
        "ix_etf_factor_evidence_hypothesis_registry",
        "etf_factor_experiment_evidence",
        ["hypothesis_registry_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_factor_evidence_hypothesis_registry",
        table_name="etf_factor_experiment_evidence",
    )
    op.drop_index(
        "ix_etf_factor_evidence_family_latest",
        table_name="etf_factor_experiment_evidence",
    )
    op.drop_column(
        "etf_factor_experiment_evidence",
        "hypothesis_registry_hash",
    )
    op.drop_column("etf_factor_experiment_evidence", "experiment_family")
