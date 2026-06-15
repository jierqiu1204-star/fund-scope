"""Add intraday ETF watch tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260615_000016"
down_revision = "20260612_000015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_tracked_position_alert_day_type", "tracked_position_alerts", type_="unique")

    op.create_table(
        "etf_intraday_quotes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), sa.ForeignKey("tradable_etfs.code", ondelete="CASCADE"), nullable=False),
        sa.Column("quote_time", sa.DateTime(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("latest_price", sa.Float(), nullable=False),
        sa.Column("change_percent", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("bid_price", sa.Float(), nullable=True),
        sa.Column("ask_price", sa.Float(), nullable=True),
        sa.Column("iopv", sa.Float(), nullable=True),
        sa.Column("premium_discount_pct", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="akshare"),
        sa.Column("freshness_status", sa.String(length=32), nullable=False, server_default="fresh"),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_etf_intraday_quotes_code_time",
        "etf_intraday_quotes",
        ["etf_code", "quote_time"],
    )

    op.create_table(
        "intraday_etf_watch_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_type", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("market_session", sa.String(length=32), nullable=True),
        sa.Column("watched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_quote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_quote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suppressed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_reason", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_intraday_etf_watch_runs_started", "intraday_etf_watch_runs", ["started_at"])

    op.add_column("tracked_position_alerts", sa.Column("alert_level", sa.String(length=32), nullable=True))
    op.add_column("tracked_position_alerts", sa.Column("quote_time", sa.DateTime(), nullable=True))
    op.add_column("tracked_position_alerts", sa.Column("alert_source", sa.String(length=32), nullable=True))
    op.add_column("tracked_position_alerts", sa.Column("suppression_status", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_position_alerts", "suppression_status")
    op.drop_column("tracked_position_alerts", "alert_source")
    op.drop_column("tracked_position_alerts", "quote_time")
    op.drop_column("tracked_position_alerts", "alert_level")
    op.create_unique_constraint(
        "uq_tracked_position_alert_day_type",
        "tracked_position_alerts",
        ["tracked_position_id", "alert_date", "alert_type"],
    )
    op.drop_index("ix_intraday_etf_watch_runs_started", table_name="intraday_etf_watch_runs")
    op.drop_table("intraday_etf_watch_runs")
    op.drop_index("ix_etf_intraday_quotes_code_time", table_name="etf_intraday_quotes")
    op.drop_table("etf_intraday_quotes")
