"""Add confirmed NAV fields to tracked positions."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260612_000015"
down_revision = "20260607_000014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracked_positions",
        sa.Column("order_time_bucket", sa.String(length=16), nullable=False, server_default="unknown"),
    )
    op.add_column("tracked_positions", sa.Column("confirmed_nav_date", sa.Date(), nullable=True))
    op.add_column("tracked_positions", sa.Column("confirmed_nav", sa.Float(), nullable=True))
    op.add_column("tracked_positions", sa.Column("confirmed_shares", sa.Float(), nullable=True))
    op.alter_column("tracked_positions", "order_time_bucket", server_default=None)


def downgrade() -> None:
    op.drop_column("tracked_positions", "confirmed_shares")
    op.drop_column("tracked_positions", "confirmed_nav")
    op.drop_column("tracked_positions", "confirmed_nav_date")
    op.drop_column("tracked_positions", "order_time_bucket")
