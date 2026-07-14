"""add ETF alert action lifecycle persistence

Revision ID: 20260714_000039
Revises: 20260712_000038
Create Date: 2026-07-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_000039"
down_revision: str | None = "20260712_000038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracked_positions",
        sa.Column("exit_state_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "tracked_position_action_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tracked_position_id", sa.Integer(), nullable=False),
        sa.Column("position_episode_id", sa.String(length=64), nullable=False),
        sa.Column("exposure_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("action_cycle_id", sa.String(length=64), nullable=False),
        sa.Column("target_stage", sa.String(length=32), nullable=False),
        sa.Column("target_remaining_fraction", sa.Float(), nullable=False),
        sa.Column("baseline_normalized_quantity", sa.Float(), nullable=False),
        sa.Column("baseline_account_weight", sa.Float(), nullable=True),
        sa.Column("baseline_adjustment_factor", sa.Float(), server_default="1", nullable=False),
        sa.Column("baseline_source", sa.String(length=64), nullable=False),
        sa.Column("target_normalized_quantity", sa.Float(), nullable=False),
        sa.Column("target_account_weight", sa.Float(), nullable=True),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("data_state", sa.String(length=32), server_default="eligible", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="proposed", nullable=False),
        sa.Column("execution_provenance", sa.String(length=32), server_default="none", nullable=False),
        sa.Column("cumulative_executed_quantity", sa.Float(), server_default="0", nullable=False),
        sa.Column("contributing_rules_json", sa.JSON(), nullable=False),
        sa.Column("alert_episode_ids_json", sa.JSON(), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.DateTime(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("superseded_by_action_id", sa.Integer(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("expired_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed','acknowledged','partially_executed','executed','expired','cancelled','superseded')",
            name="ck_tracked_action_status",
        ),
        sa.CheckConstraint(
            "target_remaining_fraction >= 0 AND target_remaining_fraction <= 1",
            name="ck_tracked_action_target_fraction",
        ),
        sa.ForeignKeyConstraint(["tracked_position_id"], ["tracked_positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["superseded_by_action_id"],
            ["tracked_position_action_decisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_episode_id",
            "exposure_version",
            "policy_version",
            "action_cycle_id",
            "target_stage",
            name="uq_tracked_action_target_stage",
        ),
    )
    op.create_index(
        "uq_tracked_action_current_slot",
        "tracked_position_action_decisions",
        ["tracked_position_id"],
        unique=True,
        sqlite_where=sa.text("is_current = 1"),
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_tracked_action_history",
        "tracked_position_action_decisions",
        ["tracked_position_id", "created_at", "id"],
    )

    op.create_table(
        "tracked_position_action_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action_decision_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tracked_position_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "execution_provenance",
            sa.String(length=32),
            server_default="owner_confirmed",
            nullable=False,
        ),
        sa.Column("execution_quantity", sa.Float(), nullable=False),
        sa.Column("execution_price", sa.Float(), nullable=False),
        sa.Column("price_source", sa.String(length=64), nullable=False),
        sa.Column("fees", sa.Float(), server_default="0", nullable=False),
        sa.Column("before_normalized_quantity", sa.Float(), nullable=False),
        sa.Column("resulting_normalized_quantity", sa.Float(), nullable=False),
        sa.Column("resulting_position_state_version", sa.Integer(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("execution_price > 0", name="ck_tracked_execution_price"),
        sa.CheckConstraint("execution_quantity > 0", name="ck_tracked_execution_quantity"),
        sa.CheckConstraint("fees >= 0", name="ck_tracked_execution_fees"),
        sa.ForeignKeyConstraint(
            ["action_decision_id"],
            ["tracked_position_action_decisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tracked_position_id"], ["tracked_positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_tracked_action_execution_request"),
    )
    op.create_index(
        "ix_tracked_action_execution_history",
        "tracked_position_action_executions",
        ["action_decision_id", "executed_at", "id"],
    )

    op.create_table(
        "tracked_position_notification_envelopes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("trade_session", sa.Date(), nullable=False),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("sealed_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("digest_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("claim_token", sa.String(length=128), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("sealed_at", sa.DateTime(), nullable=True),
        sa.Column("first_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("smtp_accepted_at", sa.DateTime(), nullable=True),
        sa.Column("rendered_subject", sa.Text(), nullable=True),
        sa.Column("rendered_body", sa.Text(), nullable=True),
        sa.Column("last_error_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "trade_session",
            "route",
            "severity",
            "channel",
            "sealed_snapshot_hash",
            "digest_revision",
            name="uq_tracked_notification_envelope_identity",
        ),
    )
    op.create_index(
        "ix_tracked_notification_envelope_pending",
        "tracked_position_notification_envelopes",
        ["status", "lease_expires_at", "id"],
    )

    op.create_table(
        "tracked_position_notification_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tracked_position_id", sa.Integer(), nullable=False),
        sa.Column("action_decision_id", sa.Integer(), nullable=True),
        sa.Column("alert_episode_id", sa.String(length=64), nullable=False),
        sa.Column("transition", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("repeat_slot", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("envelope_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_decision_id"],
            ["tracked_position_action_decisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["envelope_id"],
            ["tracked_position_notification_envelopes.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["tracked_position_id"], ["tracked_positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "alert_episode_id",
            "transition",
            "recipient",
            "channel",
            "repeat_slot",
            name="uq_tracked_notification_item_identity",
        ),
    )
    op.create_index(
        "ix_tracked_notification_item_repeat",
        "tracked_position_notification_items",
        ["tracked_position_id", "repeat_slot", "created_at", "id"],
    )
    op.create_index(
        "ix_tracked_notification_item_envelope",
        "tracked_position_notification_items",
        ["envelope_id", "id"],
    )

    with op.batch_alter_table("tracked_position_alerts") as batch_op:
        batch_op.add_column(sa.Column("alert_episode_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("alert_transition", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("action_decision_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("notification_item_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("notification_envelope_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("policy_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("data_state", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_tracked_alert_action_decision",
            "tracked_position_action_decisions",
            ["action_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tracked_alert_notification_item",
            "tracked_position_notification_items",
            ["notification_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tracked_alert_notification_envelope",
            "tracked_position_notification_envelopes",
            ["notification_envelope_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("tracked_position_alert_audits") as batch_op:
        batch_op.add_column(sa.Column("event_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("event_schema_version", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("alert_episode_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("alert_transition", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("action_decision_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("notification_item_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("notification_envelope_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("policy_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("data_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("from_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("to_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("actor_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("request_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("causation_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("occurred_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("execution_provenance", sa.String(length=32), nullable=True))
        batch_op.create_unique_constraint("uq_tracked_alert_audit_event_id", ["event_id"])
        batch_op.create_foreign_key(
            "fk_tracked_audit_action_decision",
            "tracked_position_action_decisions",
            ["action_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tracked_audit_notification_item",
            "tracked_position_notification_items",
            ["notification_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tracked_audit_notification_envelope",
            "tracked_position_notification_envelopes",
            ["notification_envelope_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tracked_audit_actor",
            "users",
            ["actor_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_tracked_alert_audit_cursor",
        "tracked_position_alert_audits",
        ["tracked_position_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracked_alert_audit_cursor", table_name="tracked_position_alert_audits")
    with op.batch_alter_table("tracked_position_alert_audits") as batch_op:
        batch_op.drop_constraint("fk_tracked_audit_actor", type_="foreignkey")
        batch_op.drop_constraint("fk_tracked_audit_notification_envelope", type_="foreignkey")
        batch_op.drop_constraint("fk_tracked_audit_notification_item", type_="foreignkey")
        batch_op.drop_constraint("fk_tracked_audit_action_decision", type_="foreignkey")
        batch_op.drop_constraint("uq_tracked_alert_audit_event_id", type_="unique")
        for column in (
            "execution_provenance",
            "occurred_at",
            "causation_id",
            "request_id",
            "actor_id",
            "to_state",
            "from_state",
            "data_state",
            "policy_version",
            "notification_envelope_id",
            "notification_item_id",
            "action_decision_id",
            "alert_transition",
            "alert_episode_id",
            "event_schema_version",
            "event_id",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("tracked_position_alerts") as batch_op:
        batch_op.drop_constraint("fk_tracked_alert_notification_envelope", type_="foreignkey")
        batch_op.drop_constraint("fk_tracked_alert_notification_item", type_="foreignkey")
        batch_op.drop_constraint("fk_tracked_alert_action_decision", type_="foreignkey")
        for column in (
            "data_state",
            "policy_version",
            "notification_item_id",
            "notification_envelope_id",
            "action_decision_id",
            "alert_transition",
            "alert_episode_id",
        ):
            batch_op.drop_column(column)
    op.drop_index("ix_tracked_notification_item_repeat", table_name="tracked_position_notification_items")
    op.drop_index("ix_tracked_notification_item_envelope", table_name="tracked_position_notification_items")
    op.drop_table("tracked_position_notification_items")
    op.drop_index(
        "ix_tracked_notification_envelope_pending",
        table_name="tracked_position_notification_envelopes",
    )
    op.drop_table("tracked_position_notification_envelopes")
    op.drop_index("ix_tracked_action_execution_history", table_name="tracked_position_action_executions")
    op.drop_table("tracked_position_action_executions")
    op.drop_index("ix_tracked_action_history", table_name="tracked_position_action_decisions")
    op.drop_index("uq_tracked_action_current_slot", table_name="tracked_position_action_decisions")
    op.drop_table("tracked_position_action_decisions")
    op.drop_column("tracked_positions", "exit_state_version")
