"""add etf theme profiles

Revision ID: 20260628_000025
Revises: 20260628_000024
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260628_000025"
down_revision: str | None = "20260628_000024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_theme_profiles",
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("asset_bucket", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("theme_group", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("primary_theme", sa.String(length=64), nullable=False, server_default="未分类"),
        sa.Column(
            "secondary_themes_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column("classification_source", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("classification_confidence", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("classification_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["etf_code"], ["tradable_etfs.code"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("etf_code"),
    )
    op.create_index("ix_etf_theme_profiles_theme_group", "etf_theme_profiles", ["theme_group"])
    op.create_index("ix_etf_theme_profiles_primary_theme", "etf_theme_profiles", ["primary_theme"])


def downgrade() -> None:
    op.drop_index("ix_etf_theme_profiles_primary_theme", table_name="etf_theme_profiles")
    op.drop_index("ix_etf_theme_profiles_theme_group", table_name="etf_theme_profiles")
    op.drop_table("etf_theme_profiles")
