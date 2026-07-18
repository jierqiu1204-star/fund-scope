"""add bounded ETF validation continuation and materialized samples

Revision ID: 20260717_000051
Revises: 20260717_000050
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260717_000051"
down_revision = "20260717_000050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_validation_continuations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("validation_run_id", sa.Integer(), nullable=False),
        sa.Column("identity_hash", sa.String(length=128), nullable=False),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("execution_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("candidate_registry_hash", sa.String(length=128), nullable=False),
        sa.Column("horizon_set_hash", sa.String(length=128), nullable=False),
        sa.Column("schema_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("checkpoint_source_date", sa.Date(), nullable=True),
        sa.Column("checkpoint_horizon", sa.Integer(), nullable=True),
        sa.Column("checkpoint_asset_key", sa.String(length=64), nullable=True),
        sa.Column("processed_sample_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("rolling_aggregate_hash", sa.String(length=128), nullable=True),
        sa.Column("final_aggregate_hash", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'partial', 'complete', 'failed')",
            name="ck_etf_validation_continuation_status",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["etf_signal_validation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            name="uq_etf_validation_continuation_run",
        ),
        sa.UniqueConstraint(
            "identity_hash",
            name="uq_etf_validation_continuation_identity",
        ),
    )
    op.create_index(
        "ix_etf_validation_continuation_status",
        "etf_validation_continuations",
        ["status", "updated_at"],
    )
    op.create_table(
        "etf_validation_materialized_samples",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("continuation_id", sa.Integer(), nullable=False),
        sa.Column("validation_run_id", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=False),
        sa.Column("source_event_hash", sa.String(length=128), nullable=False),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("horizon_sessions", sa.Integer(), nullable=False),
        sa.Column("asset_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("exit_date", sa.Date(), nullable=True),
        sa.Column("adjusted_entry_price", sa.Float(), nullable=True),
        sa.Column("adjusted_exit_price", sa.Float(), nullable=True),
        sa.Column("gross_return", sa.Float(), nullable=True),
        sa.Column("net_return", sa.Float(), nullable=True),
        sa.Column("fee_rate", sa.Float(), nullable=False),
        sa.Column("slippage_rate", sa.Float(), nullable=False),
        sa.Column("total_cost_rate", sa.Float(), nullable=False),
        sa.Column("adverse_drawdown", sa.Float(), nullable=True),
        sa.Column("interval_start", sa.Date(), nullable=True),
        sa.Column("interval_end", sa.Date(), nullable=True),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("sample_hash", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'pending', 'overlapping', 'excluded')",
            name="ck_etf_validation_materialized_sample_status",
        ),
        sa.ForeignKeyConstraint(
            ["continuation_id"],
            ["etf_validation_continuations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["etf_signal_validation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_event_id"],
            ["etf_signal_validation_source_events.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "continuation_id",
            "source_date",
            "horizon_sessions",
            "asset_key",
            name="uq_etf_validation_materialized_sample_key",
        ),
    )
    op.create_index(
        "ix_etf_validation_materialized_sample_source",
        "etf_validation_materialized_samples",
        ["validation_run_id", "source_date", "horizon_sessions"],
    )
    op.create_index(
        "ix_etf_validation_materialized_sample_status",
        "etf_validation_materialized_samples",
        ["validation_run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_validation_materialized_sample_status",
        table_name="etf_validation_materialized_samples",
    )
    op.drop_index(
        "ix_etf_validation_materialized_sample_source",
        table_name="etf_validation_materialized_samples",
    )
    op.drop_table("etf_validation_materialized_samples")
    op.drop_index(
        "ix_etf_validation_continuation_status",
        table_name="etf_validation_continuations",
    )
    op.drop_table("etf_validation_continuations")
