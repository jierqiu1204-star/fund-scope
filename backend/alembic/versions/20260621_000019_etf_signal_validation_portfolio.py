"""etf signal validation and observation portfolio snapshots

Revision ID: 20260621_000019
Revises: 20260620_000018
Create Date: 2026-06-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260621_000019"
down_revision = "20260620_000018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_signal_validation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=True),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_signal_run_id"], ["short_research_signal_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_signal_validation_runs_latest",
        "etf_signal_validation_runs",
        ["asset_type", "as_of_date", "id"],
    )
    op.create_table(
        "etf_signal_validation_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("entry_timing_label", sa.String(length=64), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column("avg_return", sa.Float(), nullable=True),
        sa.Column("median_return", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("worst_forward_drawdown", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["etf_signal_validation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "label",
            "entry_timing_label",
            "horizon_days",
            name="uq_etf_signal_validation_item",
        ),
    )
    op.create_index(
        "ix_etf_signal_validation_items_lookup",
        "etf_signal_validation_items",
        ["label", "entry_timing_label", "horizon_days"],
    )
    op.create_table(
        "etf_observation_portfolio_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=True),
        sa.Column("validation_run_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_signal_run_id"], ["short_research_signal_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["validation_run_id"], ["etf_signal_validation_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_observation_portfolio_snapshots_latest",
        "etf_observation_portfolio_snapshots",
        ["asset_type", "as_of_date", "id"],
    )
    op.create_table(
        "etf_observation_portfolio_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("rank_order", sa.Integer(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=255), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("entry_timing_label", sa.String(length=64), nullable=True),
        sa.Column("data_date", sa.Date(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("risk_reasons_json", sa.JSON(), nullable=True),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["etf_observation_portfolio_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_observation_portfolio_items_snapshot_type_rank",
        "etf_observation_portfolio_items",
        ["snapshot_id", "item_type", "rank_order"],
    )
    op.add_column(
        "tracked_position_alerts",
        sa.Column("threshold_context_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracked_position_alerts", "threshold_context_json")
    op.drop_index(
        "ix_etf_observation_portfolio_items_snapshot_type_rank",
        table_name="etf_observation_portfolio_items",
    )
    op.drop_table("etf_observation_portfolio_items")
    op.drop_index(
        "ix_etf_observation_portfolio_snapshots_latest",
        table_name="etf_observation_portfolio_snapshots",
    )
    op.drop_table("etf_observation_portfolio_snapshots")
    op.drop_index("ix_etf_signal_validation_items_lookup", table_name="etf_signal_validation_items")
    op.drop_table("etf_signal_validation_items")
    op.drop_index("ix_etf_signal_validation_runs_latest", table_name="etf_signal_validation_runs")
    op.drop_table("etf_signal_validation_runs")
