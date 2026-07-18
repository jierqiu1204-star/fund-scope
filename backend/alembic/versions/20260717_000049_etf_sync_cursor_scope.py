"""widen ETF sync cursor scope for contract-derived lanes

Revision ID: 20260717_000049
Revises: 20260717_000048
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_000049"
down_revision: str | None = "20260717_000048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("etf_sync_cursors") as batch_op:
        batch_op.alter_column(
            "scope",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM etf_sync_cursors "
            "WHERE length(scope) > 64"
        )
    )
    with op.batch_alter_table("etf_sync_cursors") as batch_op:
        batch_op.alter_column(
            "scope",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
