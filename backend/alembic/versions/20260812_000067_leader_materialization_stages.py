"""add PIT fine themes and resumable leader-tactics materialization stages"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260812_000067"
down_revision = "20260811_000066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ashare_fine_theme_membership_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("group_id", sa.String(256), nullable=False),
        sa.Column("theme", sa.String(256), nullable=False),
        sa.Column("normalized_theme_key", sa.String(128), nullable=False),
        sa.Column("hierarchy_level", sa.String(32), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("taxonomy_version", sa.String(128), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("mapping_kind", sa.String(32), nullable=False),
        sa.Column("fact_hash", sa.String(128), nullable=False, unique=True),
    )
    op.create_index(
        "ix_ashare_fine_theme_pit_lookup",
        "ashare_fine_theme_membership_facts",
        ["asset_code", "effective_from", "effective_to", "received_at"],
    )
    op.create_index(
        "ix_ashare_fine_theme_snapshot_lookup",
        "ashare_fine_theme_membership_facts",
        ["source", "taxonomy_version", "effective_from", "received_at"],
    )
    op.create_table(
        "leader_tactics_v2_materialization_runs",
        sa.Column("run_hash", sa.String(128), primary_key=True),
        sa.Column("universe", sa.String(16), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("source_cutoff", sa.DateTime(), nullable=False),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("universe_hash", sa.String(128), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("code_version", sa.String(128), nullable=False),
        sa.Column("source_registry_hash", sa.String(128), nullable=False),
        sa.Column("formula_registry_hash", sa.String(128), nullable=False),
        sa.Column("provider_health_json", sa.Text(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "leader_tactics_v2_materialization_features",
        sa.Column("run_hash", sa.String(128), nullable=False),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("asset_name", sa.String(256), nullable=False),
        sa.Column("group_key", sa.String(256), nullable=True),
        sa.Column("feature_json", sa.Text(), nullable=False),
        sa.Column("feature_hash", sa.String(128), nullable=False),
        sa.Column("input_digest", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("run_hash", "asset_code"),
    )
    op.create_index(
        "ix_leader_tactics_v2_materialization_feature_group",
        "leader_tactics_v2_materialization_features",
        ["run_hash", "group_key", "asset_code"],
    )
    op.create_table(
        "leader_tactics_v2_materialization_groups",
        sa.Column("run_hash", sa.String(128), nullable=False),
        sa.Column("group_key", sa.String(256), nullable=False),
        sa.Column("observation_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("run_hash", "group_key"),
    )


def downgrade() -> None:
    op.drop_table("leader_tactics_v2_materialization_groups")
    op.drop_index(
        "ix_leader_tactics_v2_materialization_feature_group",
        table_name="leader_tactics_v2_materialization_features",
    )
    op.drop_table("leader_tactics_v2_materialization_features")
    op.drop_table("leader_tactics_v2_materialization_runs")
    op.drop_index(
        "ix_ashare_fine_theme_snapshot_lookup",
        table_name="ashare_fine_theme_membership_facts",
    )
    op.drop_index(
        "ix_ashare_fine_theme_pit_lookup",
        table_name="ashare_fine_theme_membership_facts",
    )
    op.drop_table("ashare_fine_theme_membership_facts")
