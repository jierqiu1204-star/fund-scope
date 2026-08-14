"""add versioned alert policy and strategy provenance to tracked positions"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260814_000070"
down_revision = "20260814_000069"
branch_labels = None
depends_on = None

DEFAULT_POLICY = "standard_dynamic_v2"
DEFAULT_VERSION = "standard_dynamic_v2"


def upgrade() -> None:
    with op.batch_alter_table("tracked_positions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "alert_policy_id",
                sa.String(length=64),
                nullable=False,
                server_default=DEFAULT_POLICY,
            )
        )
        batch_op.add_column(
            sa.Column(
                "alert_policy_version",
                sa.String(length=64),
                nullable=False,
                server_default=DEFAULT_VERSION,
            )
        )
        batch_op.add_column(
            sa.Column(
                "alert_policy_provenance",
                sa.String(length=32),
                nullable=False,
                server_default="default",
            )
        )
        batch_op.add_column(sa.Column("source_strategy", sa.String(length=64)))
        batch_op.add_column(sa.Column("source_manifest_hash", sa.String(length=128)))
        batch_op.add_column(sa.Column("source_decision_at", sa.DateTime()))
        batch_op.create_check_constraint(
            "ck_tracked_position_alert_policy",
            "alert_policy_id IN ('standard_dynamic_v2','late_day_turnaround_t1_v1',"
            "'leader_tactics_exit_v1')",
        )
        batch_op.create_check_constraint(
            "ck_tracked_position_asset_type",
            "asset_type IN ('fund','etf','stock')",
        )
        batch_op.create_check_constraint(
            "ck_tracked_position_alert_provenance",
            "alert_policy_provenance IN ('default','manual_selection','candidate_backed')",
        )
        batch_op.create_check_constraint(
            "ck_tracked_position_late_day_etf_only",
            "alert_policy_id != 'late_day_turnaround_t1_v1' OR asset_type = 'etf'",
        )
        batch_op.create_check_constraint(
            "ck_tracked_position_alert_policy_asset_type",
            "(alert_policy_id = 'standard_dynamic_v2' AND asset_type IN ('fund','etf')) "
            "OR (alert_policy_id = 'late_day_turnaround_t1_v1' AND asset_type = 'etf') "
            "OR (alert_policy_id = 'leader_tactics_exit_v1' AND asset_type IN ('etf','stock'))",
        )
        batch_op.create_check_constraint(
            "ck_tracked_position_candidate_provenance",
            "alert_policy_provenance != 'candidate_backed' OR "
            "(source_strategy IS NOT NULL AND source_manifest_hash IS NOT NULL "
            "AND source_decision_at IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("tracked_positions") as batch_op:
        batch_op.drop_constraint(
            "ck_tracked_position_candidate_provenance", type_="check"
        )
        batch_op.drop_constraint(
            "ck_tracked_position_alert_policy_asset_type", type_="check"
        )
        batch_op.drop_constraint("ck_tracked_position_late_day_etf_only", type_="check")
        batch_op.drop_constraint("ck_tracked_position_asset_type", type_="check")
        batch_op.drop_constraint("ck_tracked_position_alert_provenance", type_="check")
        batch_op.drop_constraint("ck_tracked_position_alert_policy", type_="check")
        batch_op.drop_column("source_decision_at")
        batch_op.drop_column("source_manifest_hash")
        batch_op.drop_column("source_strategy")
        batch_op.drop_column("alert_policy_provenance")
        batch_op.drop_column("alert_policy_version")
        batch_op.drop_column("alert_policy_id")
