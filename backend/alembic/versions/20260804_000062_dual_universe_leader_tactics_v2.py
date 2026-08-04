"""Add isolated dual-universe leader-tactics V2 research storage.

The tables are append-only research materializations.  They do not share
ranking, position, notification, or execution tables.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260804_000062"
down_revision = "20260802_000061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leader_tactics_v2_source_registries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("registry_version", sa.String(length=128), nullable=False),
        sa.Column("registry_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "leader_tactics_v2_run_manifests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("universe", sa.String(length=16), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(), nullable=False),
        sa.Column("data_receipt_cutoff", sa.DateTime(), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("source_registry_hash", sa.String(length=128), nullable=False),
        sa.Column("formula_registry_hash", sa.String(length=128), nullable=False),
        sa.Column("code_version", sa.String(length=128), nullable=False),
        sa.Column("holdout_identity", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider_health_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("exclusions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("manifest_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_leader_tactics_v2_manifest_universe_cutoff",
        "leader_tactics_v2_run_manifests",
        ["universe", "decision_cutoff"],
    )
    op.create_table(
        "leader_tactics_v2_candidate_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("universe", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=256), nullable=False),
        sa.Column("theme", sa.String(length=256), nullable=True),
        sa.Column("sector", sa.String(length=256), nullable=True),
        sa.Column("tracked_index", sa.String(length=64), nullable=True),
        sa.Column("formula_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("availability", sa.String(length=32), nullable=False),
        sa.Column("qualifies", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("source_cutoff", sa.DateTime(), nullable=False),
        sa.Column("gate_facts_json", sa.Text(), nullable=False),
        sa.Column("exclusion_reasons_json", sa.Text(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("feature_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "manifest_hash",
            "universe",
            "asset_code",
            "formula_id",
            "signal_date",
            name="uq_leader_tactics_v2_candidate_identity",
        ),
    )
    op.create_index(
        "ix_leader_tactics_v2_candidate_query",
        "leader_tactics_v2_candidate_observations",
        ["universe", "formula_id", "state", "signal_date", "asset_code"],
    )
    op.create_table(
        "leader_tactics_v2_state_transitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("universe", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("formula_id", sa.String(length=128), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("transition_date", sa.Date(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("transition_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_leader_tactics_v2_transition_asset",
        "leader_tactics_v2_state_transitions",
        ["universe", "asset_code", "formula_id", "transition_date"],
    )
    op.create_table(
        "leader_tactics_v2_source_labels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("article_id", sa.String(length=128), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=True),
        sa.Column("label_kind", sa.String(length=64), nullable=False),
        sa.Column("label_date", sa.Date(), nullable=True),
        sa.Column("theme", sa.String(length=256), nullable=True),
        sa.Column("observability", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("label_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "leader_tactics_v2_holdout_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("holdout_identity", sa.String(length=128), nullable=False, unique=True),
        sa.Column("case_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "leader_tactics_v2_revision_audits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_key", sa.String(length=256), nullable=False),
        sa.Column("prior_hash", sa.String(length=128), nullable=True),
        sa.Column("new_hash", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "entity_type",
            "entity_key",
            "new_hash",
            name="uq_leader_tactics_v2_revision_identity",
        ),
    )
    op.create_index(
        "ix_leader_tactics_v2_revision_lookup",
        "leader_tactics_v2_revision_audits",
        ["manifest_hash", "entity_type", "entity_key"],
    )
    op.create_table(
        "leader_tactics_v2_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("cursor", sa.String(length=256), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_hashes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("failed_codes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "ashare_research_universe_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=256), nullable=False),
        sa.Column("listing_state", sa.String(length=32), nullable=False),
        sa.Column("board", sa.String(length=32), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_cutoff", sa.DateTime(), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=256), nullable=True),
        sa.Column("fact_hash", sa.String(length=128), nullable=False, unique=True),
    )
    op.create_index(
        "ix_ashare_research_universe_pit_lookup",
        "ashare_research_universe_snapshots",
        ["asset_code", "snapshot_date", "effective_at", "received_at", "source_cutoff"],
    )
    op.create_table(
        "ashare_theme_membership_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("group_id", sa.String(length=256), nullable=False),
        sa.Column("theme", sa.String(length=256), nullable=True),
        sa.Column("sector", sa.String(length=256), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("supersedes_fact_hash", sa.String(length=128), nullable=True),
        sa.Column("mapping_kind", sa.String(length=32), nullable=False),
        sa.Column("tracked_index", sa.String(length=64), nullable=True),
        sa.Column("clone_group", sa.String(length=128), nullable=True),
        sa.Column("issuer", sa.String(length=128), nullable=True),
        sa.Column("fact_hash", sa.String(length=128), nullable=False, unique=True),
    )
    op.create_index(
        "ix_ashare_theme_membership_pit_lookup",
        "ashare_theme_membership_facts",
        ["asset_code", "effective_from", "effective_to", "received_at"],
    )
    op.create_table(
        "ashare_adjusted_price_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjusted_open", sa.Float(), nullable=False),
        sa.Column("adjusted_high", sa.Float(), nullable=False),
        sa.Column("adjusted_low", sa.Float(), nullable=False),
        sa.Column("adjusted_close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("turnover", sa.Float(), nullable=False),
        sa.Column("price_basis", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("adjustment_version", sa.String(length=128), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column(
            "historical_research_only", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("decision_eligible", sa.Boolean(), nullable=False),
        sa.Column("fact_hash", sa.String(length=128), nullable=False, unique=True),
        sa.UniqueConstraint(
            "asset_code", "trade_date", "revision_id", name="uq_ashare_adjusted_price_revision"
        ),
    )
    op.create_index(
        "ix_ashare_adjusted_price_pit_lookup",
        "ashare_adjusted_price_facts",
        ["asset_code", "trade_date", "received_at", "decision_eligible"],
    )


def downgrade() -> None:
    op.drop_index("ix_ashare_adjusted_price_pit_lookup", table_name="ashare_adjusted_price_facts")
    op.drop_table("ashare_adjusted_price_facts")
    op.drop_index(
        "ix_ashare_theme_membership_pit_lookup", table_name="ashare_theme_membership_facts"
    )
    op.drop_table("ashare_theme_membership_facts")
    op.drop_index(
        "ix_ashare_research_universe_pit_lookup",
        table_name="ashare_research_universe_snapshots",
    )
    op.drop_table("ashare_research_universe_snapshots")
    op.drop_index(
        "ix_leader_tactics_v2_revision_lookup", table_name="leader_tactics_v2_revision_audits"
    )
    op.drop_table("leader_tactics_v2_revision_audits")
    op.drop_table("leader_tactics_v2_checkpoints")
    op.drop_table("leader_tactics_v2_holdout_events")
    op.drop_table("leader_tactics_v2_source_labels")
    op.drop_index(
        "ix_leader_tactics_v2_transition_asset", table_name="leader_tactics_v2_state_transitions"
    )
    op.drop_table("leader_tactics_v2_state_transitions")
    op.drop_index(
        "ix_leader_tactics_v2_candidate_query",
        table_name="leader_tactics_v2_candidate_observations",
    )
    op.drop_table("leader_tactics_v2_candidate_observations")
    op.drop_index(
        "ix_leader_tactics_v2_manifest_universe_cutoff",
        table_name="leader_tactics_v2_run_manifests",
    )
    op.drop_table("leader_tactics_v2_run_manifests")
    op.drop_table("leader_tactics_v2_source_registries")
