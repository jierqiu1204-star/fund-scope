"""Add the append-only A-share multilayer theme graph research stores."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260819_000073"
down_revision = "20260817_000072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ashare_industry_path_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("taxonomy", sa.String(64), nullable=False),
        sa.Column("taxonomy_version", sa.String(128), nullable=False),
        sa.Column("mapping_kind", sa.String(32), nullable=False),
        sa.Column("level1_code", sa.String(64), nullable=True),
        sa.Column("level1_label", sa.String(256), nullable=True),
        sa.Column("level2_code", sa.String(64), nullable=True),
        sa.Column("level2_label", sa.String(256), nullable=True),
        sa.Column("level3_code", sa.String(64), nullable=True),
        sa.Column("level3_label", sa.String(256), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("fact_hash", sa.String(128), nullable=False, unique=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ashare_industry_path_confidence",
        ),
        sa.UniqueConstraint(
            "asset_code",
            "taxonomy",
            "taxonomy_version",
            "effective_from",
            "source",
            name="uq_ashare_industry_path_primary_session",
        ),
    )
    op.create_index(
        "ix_ashare_industry_path_pit_lookup",
        "ashare_industry_path_facts",
        ["asset_code", "taxonomy", "effective_from", "effective_to", "received_at"],
    )
    op.create_index(
        "ix_ashare_industry_path_snapshot_lookup",
        "ashare_industry_path_facts",
        ["source", "taxonomy_version", "snapshot_date", "received_at"],
    )

    op.create_table(
        "ashare_theme_capture_runs",
        sa.Column("run_hash", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.String(256), nullable=False),
        sa.Column("source_snapshot_date", sa.Date(), nullable=False),
        sa.Column("taxonomy_version", sa.String(128), nullable=False),
        sa.Column("registry_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=True),
        sa.Column("cursor", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("error_reason", sa.String(512), nullable=True),
        sa.CheckConstraint(
            "expected_count >= 0 AND completed_count >= 0 "
            "AND completed_count <= expected_count",
            name="ck_ashare_theme_capture_counts",
        ),
        sa.CheckConstraint(
            "status IN ('partial', 'complete', 'failed')",
            name="ck_ashare_theme_capture_status",
        ),
    )
    op.create_index(
        "ix_ashare_theme_capture_source_snapshot",
        "ashare_theme_capture_runs",
        ["source", "source_snapshot_date", "status", "received_at"],
    )
    op.create_index(
        "ix_ashare_theme_capture_resume",
        "ashare_theme_capture_runs",
        ["source_snapshot_id", "status", "received_at"],
    )

    op.create_table(
        "ashare_theme_state_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("context_kind", sa.String(32), nullable=False),
        sa.Column("context_key", sa.String(256), nullable=False),
        sa.Column("state_date", sa.Date(), nullable=False),
        sa.Column("source_cutoff", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("registry_version", sa.String(128), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("eligible_member_count", sa.Integer(), nullable=False),
        sa.Column("up_member_count", sa.Integer(), nullable=False),
        sa.Column("up_breadth", sa.Float(), nullable=True),
        sa.Column("median_return_1d", sa.Float(), nullable=True),
        sa.Column("median_return_5d", sa.Float(), nullable=True),
        sa.Column("relative_market_return_1d", sa.Float(), nullable=True),
        sa.Column("amount_participation", sa.Float(), nullable=True),
        sa.Column("leader_count", sa.Integer(), nullable=True),
        sa.Column("limit_up_count", sa.Integer(), nullable=True),
        sa.Column("limit_down_count", sa.Integer(), nullable=True),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("unavailable_reasons_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("state_hash", sa.String(128), nullable=False, unique=True),
        sa.CheckConstraint(
            "eligible_member_count >= 0 AND up_member_count >= 0 "
            "AND up_member_count <= eligible_member_count",
            name="ck_ashare_theme_state_counts",
        ),
        sa.UniqueConstraint(
            "context_kind",
            "context_key",
            "state_date",
            "input_hash",
            name="uq_ashare_theme_state_input",
        ),
    )
    op.create_index(
        "ix_ashare_theme_state_pit_lookup",
        "ashare_theme_state_facts",
        ["context_kind", "context_key", "state_date", "source_cutoff", "received_at"],
    )
    op.create_index(
        "ix_ashare_theme_state_snapshot_lookup",
        "ashare_theme_state_facts",
        ["source_snapshot_hash", "state_date", "available"],
    )

    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("provider_theme_code", sa.String(128), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("provider_theme_label", sa.String(256), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("membership_reason", sa.String(512), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("exposure_weight", sa.Float(), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("relation_kind", sa.String(32), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("source_snapshot_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("source_snapshot_hash", sa.String(128), nullable=True),
    )
    op.add_column(
        "ashare_fine_theme_membership_facts",
        sa.Column("capture_run_hash", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_ashare_fine_theme_context_pit",
        "ashare_fine_theme_membership_facts",
        ["normalized_theme_key", "effective_from", "received_at", "asset_code"],
    )
    op.create_index(
        "ix_ashare_fine_theme_source_snapshot",
        "ashare_fine_theme_membership_facts",
        ["source", "source_snapshot_date", "source_snapshot_hash", "asset_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ashare_fine_theme_source_snapshot",
        table_name="ashare_fine_theme_membership_facts",
    )
    op.drop_index(
        "ix_ashare_fine_theme_context_pit",
        table_name="ashare_fine_theme_membership_facts",
    )
    for column_name in (
        "capture_run_hash",
        "source_snapshot_hash",
        "source_snapshot_date",
        "relation_kind",
        "exposure_weight",
        "membership_reason",
        "provider_theme_label",
        "provider_theme_code",
    ):
        op.drop_column("ashare_fine_theme_membership_facts", column_name)

    op.drop_index(
        "ix_ashare_theme_state_snapshot_lookup",
        table_name="ashare_theme_state_facts",
    )
    op.drop_index(
        "ix_ashare_theme_state_pit_lookup",
        table_name="ashare_theme_state_facts",
    )
    op.drop_table("ashare_theme_state_facts")

    op.drop_index(
        "ix_ashare_theme_capture_resume",
        table_name="ashare_theme_capture_runs",
    )
    op.drop_index(
        "ix_ashare_theme_capture_source_snapshot",
        table_name="ashare_theme_capture_runs",
    )
    op.drop_table("ashare_theme_capture_runs")

    op.drop_index(
        "ix_ashare_industry_path_snapshot_lookup",
        table_name="ashare_industry_path_facts",
    )
    op.drop_index(
        "ix_ashare_industry_path_pit_lookup",
        table_name="ashare_industry_path_facts",
    )
    op.drop_table("ashare_industry_path_facts")
