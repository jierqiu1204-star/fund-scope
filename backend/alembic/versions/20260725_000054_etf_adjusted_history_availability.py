"""add adjusted ETF history availability observations

Revision ID: 20260725_000054
Revises: 20260719_000053
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260725_000054"
down_revision = "20260719_000053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_adjusted_history_availability",
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column(
            "provider_policy_version",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=False),
        sa.Column("adjustment_version", sa.String(length=128), nullable=False),
        sa.Column("requested_from", sa.Date(), nullable=False),
        sa.Column("requested_to", sa.Date(), nullable=False),
        sa.Column("earliest_eligible_date", sa.Date(), nullable=False),
        sa.Column("latest_eligible_date", sa.Date(), nullable=False),
        sa.Column("eligible_session_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("retry_after", sa.DateTime(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('sufficient', 'source_history_shortfall')",
            name="ck_etf_adjusted_history_availability_status",
        ),
        sa.CheckConstraint(
            "eligible_session_count >= 0",
            name="ck_etf_adjusted_history_availability_count",
        ),
        sa.ForeignKeyConstraint(
            ["etf_code"],
            ["tradable_etfs.code"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "etf_code",
            "provider_policy_version",
            name="pk_etf_adjusted_history_availability",
        ),
    )
    op.create_index(
        "ix_etf_adjusted_history_availability_retry",
        "etf_adjusted_history_availability",
        ["provider_policy_version", "retry_after"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_adjusted_history_availability_retry",
        table_name="etf_adjusted_history_availability",
    )
    op.drop_table("etf_adjusted_history_availability")
