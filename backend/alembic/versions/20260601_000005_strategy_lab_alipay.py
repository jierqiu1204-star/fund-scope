"""Add Alipay-style order metadata."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260601_000005"
down_revision = "20260601_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strategy_orders", sa.Column("submitted_date", sa.Date(), nullable=True))
    op.add_column("strategy_orders", sa.Column("confirmed_date", sa.Date(), nullable=True))
    op.add_column(
        "strategy_orders",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="confirmed"),
    )
    op.add_column(
        "strategy_orders",
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="generic"),
    )


def downgrade() -> None:
    op.drop_column("strategy_orders", "platform")
    op.drop_column("strategy_orders", "status")
    op.drop_column("strategy_orders", "confirmed_date")
    op.drop_column("strategy_orders", "submitted_date")
