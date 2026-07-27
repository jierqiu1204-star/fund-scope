"""add authoritative provider-observed ETF listing metadata

Revision ID: 20260726_000055
Revises: 20260725_000054
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260726_000055"
down_revision = "20260725_000054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tradable_etfs",
        sa.Column("listing_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "tradable_etfs",
        sa.Column("listing_date_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tradable_etfs",
        sa.Column("listing_date_observed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tradable_etfs", "listing_date_observed_at")
    op.drop_column("tradable_etfs", "listing_date_source")
    op.drop_column("tradable_etfs", "listing_date")
