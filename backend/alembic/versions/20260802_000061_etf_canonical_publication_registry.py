"""add ETF canonical publication registry

Revision ID: 20260802_000061
Revises: 20260802_000060
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260802_000061"
down_revision = "20260802_000060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_canonical_publication_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=False),
        sa.Column("as_of_trade_date", sa.Date(), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_hash", sa.String(length=128), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("research_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("actionable_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("readiness_policy_version", sa.String(length=64), nullable=False),
        sa.Column("readiness_policy_hash", sa.String(length=128), nullable=False),
        sa.Column("universe_snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(), nullable=False),
        sa.Column("provider_health_seal_hash", sa.String(length=128), nullable=False),
        sa.Column("provider_health_check_time", sa.DateTime(), nullable=True),
        sa.Column("provider_health_policy", sa.JSON(), nullable=False),
        sa.Column("provider_health_covered_fields", sa.JSON(), nullable=False),
        sa.Column("provider_health_source_range", sa.JSON(), nullable=False),
        sa.Column("provider_health_state", sa.String(length=32), nullable=False),
        sa.Column("provider_health_unavailable_reason", sa.String(length=128), nullable=True),
        sa.Column("surface_group_hash", sa.String(length=128), nullable=False),
        sa.Column("publication_identity_hash", sa.String(length=128), nullable=False),
        sa.Column("canonical_slot_hash", sa.String(length=128), nullable=False),
        sa.Column("supersedes_publication_id", sa.Integer(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "provider_health_state IN "
            "('compatible', 'missing', 'stale', 'incompatible', 'not_applicable')",
            name="ck_etf_canonical_publication_registry_provider_health_state",
        ),
        sa.ForeignKeyConstraint(
            ["source_signal_run_id"],
            ["short_research_signal_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_publication_id"],
            ["etf_canonical_publication_registry.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "publication_identity_hash",
            name="uq_etf_canonical_publication_registry_identity",
        ),
        sa.UniqueConstraint(
            "source_signal_run_id",
            name="uq_etf_canonical_publication_registry_source_run",
        ),
    )
    op.create_index(
        "ix_etf_canonical_publication_registry_trade_lookup",
        "etf_canonical_publication_registry",
        ["as_of_trade_date", "scope_kind", "price_basis", "surface_group_hash"],
    )
    op.create_index(
        "ix_etf_canonical_publication_registry_supersession",
        "etf_canonical_publication_registry",
        ["supersedes_publication_id"],
    )
    op.create_index(
        "ux_etf_canonical_publication_registry_current_slot",
        "etf_canonical_publication_registry",
        ["canonical_slot_hash"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_etf_canonical_publication_registry_current_slot",
        table_name="etf_canonical_publication_registry",
    )
    op.drop_index(
        "ix_etf_canonical_publication_registry_supersession",
        table_name="etf_canonical_publication_registry",
    )
    op.drop_index(
        "ix_etf_canonical_publication_registry_trade_lookup",
        table_name="etf_canonical_publication_registry",
    )
    op.drop_table("etf_canonical_publication_registry")
