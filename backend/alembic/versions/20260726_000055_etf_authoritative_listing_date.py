"""add authoritative ETF listing observations and lane-scoped cooldowns

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

LEGACY_CALENDAR_HASH = "0" * 64


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

    with op.batch_alter_table("etf_adjusted_history_availability") as batch_op:
        batch_op.drop_index("ix_etf_adjusted_history_availability_retry")
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(length=128),
                nullable=False,
                server_default="legacy",
            )
        )
        batch_op.add_column(
            sa.Column(
                "required_calendar_hash",
                sa.String(length=64),
                nullable=False,
                server_default=LEGACY_CALENDAR_HASH,
            )
        )
        batch_op.drop_constraint(
            "pk_etf_adjusted_history_availability",
            type_="primary",
        )
        batch_op.create_primary_key(
            "pk_etf_adjusted_history_availability",
            [
                "etf_code",
                "provider_policy_version",
                "scope",
                "required_calendar_hash",
            ],
        )
        batch_op.create_index(
            "ix_etf_adjusted_history_availability_retry",
            [
                "provider_policy_version",
                "scope",
                "required_calendar_hash",
                "retry_after",
            ],
        )

    op.create_table(
        "etf_listing_date_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("listing_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("universe_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["etf_code"],
            ["tradable_etfs.code"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_hash",
            name="uq_etf_listing_date_observation_evidence",
        ),
    )
    op.create_index(
        "ix_etf_listing_date_observations_cutoff",
        "etf_listing_date_observations",
        ["etf_code", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_listing_date_observations_cutoff",
        table_name="etf_listing_date_observations",
    )
    op.drop_table("etf_listing_date_observations")

    with op.batch_alter_table("etf_adjusted_history_availability") as batch_op:
        batch_op.drop_index("ix_etf_adjusted_history_availability_retry")
        batch_op.drop_constraint(
            "pk_etf_adjusted_history_availability",
            type_="primary",
        )
        batch_op.create_primary_key(
            "pk_etf_adjusted_history_availability",
            ["etf_code", "provider_policy_version"],
        )
        batch_op.drop_column("required_calendar_hash")
        batch_op.drop_column("scope")
        batch_op.create_index(
            "ix_etf_adjusted_history_availability_retry",
            ["provider_policy_version", "retry_after"],
        )

    op.drop_column("tradable_etfs", "listing_date_observed_at")
    op.drop_column("tradable_etfs", "listing_date_source")
    op.drop_column("tradable_etfs", "listing_date")
