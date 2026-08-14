"""retire the superseded stock price history table

Recommendation metrics now read authoritative total-return-adjusted A-share
facts. Only the obsolete price table is removed; stock universe, fundamental,
metric, and recommendation tables remain intact.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260814_000069"
down_revision = "20260814_000068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("stock_price_history")


def downgrade() -> None:
    op.create_table(
        "stock_price_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "stock_code",
            sa.String(length=32),
            sa.ForeignKey("stocks.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "stock_code",
            "trade_date",
            name="uq_stock_price_history",
        ),
    )
