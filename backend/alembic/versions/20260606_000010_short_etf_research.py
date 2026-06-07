"""Add short ETF research tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260606_000010"
down_revision = "20260605_000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tradable_etfs",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("theme_tags_json", sa.JSON(), nullable=False),
        sa.Column("trading_rule_label", sa.String(length=64), nullable=False),
        sa.Column("asset_class", sa.String(length=64), nullable=False),
        sa.Column("is_short_term_eligible", sa.Boolean(), nullable=False),
        sa.Column("is_watchlist", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "etf_price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("pct_change", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("etf_code", "trade_date", name="uq_etf_price_history"),
    )
    op.create_index("ix_etf_price_history_code_date", "etf_price_history", ["etf_code", "trade_date"])
    op.create_table(
        "etf_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("return_5d", sa.Float(), nullable=True),
        sa.Column("return_20d", sa.Float(), nullable=True),
        sa.Column("return_60d", sa.Float(), nullable=True),
        sa.Column("average_turnover_20d", sa.Float(), nullable=True),
        sa.Column("volatility_20d", sa.Float(), nullable=True),
        sa.Column("max_drawdown_60d", sa.Float(), nullable=True),
        sa.Column("trend_score", sa.Float(), nullable=False),
        sa.Column("liquidity_score", sa.Float(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("etf_code", "metric_date", name="uq_etf_metric"),
    )
    op.create_table(
        "etf_theme_exposures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("theme", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("etf_code", "theme", name="uq_etf_theme_exposure"),
    )
    op.create_table(
        "short_etf_signal_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_table(
        "short_etf_signal_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("short_etf_signal_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "etf_code", name="uq_short_etf_signal_item"),
    )
    op.create_table(
        "short_etf_signal_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("short_etf_signal_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_short_etf_signal_review_run"),
    )
    op.create_table(
        "short_etf_signal_review_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("short_etf_signal_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_item_id", sa.Integer(), sa.ForeignKey("short_etf_signal_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("verdict", sa.String(length=64), nullable=False),
        sa.Column("agent_notes_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("review_id", "signal_item_id", name="uq_short_etf_review_item"),
    )
    op.create_table(
        "short_etf_paper_portfolios",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("latest_equity", sa.Float(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "short_etf_paper_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("short_etf_paper_portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_run_id", sa.Integer(), sa.ForeignKey("short_etf_signal_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "short_etf_paper_equity_curve",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("short_etf_paper_portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("curve_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("drawdown", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("paper_id", "curve_date", name="uq_short_etf_paper_equity_curve"),
    )


def downgrade() -> None:
    op.drop_table("short_etf_paper_equity_curve")
    op.drop_table("short_etf_paper_orders")
    op.drop_table("short_etf_paper_portfolios")
    op.drop_table("short_etf_signal_review_items")
    op.drop_table("short_etf_signal_reviews")
    op.drop_table("short_etf_signal_items")
    op.drop_table("short_etf_signal_runs")
    op.drop_table("etf_theme_exposures")
    op.drop_table("etf_metrics")
    op.drop_index("ix_etf_price_history_code_date", table_name="etf_price_history")
    op.drop_table("etf_price_history")
    op.drop_table("tradable_etfs")
