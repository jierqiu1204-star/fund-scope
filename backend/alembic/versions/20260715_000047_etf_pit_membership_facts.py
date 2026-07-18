"""add factual ETF point-in-time membership facts

Revision ID: 20260715_000047
Revises: 20260715_000046
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_000047"
down_revision: str | None = "20260715_000046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_point_in_time_membership_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(32), nullable=False),
        sa.Column("external_source_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "membership_state",
            sa.String(16),
            nullable=False,
            server_default="included",
        ),
        sa.Column("evidence_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("raw_payload_hash", sa.String(64), nullable=False),
        sa.Column("fact_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_etf_pit_membership_facts_effective_interval",
        ),
        sa.CheckConstraint(
            "membership_state IN ('included', 'excluded')",
            name="ck_etf_pit_membership_facts_state",
        ),
    )
    op.create_index(
        "ix_etf_pit_membership_facts_effective_lookup",
        "etf_point_in_time_membership_facts",
        ["etf_code", "effective_from", "effective_to"],
    )
    op.create_index(
        "ix_etf_pit_membership_facts_observed_cursor",
        "etf_point_in_time_membership_facts",
        ["observed_at", "etf_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_pit_membership_facts_observed_cursor",
        table_name="etf_point_in_time_membership_facts",
    )
    op.drop_index(
        "ix_etf_pit_membership_facts_effective_lookup",
        table_name="etf_point_in_time_membership_facts",
    )
    op.drop_table("etf_point_in_time_membership_facts")
