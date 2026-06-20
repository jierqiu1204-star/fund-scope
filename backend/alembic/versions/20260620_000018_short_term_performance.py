"""short term performance indexes and intraday summary

Revision ID: 20260620_000018
Revises: 20260617_000017
Create Date: 2026-06-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260620_000018"
down_revision = "20260617_000017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_intraday_daily_summaries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("etf_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("quote_count", sa.Integer(), nullable=False),
        sa.Column("first_quote_time", sa.DateTime(), nullable=True),
        sa.Column("last_quote_time", sa.DateTime(), nullable=True),
        sa.Column("open_price", sa.Float(), nullable=True),
        sa.Column("high_price", sa.Float(), nullable=True),
        sa.Column("low_price", sa.Float(), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("total_volume", sa.Float(), nullable=True),
        sa.Column("total_turnover", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["etf_code"], ["tradable_etfs.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("etf_code", "trade_date", name="uq_etf_intraday_daily_summary"),
    )
    op.create_index(
        "ix_etf_intraday_daily_summaries_code_date",
        "etf_intraday_daily_summaries",
        ["etf_code", "trade_date"],
    )
    op.create_index(
        "ix_etf_intraday_quotes_code_time_id",
        "etf_intraday_quotes",
        ["etf_code", "quote_time", "id"],
    )
    op.create_index(
        "ix_short_research_signal_runs_status_latest",
        "short_research_signal_runs",
        ["status", "as_of_date", "finished_at", "id"],
    )
    op.create_index("ix_job_runs_name_started", "job_runs", ["job_name", "started_at", "id"])
    op.create_index(
        "ix_tracked_position_alerts_position_type_date_email",
        "tracked_position_alerts",
        ["tracked_position_id", "alert_type", "alert_date", "email_status"],
    )
    op.create_index(
        "ix_tracked_position_alerts_position_source_created",
        "tracked_position_alerts",
        ["tracked_position_id", "alert_source", "created_at", "id"],
    )
    op.create_index(
        "ix_tracked_positions_user_status_created",
        "tracked_positions",
        ["user_id", "status", "created_at", "id"],
    )
    op.add_column("tracked_positions", sa.Column("exit_state_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_positions", "exit_state_json")
    op.drop_index("ix_tracked_positions_user_status_created", table_name="tracked_positions")
    op.drop_index("ix_tracked_position_alerts_position_source_created", table_name="tracked_position_alerts")
    op.drop_index("ix_tracked_position_alerts_position_type_date_email", table_name="tracked_position_alerts")
    op.drop_index("ix_job_runs_name_started", table_name="job_runs")
    op.drop_index("ix_short_research_signal_runs_status_latest", table_name="short_research_signal_runs")
    op.drop_index("ix_etf_intraday_quotes_code_time_id", table_name="etf_intraday_quotes")
    op.drop_index("ix_etf_intraday_daily_summaries_code_date", table_name="etf_intraday_daily_summaries")
    op.drop_table("etf_intraday_daily_summaries")