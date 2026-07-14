from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
    TrackedPositionActionTransitionReceipt,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)


def _column_names(model: type) -> set[str]:
    return set(model.__table__.columns.keys())


def _foreign_key_targets(model: type) -> set[str]:
    return {foreign_key.target_fullname for foreign_key in model.__table__.foreign_keys}


def _unique_constraint_names(model: type) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name is not None
    }


def test_lifecycle_schema_has_position_action_execution_and_correlation_fields() -> None:
    assert "exit_state_version" in _column_names(TrackedPosition)
    assert {
        "user_id",
        "tracked_position_id",
        "position_episode_id",
        "exposure_version",
        "policy_version",
        "action_cycle_id",
        "target_stage",
        "target_remaining_fraction",
        "baseline_normalized_quantity",
        "baseline_account_weight",
        "baseline_adjustment_factor",
        "baseline_source",
        "target_normalized_quantity",
        "target_account_weight",
        "input_snapshot_hash",
        "data_state",
        "status",
        "execution_provenance",
        "cumulative_executed_quantity",
        "contributing_rules_json",
        "alert_episode_ids_json",
        "valid_until",
        "is_current",
        "superseded_by_action_id",
    } <= _column_names(TrackedPositionActionDecision)
    assert {
        "action_decision_id",
        "user_id",
        "tracked_position_id",
        "idempotency_key",
        "request_hash",
        "execution_provenance",
        "execution_quantity",
        "execution_price",
        "price_source",
        "fees",
        "before_normalized_quantity",
        "resulting_normalized_quantity",
        "resulting_position_state_version",
        "executed_at",
        "actor_id",
        "request_id",
    } <= _column_names(TrackedPositionActionExecution)
    assert {
        "action_decision_id",
        "user_id",
        "tracked_position_id",
        "idempotency_key",
        "transition",
        "request_hash",
        "response_json",
    } <= _column_names(TrackedPositionActionTransitionReceipt)

    assert {"users.id", "tracked_positions.id"} <= _foreign_key_targets(TrackedPositionActionDecision)
    assert {
        "tracked_position_action_decisions.id",
        "users.id",
        "tracked_positions.id",
    } <= _foreign_key_targets(TrackedPositionActionExecution)
    assert {
        "tracked_position_action_decisions.id",
        "users.id",
        "tracked_positions.id",
    } <= _foreign_key_targets(TrackedPositionActionTransitionReceipt)

    correlation_fields = {
        "alert_episode_id",
        "alert_transition",
        "action_decision_id",
        "notification_item_id",
        "notification_envelope_id",
        "policy_version",
        "data_state",
    }
    assert correlation_fields <= _column_names(TrackedPositionAlert)
    assert correlation_fields | {
        "event_id",
        "event_schema_version",
        "from_state",
        "to_state",
        "actor_id",
        "request_id",
        "causation_id",
        "occurred_at",
        "execution_provenance",
    } <= _column_names(
        TrackedPositionAlertAudit
    )


def test_notification_item_and_envelope_schema_keep_double_identity() -> None:
    assert {
        "user_id",
        "tracked_position_id",
        "action_decision_id",
        "alert_episode_id",
        "transition",
        "recipient",
        "channel",
        "repeat_slot",
        "route",
        "severity",
        "payload_json",
        "envelope_id",
    } <= _column_names(TrackedPositionNotificationItem)
    assert {
        "user_id",
        "trade_session",
        "route",
        "severity",
        "channel",
        "sealed_snapshot_hash",
        "digest_revision",
        "status",
        "claim_token",
        "claimed_by",
        "lease_expires_at",
        "message_id",
    } <= _column_names(TrackedPositionNotificationEnvelope)


def test_lifecycle_uniqueness_and_current_slot_constraints_are_named() -> None:
    assert "uq_tracked_action_target_stage" in _unique_constraint_names(TrackedPositionActionDecision)
    assert "uq_tracked_action_execution_request" in _unique_constraint_names(TrackedPositionActionExecution)
    assert "uq_tracked_action_transition_request" in _unique_constraint_names(
        TrackedPositionActionTransitionReceipt
    )
    assert "uq_tracked_notification_item_identity" in _unique_constraint_names(TrackedPositionNotificationItem)
    assert "uq_tracked_notification_envelope_identity" in _unique_constraint_names(TrackedPositionNotificationEnvelope)

    action_indexes = {index.name: index for index in TrackedPositionActionDecision.__table__.indexes}
    assert action_indexes["uq_tracked_action_current_slot"].unique is True
    assert "ix_tracked_action_history" in action_indexes
    assert "ix_tracked_notification_item_repeat" in {
        index.name for index in TrackedPositionNotificationItem.__table__.indexes
    }
    assert "ix_tracked_notification_item_envelope" in {
        index.name for index in TrackedPositionNotificationItem.__table__.indexes
    }
    assert "ix_tracked_notification_envelope_pending" in {
        index.name for index in TrackedPositionNotificationEnvelope.__table__.indexes
    }
    assert "ix_tracked_action_execution_history" in {
        index.name for index in TrackedPositionActionExecution.__table__.indexes
    }
    assert "ix_tracked_alert_audit_cursor" in {
        index.name for index in TrackedPositionAlertAudit.__table__.indexes
    }
