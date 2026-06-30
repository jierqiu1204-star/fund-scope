"""add etf healthcheck and optimized allocation tables

Revision ID: 20260630_000027
Revises: 20260628_000026
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260630_000027"
down_revision: str | None = "20260628_000026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_strategy_healthcheck_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=True),
        sa.Column("validation_run_id", sa.Integer(), nullable=True),
        sa.Column("backtest_run_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("execution_model", sa.String(length=32), nullable=False),
        sa.Column("evidence_contract_hash", sa.String(length=128), nullable=True),
        sa.Column("evidence_status", sa.String(length=64), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("data_window_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("caveats_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_signal_run_id"],
            ["short_research_signal_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["etf_signal_validation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"],
            ["etf_portfolio_backtest_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_strategy_healthcheck_status_created",
        "etf_strategy_healthcheck_snapshots",
        ["status", "created_at"],
    )

    op.create_table(
        "etf_strategy_healthcheck_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("avg_return", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["etf_strategy_healthcheck_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_strategy_healthcheck_items_snapshot_type",
        "etf_strategy_healthcheck_items",
        ["snapshot_id", "item_type"],
    )

    op.create_table(
        "etf_optimized_allocation_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=True),
        sa.Column("observation_portfolio_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("method_set", sa.String(length=64), nullable=False),
        sa.Column("evidence_contract_hash", sa.String(length=128), nullable=True),
        sa.Column("data_window_json", sa.JSON(), nullable=False),
        sa.Column("constraints_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_signal_run_id"],
            ["short_research_signal_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["observation_portfolio_snapshot_id"],
            ["etf_observation_portfolio_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_optimized_allocation_status_created",
        "etf_optimized_allocation_snapshots",
        ["status", "created_at"],
    )

    op.create_table(
        "etf_optimized_allocation_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=255), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=False),
        sa.Column("expected_return", sa.Float(), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("theme_group", sa.String(length=64), nullable=True),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["etf_optimized_allocation_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_optimized_allocation_items_snapshot_method",
        "etf_optimized_allocation_items",
        ["snapshot_id", "method"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_optimized_allocation_items_snapshot_method",
        table_name="etf_optimized_allocation_items",
    )
    op.drop_table("etf_optimized_allocation_items")
    op.drop_index(
        "ix_etf_optimized_allocation_status_created",
        table_name="etf_optimized_allocation_snapshots",
    )
    op.drop_table("etf_optimized_allocation_snapshots")
    op.drop_index(
        "ix_etf_strategy_healthcheck_items_snapshot_type",
        table_name="etf_strategy_healthcheck_items",
    )
    op.drop_table("etf_strategy_healthcheck_items")
    op.drop_index(
        "ix_etf_strategy_healthcheck_status_created",
        table_name="etf_strategy_healthcheck_snapshots",
    )
    op.drop_table("etf_strategy_healthcheck_snapshots")
