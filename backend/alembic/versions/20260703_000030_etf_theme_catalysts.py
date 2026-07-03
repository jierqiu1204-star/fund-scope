"""add etf theme catalyst scoring tables

Revision ID: 20260703_000030
Revises: 20260703_000029
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260703_000030"
down_revision: str | None = "20260703_000029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_theme_catalyst_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("theme_key", sa.String(length=64), nullable=False),
        sa.Column("theme_name", sa.String(length=128), nullable=False),
        sa.Column("catalyst_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("effective_start", sa.Date(), nullable=True),
        sa.Column("effective_end", sa.Date(), nullable=True),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("strength_score", sa.Float(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("theme_key", "title", "event_date", name="uq_etf_theme_catalyst_event"),
    )
    op.create_index(
        "ix_etf_theme_catalyst_active_window",
        "etf_theme_catalyst_events",
        ["theme_key", "status", "effective_start", "effective_end"],
    )
    op.create_index(
        "ix_etf_theme_catalyst_source_status",
        "etf_theme_catalyst_events",
        ["source_type", "status"],
    )

    op.create_table(
        "etf_theme_catalyst_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("theme_key", sa.String(length=64), nullable=False),
        sa.Column("theme_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("catalyst_score", sa.Float(), nullable=False),
        sa.Column("sentiment_heat_score", sa.Float(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("key_events_json", sa.JSON(), nullable=False),
        sa.Column("limitations_json", sa.JSON(), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("as_of_date", "theme_key", name="uq_etf_theme_catalyst_snapshot"),
    )
    op.create_index(
        "ix_etf_theme_catalyst_snapshot_status_date",
        "etf_theme_catalyst_snapshots",
        ["status", "as_of_date"],
    )
    op.create_index(
        "ix_etf_theme_catalyst_snapshot_theme",
        "etf_theme_catalyst_snapshots",
        ["theme_key", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_theme_catalyst_snapshot_theme", table_name="etf_theme_catalyst_snapshots")
    op.drop_index("ix_etf_theme_catalyst_snapshot_status_date", table_name="etf_theme_catalyst_snapshots")
    op.drop_table("etf_theme_catalyst_snapshots")
    op.drop_index("ix_etf_theme_catalyst_source_status", table_name="etf_theme_catalyst_events")
    op.drop_index("ix_etf_theme_catalyst_active_window", table_name="etf_theme_catalyst_events")
    op.drop_table("etf_theme_catalyst_events")
