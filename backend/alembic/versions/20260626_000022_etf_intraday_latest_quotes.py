"""add etf intraday latest quotes

Revision ID: 20260626_000022
Revises: 20260625_000021
Create Date: 2026-06-26 00:22:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260626_000022"
down_revision = "20260625_000021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.execute(
        """
        DELETE FROM etf_intraday_quotes
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM etf_intraday_quotes
            GROUP BY etf_code, quote_time
        )
        """
    )

    op.create_table(
        "etf_intraday_latest_quotes",
        sa.Column("etf_code", sa.String(length=32), nullable=False),
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
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("freshness_status", sa.String(length=32), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["etf_code"], ["tradable_etfs.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("etf_code"),
    )
    op.create_index("ix_etf_intraday_latest_quotes_quote_time", "etf_intraday_latest_quotes", ["quote_time"])
    op.create_index("uq_etf_intraday_quote_code_time", "etf_intraday_quotes", ["etf_code", "quote_time"], unique=True)
    op.create_index("ix_etf_intraday_quotes_trade_date", "etf_intraday_quotes", ["trade_date"])

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO etf_intraday_latest_quotes (
                etf_code, quote_time, trade_date, latest_price, change_percent,
                volume, turnover, bid_price, ask_price, iopv, premium_discount_pct,
                source, freshness_status, raw_json, created_at, updated_at
            )
            SELECT DISTINCT ON (etf_code)
                etf_code, quote_time, trade_date, latest_price, change_percent,
                volume, turnover, bid_price, ask_price, iopv, premium_discount_pct,
                source, freshness_status, COALESCE(raw_json, '{}'::json), NOW(), NOW()
            FROM etf_intraday_quotes
            ORDER BY etf_code, quote_time DESC, id DESC
            """
        )
    else:
        op.execute(
            """
            INSERT INTO etf_intraday_latest_quotes (
                etf_code, quote_time, trade_date, latest_price, change_percent,
                volume, turnover, bid_price, ask_price, iopv, premium_discount_pct,
                source, freshness_status, raw_json, created_at, updated_at
            )
            SELECT
                etf_code, quote_time, trade_date, latest_price, change_percent,
                volume, turnover, bid_price, ask_price, iopv, premium_discount_pct,
                source, freshness_status, COALESCE(raw_json, '{}'), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM (
                SELECT q.*, ROW_NUMBER() OVER (
                    PARTITION BY etf_code ORDER BY quote_time DESC, id DESC
                ) AS row_num
                FROM etf_intraday_quotes q
            ) ranked
            WHERE row_num = 1
            """
        )


def downgrade() -> None:
    op.drop_index("ix_etf_intraday_quotes_trade_date", table_name="etf_intraday_quotes")
    op.drop_index("uq_etf_intraday_quote_code_time", table_name="etf_intraday_quotes")
    op.drop_index("ix_etf_intraday_latest_quotes_quote_time", table_name="etf_intraday_latest_quotes")
    op.drop_table("etf_intraday_latest_quotes")
