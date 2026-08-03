"""add immutable ETF taxonomy and tracked-underlying facts

Revision ID: 20260802_000059
Revises: 20260731_000058
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260802_000059"
down_revision = "20260731_000058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_taxonomy_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("external_source_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("asset_bucket", sa.String(length=64), nullable=False),
        sa.Column("theme_group", sa.String(length=64), nullable=False),
        sa.Column("primary_theme", sa.String(length=64), nullable=False),
        sa.Column("secondary_themes_json", sa.JSON(), nullable=False),
        sa.Column("classification_source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("classification_reason", sa.Text(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_fact_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["etf_code"],
            ["tradable_etfs.code"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fact_id"],
            ["etf_taxonomy_facts.id"],
        ),
        sa.UniqueConstraint("evidence_hash", name="uq_etf_taxonomy_facts_evidence"),
        sa.UniqueConstraint("fact_hash", name="uq_etf_taxonomy_facts_fact"),
    )
    op.create_index(
        "ix_etf_taxonomy_facts_cutoff",
        "etf_taxonomy_facts",
        ["etf_code", "observed_at", "id"],
    )
    op.create_index(
        "ix_etf_taxonomy_facts_supersession",
        "etf_taxonomy_facts",
        ["supersedes_fact_id"],
    )

    op.create_table(
        "etf_tracked_underlying_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("external_source_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("identity_state", sa.String(length=16), nullable=False),
        sa.Column("tracked_underlying_id", sa.String(length=128), nullable=True),
        sa.Column("mapping_basis", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("identity_reason", sa.Text(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_fact_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "identity_state IN ('resolved', 'unresolved')",
            name="ck_etf_tracked_underlying_facts_state",
        ),
        sa.CheckConstraint(
            "(identity_state = 'resolved' AND tracked_underlying_id IS NOT NULL) "
            "OR (identity_state = 'unresolved' AND tracked_underlying_id IS NULL)",
            name="ck_etf_tracked_underlying_facts_resolution",
        ),
        sa.CheckConstraint(
            "mapping_basis IN ('authoritative', 'manual')",
            name="ck_etf_tracked_underlying_facts_basis",
        ),
        sa.ForeignKeyConstraint(
            ["etf_code"],
            ["tradable_etfs.code"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fact_id"],
            ["etf_tracked_underlying_facts.id"],
        ),
        sa.UniqueConstraint(
            "evidence_hash",
            name="uq_etf_tracked_underlying_facts_evidence",
        ),
        sa.UniqueConstraint(
            "fact_hash",
            name="uq_etf_tracked_underlying_facts_fact",
        ),
    )
    op.create_index(
        "ix_etf_tracked_underlying_facts_cutoff",
        "etf_tracked_underlying_facts",
        ["etf_code", "observed_at", "id"],
    )
    op.create_index(
        "ix_etf_tracked_underlying_facts_supersession",
        "etf_tracked_underlying_facts",
        ["supersedes_fact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_etf_tracked_underlying_facts_supersession",
        table_name="etf_tracked_underlying_facts",
    )
    op.drop_index(
        "ix_etf_tracked_underlying_facts_cutoff",
        table_name="etf_tracked_underlying_facts",
    )
    op.drop_table("etf_tracked_underlying_facts")

    op.drop_index(
        "ix_etf_taxonomy_facts_supersession",
        table_name="etf_taxonomy_facts",
    )
    op.drop_index(
        "ix_etf_taxonomy_facts_cutoff",
        table_name="etf_taxonomy_facts",
    )
    op.drop_table("etf_taxonomy_facts")
