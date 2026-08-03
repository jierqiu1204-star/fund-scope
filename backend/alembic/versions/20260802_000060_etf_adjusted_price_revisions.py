"""add append-only ETF adjusted-price revisions

Revision ID: 20260802_000060
Revises: 20260802_000059
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260802_000060"
down_revision = "20260802_000059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_adjusted_price_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("pct_change", sa.Float(), nullable=False),
        sa.Column("raw_price_basis", sa.String(length=64), nullable=True),
        sa.Column("research_adjusted_value", sa.Float(), nullable=True),
        sa.Column("research_price_basis", sa.String(length=64), nullable=True),
        sa.Column("data_provider", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=False),
        sa.Column("adjustment_version", sa.String(length=128), nullable=True),
        sa.Column("decision_eligible", sa.Boolean(), nullable=False),
        sa.Column("decision_ineligibility_reason", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_revision_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["etf_code"],
            ["tradable_etfs.code"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_revision_id"],
            ["etf_adjusted_price_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "revision_hash",
            name="uq_etf_adjusted_price_revisions_revision_hash",
        ),
    )
    op.create_index(
        "ix_etf_adjusted_price_revisions_pit_lookup",
        "etf_adjusted_price_revisions",
        ["etf_code", "trade_date", "first_seen_at", "observed_at", "id"],
    )
    op.create_index(
        "ix_etf_adjusted_price_revisions_payload",
        "etf_adjusted_price_revisions",
        ["etf_code", "trade_date", "payload_hash"],
    )
    op.create_index(
        "ix_etf_adjusted_price_revisions_supersession",
        "etf_adjusted_price_revisions",
        ["supersedes_revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_adjusted_price_revisions_supersession",
        table_name="etf_adjusted_price_revisions",
    )
    op.drop_index(
        "ix_etf_adjusted_price_revisions_payload",
        table_name="etf_adjusted_price_revisions",
    )
    op.drop_index(
        "ix_etf_adjusted_price_revisions_pit_lookup",
        table_name="etf_adjusted_price_revisions",
    )
    op.drop_table("etf_adjusted_price_revisions")
