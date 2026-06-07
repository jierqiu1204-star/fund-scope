"""Add strategy lab schema."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260601_000004"
down_revision = "20260427_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("strategy_type", sa.String(length=64), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "strategy_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("strategy_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("date_range_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_table(
        "strategy_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "strategy_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("market_value", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "snapshot_date", "asset_code", name="uq_strategy_position_snapshot"),
    )
    op.create_table(
        "strategy_equity_curve",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("curve_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("drawdown", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "curve_date", name="uq_strategy_equity_curve_date"),
    )
    op.create_table(
        "paper_portfolios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Integer(),
            sa.ForeignKey("strategy_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("latest_equity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("paper_portfolios")
    op.drop_table("strategy_equity_curve")
    op.drop_table("strategy_positions")
    op.drop_table("strategy_orders")
    op.drop_table("strategy_runs")
    op.drop_table("strategy_definitions")
