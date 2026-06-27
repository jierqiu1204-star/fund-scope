"""add user etf position sizing settings

Revision ID: 20260627_000023
Revises: 20260626_000022
Create Date: 2026-06-27 00:23:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260627_000023"
down_revision = "20260626_000022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("etf_trading_capital", sa.Float(), nullable=False, server_default="10000"),
    )
    op.add_column(
        "users",
        sa.Column("allow_full_exit", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "allow_full_exit")
    op.drop_column("users", "etf_trading_capital")