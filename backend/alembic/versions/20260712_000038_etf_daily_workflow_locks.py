"""add ETF daily workflow locks

Revision ID: 20260712_000038
Revises: 20260712_000037
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000038"
down_revision: str | None = "20260712_000037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_daily_workflow_locks",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("trade_date"),
    )


def downgrade() -> None:
    op.drop_table("etf_daily_workflow_locks")
