"""add append-only ETF production PIT capture sources

Revision ID: 20260729_000057
Revises: 20260727_000056
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260729_000057"
down_revision = "20260727_000056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_pit_capture_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=False),
        sa.Column("as_of_trade_date", sa.Date(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("source_context_hash", sa.String(length=128), nullable=False),
        sa.Column("universe_manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("ranking_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("research_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("actionable_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("readiness_policy_version", sa.String(length=128), nullable=False),
        sa.Column("readiness_state", sa.String(length=32), nullable=False),
        sa.Column("target_date_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("warmup_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("market_decision_cutoff", sa.DateTime(), nullable=False),
        sa.Column("data_receipt_cutoff", sa.DateTime(), nullable=False),
        sa.Column("replay_visibility_cutoff", sa.DateTime(), nullable=False),
        sa.Column("cutoff_timezone", sa.String(length=64), nullable=False),
        sa.Column("provider_health_hash", sa.String(length=128), nullable=False),
        sa.Column("source_context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "readiness_state = 'complete'",
            name="ck_etf_pit_capture_source_complete_readiness",
        ),
        sa.CheckConstraint(
            "target_date_coverage_ratio >= 0 AND target_date_coverage_ratio <= 1",
            name="ck_etf_pit_capture_source_target_coverage",
        ),
        sa.CheckConstraint(
            "warmup_coverage_ratio >= 0 AND warmup_coverage_ratio <= 1",
            name="ck_etf_pit_capture_source_warmup_coverage",
        ),
        sa.ForeignKeyConstraint(
            ["source_signal_run_id"],
            ["short_research_signal_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_signal_run_id",
            name="uq_etf_pit_capture_source_signal_run",
        ),
        sa.UniqueConstraint(
            "source_context_hash",
            name="uq_etf_pit_capture_source_context_hash",
        ),
    )
    op.create_index(
        "ix_etf_pit_capture_sources_trade_date",
        "etf_pit_capture_sources",
        ["as_of_trade_date", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_pit_capture_sources_trade_date",
        table_name="etf_pit_capture_sources",
    )
    op.drop_table("etf_pit_capture_sources")
