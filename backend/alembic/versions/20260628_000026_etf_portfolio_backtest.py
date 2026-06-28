"""add etf portfolio backtest tables

Revision ID: 20260628_000026
Revises: 20260628_000025
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260628_000026"
down_revision: str | None = "20260628_000025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_portfolio_backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("ranking_version", sa.String(length=64), nullable=False),
        sa.Column("allocation_version", sa.String(length=64), nullable=False),
        sa.Column("exit_rule_version", sa.String(length=64), nullable=False),
        sa.Column("initial_cash", sa.Float(), nullable=False),
        sa.Column("fee_rate", sa.Float(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("benchmark_json", sa.JSON(), nullable=False),
        sa.Column("data_coverage_json", sa.JSON(), nullable=False),
        sa.Column("caveats_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_etf_portfolio_backtest_runs_status", "etf_portfolio_backtest_runs", ["status"])
    op.create_index(
        "ix_etf_portfolio_backtest_runs_finished_at",
        "etf_portfolio_backtest_runs",
        ["finished_at"],
    )

    op.create_table(
        "etf_portfolio_backtest_equity_curve",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("curve_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("drawdown", sa.Float(), nullable=False),
        sa.Column("benchmark_equity", sa.Float(), nullable=True),
        sa.Column("portfolio_mode", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["etf_portfolio_backtest_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "curve_date", name="uq_etf_portfolio_backtest_equity_curve"),
    )
    op.create_index(
        "ix_etf_portfolio_backtest_equity_run_date",
        "etf_portfolio_backtest_equity_curve",
        ["run_id", "curve_date"],
    )

    op.create_table(
        "etf_portfolio_backtest_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("etf_name", sa.String(length=255), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["etf_portfolio_backtest_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_portfolio_backtest_trades_run_date",
        "etf_portfolio_backtest_trades",
        ["run_id", "trade_date"],
    )

    op.create_table(
        "etf_portfolio_backtest_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("etf_name", sa.String(length=255), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("market_value", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("cost_basis", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["etf_portfolio_backtest_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "snapshot_date", "etf_code", name="uq_etf_portfolio_backtest_position"),
    )
    op.create_index(
        "ix_etf_portfolio_backtest_positions_run_date",
        "etf_portfolio_backtest_positions",
        ["run_id", "snapshot_date"],
    )

    op.create_table(
        "etf_portfolio_backtest_label_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("entry_timing_label", sa.String(length=64), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("avg_return", sa.Float(), nullable=True),
        sa.Column("median_return", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("worst_forward_drawdown", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["etf_portfolio_backtest_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "label",
            "entry_timing_label",
            "horizon_days",
            name="uq_etf_portfolio_backtest_label_summary",
        ),
    )


def downgrade() -> None:
    op.drop_table("etf_portfolio_backtest_label_summaries")
    op.drop_index("ix_etf_portfolio_backtest_positions_run_date", table_name="etf_portfolio_backtest_positions")
    op.drop_table("etf_portfolio_backtest_positions")
    op.drop_index("ix_etf_portfolio_backtest_trades_run_date", table_name="etf_portfolio_backtest_trades")
    op.drop_table("etf_portfolio_backtest_trades")
    op.drop_index(
        "ix_etf_portfolio_backtest_equity_run_date",
        table_name="etf_portfolio_backtest_equity_curve",
    )
    op.drop_table("etf_portfolio_backtest_equity_curve")
    op.drop_index("ix_etf_portfolio_backtest_runs_finished_at", table_name="etf_portfolio_backtest_runs")
    op.drop_index("ix_etf_portfolio_backtest_runs_status", table_name="etf_portfolio_backtest_runs")
    op.drop_table("etf_portfolio_backtest_runs")
