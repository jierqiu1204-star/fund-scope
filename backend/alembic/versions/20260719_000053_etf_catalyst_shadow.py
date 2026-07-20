"""add point-in-time ETF catalyst shadow evidence

Revision ID: 20260719_000053
Revises: 20260719_000052
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260719_000053"
down_revision = "20260719_000052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_catalyst_source_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("registry_version", sa.String(length=64), nullable=False),
        sa.Column("source_class", sa.String(length=32), nullable=False),
        sa.Column("allowed_domain", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=1000), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("cadence", sa.String(length=64), nullable=False),
        sa.Column("fetch_policy_json", sa.JSON(), nullable=False),
        sa.Column("raw_retention_policy_json", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_id",
            "registry_version",
            name="uq_etf_catalyst_source_registry_version",
        ),
    )
    op.create_table(
        "etf_catalyst_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.String(length=128), nullable=False),
        sa.Column("source_registry_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("canonical_url", sa.String(length=1000), nullable=True),
        sa.Column("source_published_at", sa.DateTime(), nullable=True),
        sa.Column("published_time_precision", sa.String(length=32), nullable=False),
        sa.Column("first_received_at", sa.DateTime(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("fetch_state", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("receipt_hash", sa.String(length=128), nullable=False),
        sa.Column("item_identity_hash", sa.String(length=128), nullable=False),
        sa.Column("raw_content_ref", sa.String(length=1000), nullable=True),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("correction_of_receipt_id", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "fetch_state IN ('item', 'successful_empty', 'unavailable', 'not_applicable')",
            name="ck_etf_catalyst_receipt_fetch_state",
        ),
        sa.ForeignKeyConstraint(
            ["source_registry_id"],
            ["etf_catalyst_source_registry.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["correction_of_receipt_id"],
            ["etf_catalyst_receipts.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("receipt_id"),
        sa.UniqueConstraint(
            "source_registry_id",
            "item_identity_hash",
            "content_hash",
            name="uq_etf_catalyst_receipt_version",
        ),
    )
    op.create_index(
        "ix_etf_catalyst_receipt_cutoff",
        "etf_catalyst_receipts",
        ["source_registry_id", "first_received_at"],
    )
    op.create_table(
        "etf_catalyst_extraction_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=False),
        sa.Column("extraction_method", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_json", sa.JSON(), nullable=False),
        sa.Column("cited_receipt_ids_json", sa.JSON(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("attempt_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('success', 'invalid', 'failed')",
            name="ck_etf_catalyst_extraction_status",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["etf_catalyst_receipts.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "receipt_id",
            "extractor_version",
            "attempt_hash",
            name="uq_etf_catalyst_extraction_attempt",
        ),
    )
    op.create_table(
        "etf_catalyst_event_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("entities_json", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("supporting_receipt_ids_json", sa.JSON(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(), nullable=False),
        sa.Column("first_received_at", sa.DateTime(), nullable=False),
        sa.Column("effective_start", sa.DateTime(), nullable=False),
        sa.Column("effective_end", sa.DateTime(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("direct_theme_ids_json", sa.JSON(), nullable=False),
        sa.Column("proxy_theme_ids_json", sa.JSON(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("extraction_method", sa.String(length=32), nullable=False),
        sa.Column("verification_state", sa.String(length=32), nullable=False),
        sa.Column("supersedes_event_version_id", sa.Integer(), nullable=True),
        sa.Column("event_hash", sa.String(length=128), nullable=False),
        sa.Column("limitations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "direction IN ('positive', 'negative', 'neutral', 'uncertain')",
            name="ck_etf_catalyst_event_direction",
        ),
        sa.CheckConstraint(
            "verification_state IN ('pending', 'verified', 'rejected', 'manual_display_only')",
            name="ck_etf_catalyst_event_verification",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_event_version_id"],
            ["etf_catalyst_event_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("event_hash"),
        sa.UniqueConstraint(
            "event_id",
            "event_version",
            name="uq_etf_catalyst_event_version",
        ),
    )
    op.create_index(
        "ix_etf_catalyst_event_cutoff",
        "etf_catalyst_event_versions",
        [
            "verification_state",
            "first_received_at",
            "effective_start",
            "effective_end",
        ],
    )
    op.create_table(
        "etf_catalyst_coverage_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_registry_id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.String(length=128), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("policy_id", sa.String(length=128), nullable=False),
        sa.Column("receipt_ids_json", sa.JSON(), nullable=False),
        sa.Column("observation_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'observed_none', 'unavailable', 'not_applicable')",
            name="ck_etf_catalyst_coverage_state",
        ),
        sa.ForeignKeyConstraint(
            ["source_registry_id"],
            ["etf_catalyst_source_registry.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("observation_hash"),
        sa.UniqueConstraint(
            "source_registry_id",
            "theme_id",
            "session_date",
            "cutoff_at",
            name="uq_etf_catalyst_coverage_observation",
        ),
    )
    op.create_table(
        "etf_catalyst_shadow_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.String(length=128), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("coverage_state", sa.String(length=32), nullable=False),
        sa.Column("event_versions_json", sa.JSON(), nullable=False),
        sa.Column("source_coverage_json", sa.JSON(), nullable=False),
        sa.Column("receipt_ids_json", sa.JSON(), nullable=False),
        sa.Column("limitations_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "coverage_state IN ('active', 'observed_none', 'unavailable', 'not_applicable')",
            name="ck_etf_catalyst_snapshot_state",
        ),
        sa.UniqueConstraint("snapshot_hash"),
        sa.UniqueConstraint(
            "theme_id",
            "session_date",
            "cutoff_at",
            "contract_version",
            name="uq_etf_catalyst_shadow_snapshot_cutoff",
        ),
    )
    op.create_index(
        "ix_etf_catalyst_shadow_snapshot_latest",
        "etf_catalyst_shadow_snapshots",
        ["theme_id", "session_date", "cutoff_at"],
    )
    op.create_table(
        "etf_catalyst_run_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_kind", sa.String(length=32), nullable=False),
        sa.Column("policy_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_cursor_json", sa.JSON(), nullable=False),
        sa.Column("processed_receipt_ids_json", sa.JSON(), nullable=False),
        sa.Column("batch_hashes_json", sa.JSON(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('idle', 'running', 'partial', 'complete', 'failed')",
            name="ck_etf_catalyst_run_status",
        ),
        sa.UniqueConstraint(
            "run_kind",
            "policy_hash",
            name="uq_etf_catalyst_run_identity",
        ),
    )
    op.create_table(
        "etf_catalyst_event_study_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("ranking_contract_hash", sa.String(length=128), nullable=False),
        sa.Column("evidence_hash", sa.String(length=128), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("cohorts_json", sa.JSON(), nullable=False),
        sa.Column("outcomes_json", sa.JSON(), nullable=False),
        sa.Column("exclusions_json", sa.JSON(), nullable=False),
        sa.Column("intervals_json", sa.JSON(), nullable=False),
        sa.Column("result_state", sa.String(length=32), nullable=False),
        sa.Column("limitations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "result_state IN ('insufficient_data', 'research_only')",
            name="ck_etf_catalyst_event_study_state",
        ),
        sa.UniqueConstraint("manifest_hash"),
        sa.UniqueConstraint("evidence_hash"),
    )


def downgrade() -> None:
    op.drop_table("etf_catalyst_event_study_evidence")
    op.drop_table("etf_catalyst_run_checkpoints")
    op.drop_index(
        "ix_etf_catalyst_shadow_snapshot_latest",
        table_name="etf_catalyst_shadow_snapshots",
    )
    op.drop_table("etf_catalyst_shadow_snapshots")
    op.drop_table("etf_catalyst_coverage_observations")
    op.drop_index(
        "ix_etf_catalyst_event_cutoff",
        table_name="etf_catalyst_event_versions",
    )
    op.drop_table("etf_catalyst_event_versions")
    op.drop_table("etf_catalyst_extraction_attempts")
    op.drop_index(
        "ix_etf_catalyst_receipt_cutoff",
        table_name="etf_catalyst_receipts",
    )
    op.drop_table("etf_catalyst_receipts")
    op.drop_table("etf_catalyst_source_registry")
