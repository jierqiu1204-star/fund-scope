"""add ETF universe memberships

Revision ID: 20260712_000035
Revises: 20260712_000034
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000035"
down_revision: str | None = "20260712_000034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_universe_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("tracked_underlying_id", sa.String(length=128), nullable=True),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["etf_code"], ["tradable_etfs.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("etf_code", "effective_from", name="uq_etf_universe_membership_effective_from"),
    )
    op.create_index(
        "ix_etf_universe_memberships_effective_lookup",
        "etf_universe_memberships",
        ["etf_code", "effective_from", "effective_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_universe_memberships_effective_lookup", table_name="etf_universe_memberships")
    op.drop_table("etf_universe_memberships")
