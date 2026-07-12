"""add ETF sync cursors

Revision ID: 20260712_000037
Revises: 20260712_000036
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000037"
down_revision: str | None = "20260712_000036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_sync_cursors",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("last_priority_code", sa.String(length=32), nullable=True),
        sa.Column("last_regular_code", sa.String(length=32), nullable=True),
        sa.Column("last_lane", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("scope"),
    )


def downgrade() -> None:
    op.drop_table("etf_sync_cursors")
