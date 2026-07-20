"""add immutable ETF factor experiment evidence

Revision ID: 20260719_000052
Revises: 20260717_000051
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260719_000052"
down_revision = "20260717_000051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_factor_experiment_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("ranking_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("code_version", sa.String(length=128), nullable=False),
        sa.Column("evidence_hash", sa.String(length=128), nullable=False),
        sa.Column("samples_json", sa.JSON(), nullable=False),
        sa.Column("aggregates_json", sa.JSON(), nullable=False),
        sa.Column("exclusions_json", sa.JSON(), nullable=False),
        sa.Column("intervals_json", sa.JSON(), nullable=False),
        sa.Column("split_reports_json", sa.JSON(), nullable=False),
        sa.Column("costs_json", sa.JSON(), nullable=False),
        sa.Column("limitations_json", sa.JSON(), nullable=False),
        sa.Column("promotion_state", sa.String(length=64), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "manifest_hash",
            name="uq_etf_factor_evidence_manifest",
        ),
        sa.UniqueConstraint(
            "evidence_hash",
            name="uq_etf_factor_evidence_hash",
        ),
    )
    op.create_index(
        "ix_etf_factor_experiment_evidence_ranking_contract_hash",
        "etf_factor_experiment_evidence",
        ["ranking_contract_hash"],
    )
    op.create_table(
        "etf_factor_experiment_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("code_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cursor_date", sa.Date(), nullable=True),
        sa.Column("processed_asset_codes_json", sa.JSON(), nullable=False),
        sa.Column("completed_batch_hashes_json", sa.JSON(), nullable=False),
        sa.Column("cached_factor_rows_json", sa.JSON(), nullable=False),
        sa.Column("exclusion_count", sa.Integer(), nullable=False),
        sa.Column("batch_count", sa.Integer(), nullable=False),
        sa.Column("peak_batch_size", sa.Integer(), nullable=False),
        sa.Column("runtime_seconds", sa.Float(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("peak_memory_bytes", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'partial', 'complete')",
            name="ck_etf_factor_checkpoint_status",
        ),
        sa.UniqueConstraint(
            "manifest_hash",
            "code_version",
            name="uq_etf_factor_checkpoint_identity",
        ),
    )
    op.create_index(
        "ix_etf_factor_checkpoint_status",
        "etf_factor_experiment_checkpoints",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_factor_checkpoint_status",
        table_name="etf_factor_experiment_checkpoints",
    )
    op.drop_table("etf_factor_experiment_checkpoints")
    op.drop_index(
        "ix_etf_factor_experiment_evidence_ranking_contract_hash",
        table_name="etf_factor_experiment_evidence",
    )
    op.drop_table("etf_factor_experiment_evidence")
