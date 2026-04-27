"""Initial FundScope schema."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260421_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("reminder_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reference_index_code", sa.String(length=32), nullable=True),
        sa.Column("base_monthly_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_username", sa.String(length=255), nullable=True),
        sa.Column("smtp_password_ref", sa.String(length=255), nullable=True),
        sa.Column("smtp_from", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "funds",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("tracking_index_code", sa.String(length=32), nullable=True),
        sa.Column("target_allocation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_watchlist", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "indices",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=32), nullable=False),
        sa.Column("is_watchlist", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "fund_nav_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fund_code", sa.String(length=32), sa.ForeignKey("funds.code", ondelete="CASCADE"), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("nav", sa.Float(), nullable=False),
        sa.Column("accumulated_nav", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fund_code", "nav_date", name="uq_fund_nav_history"),
    )
    op.create_table(
        "index_valuation_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("index_code", sa.String(length=32), sa.ForeignKey("indices.code", ondelete="CASCADE"), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("pe", sa.Float(), nullable=False),
        sa.Column("pb", sa.Float(), nullable=False),
        sa.Column("dividend_yield", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pe_percentile", sa.Float(), nullable=True),
        sa.Column("pb_percentile", sa.Float(), nullable=True),
        sa.Column("effective_window", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("index_code", "valuation_date", name="uq_index_valuation_history"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fund_code", sa.String(length=32), sa.ForeignKey("funds.code", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("proceeds", sa.Float(), nullable=True),
        sa.Column("nav_at_trade", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column("traded_at", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "holdings_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("fund_code", sa.String(length=32), sa.ForeignKey("funds.code", ondelete="CASCADE"), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
        sa.Column("cost_basis", sa.Float(), nullable=False),
        sa.Column("market_value", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("portfolio_id", "snapshot_date", "fund_code", name="uq_holdings_snapshot"),
    )
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fund_code", sa.String(length=32), sa.ForeignKey("funds.code", ondelete="CASCADE"), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False, unique=True),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "news_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("news_item_id", sa.Integer(), sa.ForeignKey("news_items.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("summary", sa.String(length=280), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("template_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("notification_log")
    op.drop_table("news_summaries")
    op.drop_table("news_items")
    op.drop_table("holdings_snapshot")
    op.drop_table("transactions")
    op.drop_table("index_valuation_history")
    op.drop_table("fund_nav_history")
    op.drop_table("portfolios")
    op.drop_table("indices")
    op.drop_table("funds")
    op.drop_table("users")
