"""Add asset recommendation schema."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision = "20260427_000003"
down_revision = "20260421_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fund_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fund_code", sa.String(length=32), sa.ForeignKey("funds.code", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("return_1y", sa.Float(), nullable=True),
        sa.Column("volatility_1y", sa.Float(), nullable=True),
        sa.Column("max_drawdown_1y", sa.Float(), nullable=True),
        sa.Column("tracking_error", sa.Float(), nullable=True),
        sa.Column("fee_rate", sa.Float(), nullable=True),
        sa.Column("fund_size", sa.Float(), nullable=True),
        sa.Column("news_risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("data_quality_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fund_code", "metric_date", name="uq_fund_metrics"),
    )
    op.create_table(
        "stocks",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=128), nullable=False),
        sa.Column("is_candidate", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "stock_price_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_code", sa.String(length=32), sa.ForeignKey("stocks.code", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stock_code", "trade_date", name="uq_stock_price_history"),
    )
    op.create_table(
        "stock_fundamentals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_code", sa.String(length=32), sa.ForeignKey("stocks.code", ondelete="CASCADE"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("pe", sa.Float(), nullable=True),
        sa.Column("pb", sa.Float(), nullable=True),
        sa.Column("roe", sa.Float(), nullable=True),
        sa.Column("gross_margin", sa.Float(), nullable=True),
        sa.Column("debt_to_asset", sa.Float(), nullable=True),
        sa.Column("operating_cashflow", sa.Float(), nullable=True),
        sa.Column("dividend_yield", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stock_code", "report_date", name="uq_stock_fundamentals"),
    )
    op.create_table(
        "stock_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_code", sa.String(length=32), sa.ForeignKey("stocks.code", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("valuation_score", sa.Float(), nullable=True),
        sa.Column("momentum_score", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("liquidity_score", sa.Float(), nullable=True),
        sa.Column("data_quality_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("stock_code", "metric_date", name="uq_stock_metrics"),
    )
    op.create_table(
        "recommendation_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="balanced"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("data_cutoff_json", sa.JSON(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_table(
        "recommendation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("data_freshness_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "asset_code", name="uq_recommendation_item_asset"),
    )

    _seed_defaults()


def _seed_defaults() -> None:
    now = datetime.utcnow()
    profiles = sa.table(
        "recommendation_profiles",
        sa.column("name", sa.String()),
        sa.column("asset_type", sa.String()),
        sa.column("risk_level", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("config_json", sa.JSON()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    stocks = sa.table(
        "stocks",
        sa.column("code", sa.String()),
        sa.column("exchange", sa.String()),
        sa.column("name", sa.String()),
        sa.column("industry", sa.String()),
        sa.column("is_candidate", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )
    op.bulk_insert(
        profiles,
        [
            {
                "name": "Default fund screening",
                "asset_type": "fund",
                "risk_level": "balanced",
                "is_default": True,
                "config_json": {},
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Default stock watchlist screening",
                "asset_type": "stock",
                "risk_level": "balanced",
                "is_default": True,
                "config_json": {},
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    op.bulk_insert(
        stocks,
        [
            {
                "code": "600519.SH",
                "exchange": "SH",
                "name": "Kweichow Moutai",
                "industry": "consumer",
                "is_candidate": True,
                "created_at": now,
            },
            {
                "code": "000333.SZ",
                "exchange": "SZ",
                "name": "Midea Group",
                "industry": "consumer",
                "is_candidate": True,
                "created_at": now,
            },
            {
                "code": "600036.SH",
                "exchange": "SH",
                "name": "China Merchants Bank",
                "industry": "financials",
                "is_candidate": True,
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("recommendation_items")
    op.drop_table("recommendation_runs")
    op.drop_table("recommendation_profiles")
    op.drop_table("stock_metrics")
    op.drop_table("stock_fundamentals")
    op.drop_table("stock_price_history")
    op.drop_table("stocks")
    op.drop_table("fund_metrics")
