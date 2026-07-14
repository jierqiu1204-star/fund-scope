from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    TrackedPositionLifecycleShadowEvidence,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.risk_alerts import EvaluatedRiskRule
from app.services.tracked_positions import lifecycle_rollout as rollout_module
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    persist_action_decision,
)
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)
from app.services.workflows import tracked_position_lifecycle as lifecycle_workflow
from app.services.workflows.tracked_position_lifecycle import (
    AUDIT_EVENT_SCHEMA_VERSION,
    MAX_AUDIT_CONTEXT_BYTES,
    LifecycleEvaluationCommand,
    LifecycleEvaluationResult,
    orchestrate_position_lifecycle_evaluation,
)

ACTIVE_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
SHADOW_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.SHADOW)


async def _position(app) -> int:
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="513520",
            asset_name="日经ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0,
            buy_amount=1000.0,
            entry_price=1.0,
            estimated_shares=1000.0,
            exit_state_json={
                "position_episode_id": "position-episode-1",
                "exposure_version": 1,
                "exposure_baseline": {
                    "normalized_quantity": 1000.0,
                    "account_weight": 0.3,
                    "source": "confirmed_shares",
                    "adjustment_factor": 1.0,
                },
                "alert_rule_states": {},
            },
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        return position.id


def _command(position_id: int) -> LifecycleEvaluationCommand:
    return LifecycleEvaluationCommand(
        owner_id=1,
        position_id=position_id,
        expected_position_state_version=0,
        policy_version="policy-v2",
        sealed_snapshot_hash="a" * 64,
        trade_session=date(2026, 7, 14),
        repeat_slot="2026-07-14:close",
        recipient="owner@example.com",
        evaluations=(
            EvaluatedRiskRule(
                rule_id="hard_stop",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=0.0,
                reason="loss breached hard stop",
                hard_stop=True,
                confirmation_required=1,
                recovery_required=2,
            ),
        ),
        occurred_at=datetime(2026, 7, 14, 15, 0),
        eligible_data_time=datetime(2026, 7, 14, 15, 0),
        cutoff_time=datetime(2026, 7, 14, 15, 0),
        actor_id=1,
        request_id="request-1",
        causation_id="quote-snapshot-1",
        data_source="eastmoney_adjusted",
        quote_freshness="close_final",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        LifecycleRolloutMode.DISABLED,
        LifecycleRolloutMode.READ_ONLY,
        LifecycleRolloutMode.DISPLAY_ONLY,
    ],
)
async def test_non_production_rollout_modes_leave_all_production_facts_unchanged(
    app, mode: LifecycleRolloutMode
) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            PersistActionDecisionCommand(
                owner_id=1,
                position_id=position_id,
                expected_exit_state_version=0,
                position_episode_id="position-episode-1",
                exposure_version=1,
                policy_version="policy-v1",
                action_cycle_id="cycle-v1",
                target_remaining_fraction=0.5,
                baseline_normalized_quantity=1000.0,
                baseline_account_weight=0.3,
                baseline_adjustment_factor=1.0,
                baseline_source="confirmed_shares",
                current_normalized_quantity=1000.0,
                input_snapshot_hash="9" * 64,
                data_state="eligible",
                contributing_rules=("trailing_take_profit",),
                alert_episode_ids=("episode-v1",),
            ),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()
        action_id = persisted.action.id

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        assert position is not None
        assert action is not None
        before_position = (
            position.exit_state_version,
            deepcopy(position.exit_state_json),
            position.confirmed_shares,
            position.estimated_shares,
            position.buy_amount,
            position.status,
        )
        before_action = (
            action.status,
            action.status_reason,
            action.is_current,
            action.superseded_at,
            action.superseded_by_action_id,
        )
        before_counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                TrackedPositionActionDecision,
                TrackedPositionNotificationItem,
                TrackedPositionNotificationEnvelope,
                TrackedPositionAlertAudit,
                TrackedPositionLifecycleShadowEvidence,
            )
        }

    command = replace(
        _command(position_id),
        expected_position_state_version=1,
        policy_version="policy-v2",
        sealed_snapshot_hash="8" * 64,
    )
    async with app.state.db.session() as session:
        result = await orchestrate_position_lifecycle_evaluation(
            session,
            command,
            rollout_policy=LifecycleRolloutPolicy.for_mode(mode),
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        assert position is not None
        assert action is not None
        after_counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                TrackedPositionActionDecision,
                TrackedPositionNotificationItem,
                TrackedPositionNotificationEnvelope,
                TrackedPositionAlertAudit,
                TrackedPositionLifecycleShadowEvidence,
            )
        }
        assert (
            position.exit_state_version,
            position.exit_state_json,
            position.confirmed_shares,
            position.estimated_shares,
            position.buy_amount,
            position.status,
        ) == before_position
        assert (
            action.status,
            action.status_reason,
            action.is_current,
            action.superseded_at,
            action.superseded_by_action_id,
        ) == before_action
        assert after_counts == before_counts
        assert result.position_state_version == before_position[0]
        assert result.audit_event_id is None


@pytest.mark.asyncio
async def test_safe_rollback_entry_preserves_existing_v2_facts_without_side_effects(
    app,
) -> None:
    position_id = await _position(app)
    active_result = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        _command(position_id),
        rollout_policy=ACTIVE_ROLLOUT,
    )
    assert active_result.action_id is not None

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, active_result.action_id)
        assert position is not None
        assert action is not None
        before_position = (
            position.exit_state_version,
            deepcopy(position.exit_state_json),
            position.status,
        )
        before_action = (
            action.status,
            action.status_reason,
            action.is_current,
            action.superseded_at,
            action.superseded_by_action_id,
        )
        before_counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                TrackedPositionActionDecision,
                TrackedPositionNotificationItem,
                TrackedPositionNotificationEnvelope,
                TrackedPositionAlertAudit,
                TrackedPositionLifecycleShadowEvidence,
            )
        }

    rollback_result = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        replace(
            _command(position_id),
            expected_position_state_version=active_result.position_state_version,
            policy_version="policy-v3",
            sealed_snapshot_hash="8" * 64,
            trade_session=date(2026, 7, 15),
            repeat_slot="2026-07-15:close",
            occurred_at=datetime(2026, 7, 15, 15, 0),
        ),
        rollout_policy=rollout_module.safe_rollback_policy(),
    )

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, active_result.action_id)
        assert position is not None
        assert action is not None
        after_counts = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (
                TrackedPositionActionDecision,
                TrackedPositionNotificationItem,
                TrackedPositionNotificationEnvelope,
                TrackedPositionAlertAudit,
                TrackedPositionLifecycleShadowEvidence,
            )
        }
        assert (
            position.exit_state_version,
            position.exit_state_json,
            position.status,
        ) == before_position
        assert (
            action.status,
            action.status_reason,
            action.is_current,
            action.superseded_at,
            action.superseded_by_action_id,
        ) == before_action
        assert after_counts == before_counts
    assert rollback_result.outcome == "display_only_no_write"
    assert rollback_result.audit_event_id is None


@pytest.mark.asyncio
async def test_no_write_entry_leaves_no_side_effects_after_validation_error(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        before_position = await session.get(TrackedPosition, position_id)
        assert before_position is not None
        before_state = (
            before_position.exit_state_version,
            deepcopy(before_position.exit_state_json),
        )

    with pytest.raises(ValueError, match="at least one evaluated rule is required"):
        await lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            replace(_command(position_id), evaluations=()),
            rollout_policy=rollout_module.safe_rollback_policy(),
        )

    async with app.state.db.session() as session:
        after_position = await session.get(TrackedPosition, position_id)
        assert after_position is not None
        assert (
            after_position.exit_state_version,
            after_position.exit_state_json,
        ) == before_state
        for model in (
            TrackedPositionActionDecision,
            TrackedPositionNotificationItem,
            TrackedPositionNotificationEnvelope,
            TrackedPositionAlertAudit,
            TrackedPositionLifecycleShadowEvidence,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_transaction_entry_rejects_cross_owner_shadow_evidence(app) -> None:
    position_id = await _position(app)

    with pytest.raises(LookupError, match="tracked position not found for owner"):
        await lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            replace(_command(position_id), owner_id=2),
            rollout_policy=SHADOW_ROLLOUT,
        )

    async with app.state.db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(TrackedPositionLifecycleShadowEvidence)
        )
    assert count == 0


@pytest.mark.asyncio
async def test_lifecycle_workflow_commits_state_action_audit_and_item_atomically(app) -> None:
    position_id = await _position(app)

    async with app.state.db.session() as session:
        rolled_back = await orchestrate_position_lifecycle_evaluation(
            session,
            _command(position_id),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        assert rolled_back.action_id is not None
        assert await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(TrackedPositionNotificationItem)
        ) == 1
        await session.rollback()

    async with app.state.db.session() as session:
        assert await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        ) == 0
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        assert position.exit_state_version == 0

    async with app.state.db.session() as session:
        result = await orchestrate_position_lifecycle_evaluation(
            session,
            _command(position_id),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.scalar(select(TrackedPositionActionDecision))
        audit = await session.scalar(select(TrackedPositionAlertAudit))
        item = await session.scalar(select(TrackedPositionNotificationItem))

    assert result.outcome == "transitioned"
    assert position is not None
    assert position.exit_state_version == 1
    rule_state = position.exit_state_json["alert_rule_states"]["hard_stop"]
    assert rule_state["state"] == "firing"
    assert rule_state["alert_episode_id"]
    assert action is not None
    assert action.id == result.action_id
    assert action.target_remaining_fraction == 0.0
    assert action.status == "proposed"
    assert audit is not None
    assert audit.event_id == result.audit_event_id
    assert audit.event_schema_version == AUDIT_EVENT_SCHEMA_VERSION
    assert audit.from_state == "normal"
    assert audit.to_state == "firing"
    assert audit.actor_id == 1
    assert audit.request_id == "request-1"
    assert audit.causation_id == "quote-snapshot-1"
    assert audit.occurred_at == datetime(2026, 7, 14, 15, 0)
    assert audit.created_at is not None
    assert len(str(audit.decision_context_json).encode("utf-8")) <= MAX_AUDIT_CONTEXT_BYTES
    assert item is not None
    assert item.action_decision_id == action.id
    assert item.repeat_slot == "2026-07-14:close"
    assert item.payload_json["automatic_execution"] is False


@pytest.mark.asyncio
async def test_unchanged_polls_write_one_snapshot_slot_audit_without_suppressed_rows(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        position.exit_state_json = {
            **dict(position.exit_state_json or {}),
            "policy_version": "policy-v2",
        }
        await session.commit()

    unchanged = replace(
        _command(position_id),
        evaluations=(
            EvaluatedRiskRule(
                rule_id="hard_stop",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=False,
                recovery_met=False,
                target_remaining_fraction=0.0,
                reason="hard stop not breached",
                hard_stop=True,
                confirmation_required=1,
                recovery_required=2,
            ),
        ),
    )

    async with app.state.db.session() as session:
        first = await orchestrate_position_lifecycle_evaluation(
            session, unchanged, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        retry = await orchestrate_position_lifecycle_evaluation(
            session, unchanged, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        later_slot = await orchestrate_position_lifecycle_evaluation(
            session,
            replace(unchanged, repeat_slot="2026-07-14:close:2"),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    async with app.state.db.session() as session:
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )
        alert_row_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlert)
        )
        action_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        )
        item_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionNotificationItem)
        )
        position = await session.get(TrackedPosition, position_id)

    assert first.outcome == "unchanged"
    assert retry.outcome == "duplicate"
    assert later_slot.outcome == "unchanged"
    assert audit_count == 2
    assert alert_row_count == 0
    assert action_count == 0
    assert item_count == 0
    assert position is not None
    assert position.exit_state_version == 0


@pytest.mark.asyncio
async def test_hard_stop_action_is_reused_by_fresh_reminders_rules_and_retry(app) -> None:
    position_id = await _position(app)
    first_command = _command(position_id)
    async with app.state.db.session() as session:
        first = await orchestrate_position_lifecycle_evaluation(
            session, first_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    repeat_command = replace(
        first_command,
        expected_position_state_version=1,
        sealed_snapshot_hash="b" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        notification_repeat=True,
    )
    async with app.state.db.session() as session:
        repeat = await orchestrate_position_lifecycle_evaluation(
            session, repeat_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    second_rule = EvaluatedRiskRule(
        rule_id="forced_exit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.0,
        reason="a second rule confirms the same absolute exit target",
        hard_stop=True,
        confirmation_required=1,
        recovery_required=2,
    )
    escalated_command = replace(
        repeat_command,
        sealed_snapshot_hash="c" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        notification_repeat=False,
        evaluations=(*repeat_command.evaluations, second_rule),
    )
    async with app.state.db.session() as session:
        second_rule_result = await orchestrate_position_lifecycle_evaluation(
            session, escalated_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        smtp_style_retry = await orchestrate_position_lifecycle_evaluation(
            session, escalated_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        actions = (await session.scalars(select(TrackedPositionActionDecision))).all()
        items = (await session.scalars(select(TrackedPositionNotificationItem))).all()
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )

    assert len(actions) == 1
    assert first.action_id == actions[0].id
    assert repeat.action_id == actions[0].id
    assert second_rule_result.action_id == actions[0].id
    assert smtp_style_retry.outcome == "duplicate"
    assert set(actions[0].contributing_rules_json) == {"forced_exit", "hard_stop"}
    assert len(items) == 3
    assert all(item.action_decision_id == actions[0].id for item in items)
    assert audit_count == 3


@pytest.mark.asyncio
async def test_same_slot_and_user_silence_suppress_delivery_but_keep_action_and_audit(app) -> None:
    position_id = await _position(app)
    first_command = replace(_command(position_id), user_silenced=True)
    async with app.state.db.session() as session:
        first = await orchestrate_position_lifecycle_evaluation(
            session, first_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    same_slot = replace(
        first_command,
        expected_position_state_version=1,
        sealed_snapshot_hash="d" * 64,
        notification_repeat=True,
        user_silenced=False,
    )
    async with app.state.db.session() as session:
        repeated = await orchestrate_position_lifecycle_evaluation(
            session, same_slot, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        actions = list((await session.scalars(select(TrackedPositionActionDecision))).all())
        audits = list((await session.scalars(select(TrackedPositionAlertAudit))).all())
        items = list(
            (
                await session.scalars(
                    select(TrackedPositionNotificationItem).order_by(
                        TrackedPositionNotificationItem.id
                    )
                )
            ).all()
        )

    assert first.action_id == repeated.action_id == actions[0].id
    assert actions[0].status == "proposed"
    assert len(actions) == 1
    assert len(audits) == 2
    assert [(item.status, item.suppression_reason) for item in items] == [
        ("suppressed", "user_silenced"),
        ("suppressed", "same_repeat_slot"),
    ]


@pytest.mark.asyncio
async def test_invalid_data_repeat_keeps_action_and_emits_only_data_status(app) -> None:
    position_id = await _position(app)
    first_command = _command(position_id)
    async with app.state.db.session() as session:
        first = await orchestrate_position_lifecycle_evaluation(
            session, first_command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    invalid_repeat = replace(
        first_command,
        expected_position_state_version=1,
        sealed_snapshot_hash="e" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 14, 15, 0),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
        notification_repeat=True,
        evaluations=(
            EvaluatedRiskRule(
                rule_id="hard_stop",
                data_state="data_waiting",
                data_reason_code="adjusted_close_not_final",
                condition_met=False,
                recovery_met=False,
                target_remaining_fraction=0.0,
                reason="current adjusted close is unavailable",
                hard_stop=True,
                confirmation_required=1,
                recovery_required=2,
            ),
        ),
    )
    async with app.state.db.session() as session:
        repeated = await orchestrate_position_lifecycle_evaluation(
            session, invalid_repeat, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        actions = list((await session.scalars(select(TrackedPositionActionDecision))).all())
        item = await session.scalar(
            select(TrackedPositionNotificationItem).where(
                TrackedPositionNotificationItem.repeat_slot == "2026-07-15:close"
            )
        )
        audit = await session.scalar(
            select(TrackedPositionAlertAudit).where(
                TrackedPositionAlertAudit.alert_date == date(2026, 7, 15)
            )
        )

    assert first.action_id == repeated.action_id == actions[0].id
    assert len(actions) == 1 and actions[0].status == "proposed"
    assert item is not None
    assert item.transition == "current_data_unverifiable"
    assert item.route == "data_status"
    assert item.payload_json["notification_kind"] == "data_status"
    assert "action_id" not in item.payload_json
    assert not any(key.startswith("target_") for key in item.payload_json)
    assert "current_price" not in item.payload_json
    assert audit is not None
    assert audit.decision_context_json["data_reason_code"] == "adjusted_close_not_final"


@pytest.mark.asyncio
async def test_hard_stop_inhibits_watch_delivery_without_hiding_watch_evaluation(app) -> None:
    position_id = await _position(app)
    command = replace(
        _command(position_id),
        evaluations=(
            *_command(position_id).evaluations,
            EvaluatedRiskRule(
                rule_id="take_profit_watch",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=None,
                reason="profit reached the watch threshold",
                hard_stop=False,
                confirmation_required=1,
                recovery_required=1,
            ),
        ),
    )
    async with app.state.db.session() as session:
        result = await orchestrate_position_lifecycle_evaluation(
            session, command, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        action_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        )
        items = list(
            (
                await session.scalars(
                    select(TrackedPositionNotificationItem).order_by(
                        TrackedPositionNotificationItem.id
                    )
                )
            ).all()
        )
        audit = await session.get(TrackedPositionAlertAudit, 1)

    assert result.action_id is not None
    assert action_count == 1
    assert len(items) == 2
    hard_stop_item = next(item for item in items if item.route == "urgent_hard_stop")
    watch_item = next(
        item for item in items if item.payload_json["notification_kind"] == "watch"
    )
    assert hard_stop_item.status == "pending"
    assert watch_item.status == "suppressed"
    assert watch_item.suppression_reason == "higher_severity_active"
    assert watch_item.action_decision_id is None
    assert audit is not None
    assert set(audit.decision_context_json["rule_ids"]) == {
        "hard_stop",
        "take_profit_watch",
    }


@pytest.mark.asyncio
async def test_persistent_hard_stop_inhibits_new_watch_and_wins_repeat_selection(app) -> None:
    position_id = await _position(app)
    hard_stop = _command(position_id).evaluations[0]
    watch = EvaluatedRiskRule(
        rule_id="take_profit_watch",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=None,
        reason="profit reached the watch threshold",
        hard_stop=False,
        confirmation_required=1,
        recovery_required=1,
    )
    inactive_exit_watch = EvaluatedRiskRule(
        rule_id="exit_watch",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=False,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="ranking exit watch is inactive",
        hard_stop=False,
        confirmation_required=2,
        recovery_required=2,
    )

    async with app.state.db.session() as session:
        first = await orchestrate_position_lifecycle_evaluation(
            session,
            _command(position_id),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    watch_command = replace(
        _command(position_id),
        expected_position_state_version=1,
        sealed_snapshot_hash="b" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        evaluations=(hard_stop, watch),
    )
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session,
            watch_command,
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    repeat_command = replace(
        watch_command,
        expected_position_state_version=2,
        sealed_snapshot_hash="c" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        evaluations=(inactive_exit_watch, hard_stop, watch),
        notification_repeat=True,
    )
    async with app.state.db.session() as session:
        repeated = await orchestrate_position_lifecycle_evaluation(
            session,
            repeat_command,
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    async with app.state.db.session() as session:
        items = list(
            (
                await session.scalars(
                    select(TrackedPositionNotificationItem).order_by(
                        TrackedPositionNotificationItem.id
                    )
                )
            ).all()
        )
        actions = list((await session.scalars(select(TrackedPositionActionDecision))).all())

    watch_item = next(
        item for item in items if item.repeat_slot == "2026-07-15:close"
    )
    repeat_item = next(
        item for item in items if item.repeat_slot == "2026-07-16:close"
    )
    assert watch_item.status == "suppressed"
    assert watch_item.suppression_reason == "higher_severity_active"
    assert repeat_item.route == "urgent_hard_stop"
    assert repeat_item.status == "pending"
    assert repeat_item.action_decision_id == first.action_id == repeated.action_id
    assert len(actions) == 1


@pytest.mark.asyncio
async def test_policy_cutover_retires_old_action_without_recreating_satisfied_target(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        old = await persist_action_decision(
            session,
            PersistActionDecisionCommand(
                owner_id=1,
                position_id=position_id,
                expected_exit_state_version=0,
                position_episode_id="position-episode-1",
                exposure_version=1,
                policy_version="policy-v1",
                action_cycle_id="old-cycle",
                target_remaining_fraction=0.5,
                baseline_normalized_quantity=1000.0,
                baseline_account_weight=0.3,
                baseline_adjustment_factor=1.0,
                baseline_source="confirmed_shares",
                current_normalized_quantity=1000.0,
                input_snapshot_hash="d" * 64,
                data_state="eligible",
                contributing_rules=("trailing_take_profit",),
                alert_episode_ids=("old-alert",),
            ),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        position.confirmed_shares = 500.0
        position.exit_state_json = {
            **dict(position.exit_state_json or {}),
            "policy_version": "policy-v1",
            "alert_rule_states": {
                "trailing_take_profit": {
                    "state": "firing",
                    "alert_episode_id": "old-alert",
                    "confirmation_count": 2,
                    "recovery_count": 0,
                }
            },
        }
        await session.commit()

    cutover = replace(
        _command(position_id),
        expected_position_state_version=1,
        policy_version="policy-v2",
        sealed_snapshot_hash="e" * 64,
        evaluations=(
            EvaluatedRiskRule(
                rule_id="trailing_take_profit",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=0.5,
                reason="same absolute target under the new policy",
                confirmation_required=1,
                recovery_required=2,
            ),
        ),
    )
    async with app.state.db.session() as session:
        result = await orchestrate_position_lifecycle_evaluation(
            session, cutover, rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        actions = (await session.scalars(select(TrackedPositionActionDecision))).all()
        audit = await session.scalar(select(TrackedPositionAlertAudit))
        position = await session.get(TrackedPosition, position_id)

    assert result.action_id is None
    assert len(actions) == 1
    assert actions[0].id == old.action.id
    assert actions[0].status == "superseded"
    assert actions[0].status_reason == "policy_retired"
    assert actions[0].is_current is False
    assert audit is not None
    assert audit.decision_context_json["action_disposition"] == "target_already_satisfied"
    assert position is not None
    assert position.exit_state_json["policy_version"] == "policy-v2"
    assert position.exit_state_json["current_action_id"] is None


@pytest.mark.asyncio
async def test_shadow_evidence_initializes_pending_without_production_action_or_item(app) -> None:
    position_id = await _position(app)
    command = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="f" * 64,
        evaluations=(
            EvaluatedRiskRule(
                rule_id="trailing_take_profit",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=0.5,
                reason="first unchanged shadow observation",
                confirmation_required=2,
                recovery_required=2,
            ),
        ),
    )
    async with app.state.db.session() as session:
        result = await orchestrate_position_lifecycle_evaluation(
            session, command, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    confirmed_command = replace(
        command,
        sealed_snapshot_hash="e" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 15, 14, 59),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
    )
    async with app.state.db.session() as session:
        confirmed = await orchestrate_position_lifecycle_evaluation(
            session, confirmed_command, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        action_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        )
        item_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionNotificationItem)
        )
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )
        shadow_rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.id
                    )
                )
            ).all()
        )
        position = await session.get(TrackedPosition, position_id)

    assert result.action_id is None
    assert result.outcome == "shadow_recorded"
    assert confirmed.action_id is None
    assert confirmed.outcome == "shadow_recorded"
    assert action_count == 0
    assert item_count == 0
    assert audit_count == 0
    assert position is not None
    assert position.exit_state_version == 0
    assert position.exit_state_json["alert_rule_states"] == {}
    assert "shadow_alert_rule_states" not in position.exit_state_json
    assert len(shadow_rows) == 2
    assert shadow_rows[0].rule_states_json["trailing_take_profit"]["state"] == "pending"
    assert shadow_rows[1].rule_states_json["trailing_take_profit"]["state"] == "firing"
    assert shadow_rows[1].action_evidence_json["disposition"] == "create"
    assert shadow_rows[1].action_evidence_json["target_remaining_fraction"] == 0.5


@pytest.mark.asyncio
async def test_shadow_state_is_isolated_by_position_episode_exposure_and_policy(app) -> None:
    position_id = await _position(app)
    evaluation = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="requires a second confirmation",
        confirmation_required=2,
        recovery_required=2,
    )
    first = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        evaluations=(evaluation,),
    )
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, first, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        position.confirmed_shares = 2000.0
        position.estimated_shares = 2000.0
        position.exit_state_version = 1
        position.exit_state_json = {
            **dict(position.exit_state_json or {}),
            "position_episode_id": "position-episode-2",
            "exposure_version": 2,
            "exposure_baseline": {
                "normalized_quantity": 2000.0,
                "account_weight": 0.4,
                "source": "confirmed_shares",
                "adjustment_factor": 1.0,
            },
            "alert_rule_states": {},
        }
        await session.commit()

    second = replace(
        first,
        expected_position_state_version=1,
        sealed_snapshot_hash="b" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 15, 14, 59),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
    )
    different_policy = replace(
        second,
        policy_version="policy-shadow-v2",
        sealed_snapshot_hash="c" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
        eligible_data_time=datetime(2026, 7, 16, 14, 59),
        cutoff_time=datetime(2026, 7, 16, 15, 0),
    )
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, second, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, different_policy, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.id
                    )
                )
            ).all()
        )

    assert len(rows) == 3
    assert rows[0].rule_states_json["trailing_take_profit"]["state"] == "pending"
    assert rows[1].rule_states_json["trailing_take_profit"]["state"] == "pending"
    assert rows[2].rule_states_json["trailing_take_profit"]["state"] == "pending"
    assert [row.position_episode_id for row in rows] == [
        "position-episode-1",
        "position-episode-2",
        "position-episode-2",
    ]
    assert [row.exposure_version for row in rows] == [1, 2, 2]
    assert [row.stream_sequence for row in rows] == [1, 1, 1]
    assert all(row.predecessor_event_id is None for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rejected_occurred_at",
    [datetime(2026, 7, 14, 15, 0), datetime(2026, 7, 16, 15, 0)],
)
async def test_shadow_stream_rejects_older_or_same_time_distinct_events(
    app, rejected_occurred_at: datetime
) -> None:
    position_id = await _position(app)
    evaluation = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="ordered shadow evidence",
        confirmation_required=2,
        recovery_required=2,
    )
    first = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        evaluations=(evaluation,),
        occurred_at=datetime(2026, 7, 15, 15, 0),
    )
    second = replace(
        first,
        sealed_snapshot_hash="b" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
        eligible_data_time=datetime(2026, 7, 16, 14, 59),
        cutoff_time=datetime(2026, 7, 16, 15, 0),
    )
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, first, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, second, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    rejected = replace(
        second,
        sealed_snapshot_hash="c" * 64,
        repeat_slot="2026-07-16:close:late",
        occurred_at=rejected_occurred_at,
    )
    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="shadow lifecycle event is out of order"):
            await orchestrate_position_lifecycle_evaluation(
                session, rejected, rollout_policy=SHADOW_ROLLOUT
            )
        await session.rollback()

    async with app.state.db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.id
                    )
                )
            ).all()
        )

    assert [row.stream_sequence for row in rows] == [1, 2]
    assert rows[1].predecessor_event_id == rows[0].event_id


@pytest.mark.asyncio
async def test_shadow_stream_uses_sequence_as_authoritative_head(app) -> None:
    position_id = await _position(app)
    common = {
        "user_id": 1,
        "tracked_position_id": position_id,
        "policy_version": "policy-shadow-v1",
        "position_episode_id": "position-episode-1",
        "exposure_version": 1,
        "event_schema_version": AUDIT_EVENT_SCHEMA_VERSION,
        "production_position_state_version": 0,
        "transitions_json": [],
        "action_evidence_json": {},
        "data_state": "eligible",
    }
    second = TrackedPositionLifecycleShadowEvidence(
        **common,
        stream_sequence=2,
        predecessor_event_id="1" * 64,
        event_id="2" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        sealed_snapshot_hash="2" * 64,
        rule_states_json={
            "trailing_take_profit": {
                "state": "pending",
                "alert_episode_id": "episode-1",
                "confirmation_count": 1,
                "recovery_count": 0,
            }
        },
        occurred_at=datetime(2026, 7, 15, 15, 0),
    )
    first = TrackedPositionLifecycleShadowEvidence(
        **common,
        stream_sequence=1,
        predecessor_event_id=None,
        event_id="1" * 64,
        trade_session=date(2026, 7, 14),
        repeat_slot="2026-07-14:close",
        sealed_snapshot_hash="1" * 64,
        rule_states_json={},
        occurred_at=datetime(2026, 7, 14, 15, 0),
    )
    async with app.state.db.session() as session:
        session.add(second)
        await session.commit()
    async with app.state.db.session() as session:
        session.add(first)
        await session.commit()

    command = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="3" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
        eligible_data_time=datetime(2026, 7, 16, 14, 59),
        cutoff_time=datetime(2026, 7, 16, 15, 0),
        evaluations=(
            EvaluatedRiskRule(
                rule_id="trailing_take_profit",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=0.5,
                reason="second confirmation",
                confirmation_required=2,
                recovery_required=2,
            ),
        ),
    )
    async with app.state.db.session() as session:
        await orchestrate_position_lifecycle_evaluation(
            session, command, rollout_policy=SHADOW_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.stream_sequence
                    )
                )
            ).all()
        )

    assert [row.stream_sequence for row in rows] == [1, 2, 3]
    assert rows[2].predecessor_event_id == rows[1].event_id
    assert rows[2].rule_states_json["trailing_take_profit"]["state"] == "firing"


@pytest.mark.asyncio
async def test_shadow_stream_rejects_broken_head_predecessor(app) -> None:
    position_id = await _position(app)
    rows = [
        TrackedPositionLifecycleShadowEvidence(
            user_id=1,
            tracked_position_id=position_id,
            policy_version="policy-shadow-v1",
            position_episode_id="position-episode-1",
            exposure_version=1,
            stream_sequence=sequence,
            predecessor_event_id=None if sequence == 1 else "9" * 64,
            event_id=str(sequence) * 64,
            event_schema_version=AUDIT_EVENT_SCHEMA_VERSION,
            trade_session=date(2026, 7, 13 + sequence),
            repeat_slot=f"2026-07-{13 + sequence}:close",
            sealed_snapshot_hash=str(sequence) * 64,
            production_position_state_version=0,
            rule_states_json={},
            transitions_json=[],
            action_evidence_json={},
            data_state="eligible",
            occurred_at=datetime(2026, 7, 13 + sequence, 15, 0),
        )
        for sequence in (1, 2)
    ]
    async with app.state.db.session() as session:
        session.add_all(rows)
        await session.commit()

    command = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="3" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
    )
    async with app.state.db.session() as session:
        with pytest.raises(
            ValueError, match="shadow lifecycle stream predecessor mismatch"
        ):
            await orchestrate_position_lifecycle_evaluation(
                session, command, rollout_policy=SHADOW_ROLLOUT
            )
        await session.rollback()

    async with app.state.db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(TrackedPositionLifecycleShadowEvidence)
        )
    assert count == 2


@pytest.mark.asyncio
async def test_transaction_entry_converges_concurrent_duplicate_shadow_events(app) -> None:
    position_id = await _position(app)
    command = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        evaluations=(
            EvaluatedRiskRule(
                rule_id="hard_stop",
                data_state="eligible",
                data_reason_code="decision_eligible",
                condition_met=True,
                recovery_met=False,
                target_remaining_fraction=0.0,
                reason="same concurrent event",
                hard_stop=True,
                confirmation_required=1,
                recovery_required=2,
            ),
        ),
    )

    results = await asyncio.gather(
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            command,
            rollout_policy=SHADOW_ROLLOUT,
            max_attempts=3,
        ),
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            command,
            rollout_policy=SHADOW_ROLLOUT,
            max_attempts=3,
        ),
    )

    async with app.state.db.session() as session:
        rows = list((await session.scalars(select(TrackedPositionLifecycleShadowEvidence))).all())

    assert sorted(result.outcome for result in results) == ["duplicate", "shadow_recorded"]
    assert len(rows) == 1
    assert rows[0].stream_sequence == 1


@pytest.mark.asyncio
async def test_transaction_entry_prevents_concurrent_shadow_stream_branches(app) -> None:
    position_id = await _position(app)
    first = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="d" * 64,
        repeat_slot="2026-07-14:close:a",
    )
    second = replace(
        first,
        sealed_snapshot_hash="e" * 64,
        repeat_slot="2026-07-14:close:b",
    )

    results = await asyncio.gather(
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            first,
            rollout_policy=SHADOW_ROLLOUT,
            max_attempts=3,
        ),
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            second,
            rollout_policy=SHADOW_ROLLOUT,
            max_attempts=3,
        ),
        return_exceptions=True,
    )

    async with app.state.db.session() as session:
        rows = list((await session.scalars(select(TrackedPositionLifecycleShadowEvidence))).all())

    successes = [result for result in results if isinstance(result, LifecycleEvaluationResult)]
    failures = [result for result in results if isinstance(result, ValueError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert str(failures[0]) == "shadow lifecycle event is out of order"
    assert len(rows) == 1
    assert rows[0].stream_sequence == 1
    assert rows[0].predecessor_event_id is None


@pytest.mark.asyncio
async def test_shadow_workflow_persists_strictest_target_evidence_for_cutover_collection(
    app,
) -> None:
    first_position_id = await _position(app)
    second_position_id = await _position(app)
    weaker = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=2,
    )
    stricter = EvaluatedRiskRule(
        rule_id="hard_stop",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.0,
        reason="exit fully",
        hard_stop=True,
        confirmation_required=1,
        recovery_required=2,
    )
    commands = (
        replace(
            _command(first_position_id),
            policy_version="policy-shadow-v1",
            sealed_snapshot_hash="6" * 64,
            evaluations=(weaker, stricter),
        ),
        replace(
            _command(second_position_id),
            policy_version="policy-shadow-v1",
            sealed_snapshot_hash="7" * 64,
            evaluations=(stricter, weaker),
        ),
    )
    for command in commands:
        await lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            command,
            rollout_policy=SHADOW_ROLLOUT,
        )

    async with app.state.db.session() as session:
        observation = await rollout_module.collect_cutover_gate_observation(
            session,
            policy_version="policy-shadow-v1",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 14),
            max_evaluations=10,
        )
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.id
                    )
                )
            ).all()
        )

    gate = rollout_module.evaluate_cutover_gates(observation, max_error_rate=0.0)
    assert gate.passed is False
    assert gate.failures == (
        "insufficient_distinct_sessions",
        "unexplained_legacy_differences",
    )
    assert observation.strictest_target_mismatches == 0
    assert observation.error_count == 0
    assert len(rows) == 2
    assert all(row.action_evidence_json["target_remaining_fraction"] == 0.0 for row in rows)
    assert all(
        row.action_evidence_json["candidate_target_fractions"] == [0.0, 0.5]
        for row in rows
    )
    assert all(row.action_evidence_json["action_cycle_id"] for row in rows)
    assert all(
        row.action_evidence_json["legacy_comparison_status"] == "not_observed"
        for row in rows
    )


@pytest.mark.asyncio
async def test_shadow_resolution_closes_cycle_before_a_new_firing(app) -> None:
    position_id = await _position(app)
    firing_rule = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=1,
    )
    first = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="1" * 64,
        evaluations=(firing_rule,),
    )
    resolved = replace(
        first,
        sealed_snapshot_hash="2" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 15, 14, 59),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
        evaluations=(replace(firing_rule, condition_met=False, recovery_met=True),),
    )
    new_firing = replace(
        first,
        sealed_snapshot_hash="3" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
        eligible_data_time=datetime(2026, 7, 16, 14, 59),
        cutoff_time=datetime(2026, 7, 16, 15, 0),
    )
    for command in (first, resolved, new_firing):
        await lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            command,
            rollout_policy=SHADOW_ROLLOUT,
        )

    async with app.state.db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.stream_sequence
                    )
                )
            ).all()
        )

    first_cycle = rows[0].action_evidence_json["action_cycle_id"]
    assert rows[0].action_evidence_json["disposition"] == "create"
    assert rows[1].action_evidence_json["disposition"] == "close_resolved"
    assert rows[1].action_evidence_json["target_remaining_fraction"] is None
    assert rows[1].action_evidence_json["action_cycle_id"] is None
    assert rows[1].action_evidence_json["closed_action_cycle_id"] == first_cycle
    assert rows[2].action_evidence_json["disposition"] == "create"
    assert rows[2].action_evidence_json["action_cycle_id"] != first_cycle


@pytest.mark.asyncio
async def test_shadow_resolution_and_new_firing_close_and_open_cycles_together(
    app,
) -> None:
    position_id = await _position(app)
    old_rule = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=1,
    )
    new_rule = replace(
        old_rule,
        rule_id="hard_stop",
        target_remaining_fraction=0.0,
        reason="exit fully",
        hard_stop=True,
    )
    first = replace(
        _command(position_id),
        policy_version="policy-shadow-v1",
        sealed_snapshot_hash="4" * 64,
        evaluations=(old_rule,),
    )
    close_and_open = replace(
        first,
        sealed_snapshot_hash="5" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 15, 14, 59),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
        evaluations=(
            replace(old_rule, condition_met=False, recovery_met=True),
            new_rule,
        ),
    )
    for command in (first, close_and_open):
        await lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            command,
            rollout_policy=SHADOW_ROLLOUT,
        )

    async with app.state.db.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence).order_by(
                        TrackedPositionLifecycleShadowEvidence.stream_sequence
                    )
                )
            ).all()
        )

    old_cycle = rows[0].action_evidence_json["action_cycle_id"]
    replacement = rows[1].action_evidence_json
    assert replacement["disposition"] == "create"
    assert replacement["closed_action_cycle_id"] == old_cycle
    assert replacement["action_cycle_id"] != old_cycle
    assert replacement["target_remaining_fraction"] == 0.0
    assert replacement["contributing_rules"] == ["hard_stop"]


@pytest.mark.asyncio
async def test_active_workflow_terminalizes_resolved_cycle_before_reopening(
    app,
) -> None:
    position_id = await _position(app)
    firing_rule = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=1,
    )
    first_command = replace(
        _command(position_id),
        sealed_snapshot_hash="6" * 64,
        evaluations=(firing_rule,),
    )
    first = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        first_command,
        rollout_policy=ACTIVE_ROLLOUT,
    )
    resolved_command = replace(
        first_command,
        expected_position_state_version=first.position_state_version,
        sealed_snapshot_hash="7" * 64,
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        occurred_at=datetime(2026, 7, 15, 15, 0),
        eligible_data_time=datetime(2026, 7, 15, 14, 59),
        cutoff_time=datetime(2026, 7, 15, 15, 0),
        evaluations=(
            replace(firing_rule, condition_met=False, recovery_met=True),
            EvaluatedRiskRule(
                rule_id="unrelated_watch",
                data_state="data_waiting",
                data_reason_code="adjusted_close_not_final",
                condition_met=False,
                recovery_met=False,
                target_remaining_fraction=None,
                reason="unrelated data is pending",
                confirmation_required=1,
                recovery_required=1,
            ),
        ),
    )
    resolved = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        resolved_command,
        rollout_policy=ACTIVE_ROLLOUT,
    )

    async with app.state.db.session() as session:
        closed = await session.get(TrackedPositionActionDecision, first.action_id)
        position = await session.get(TrackedPosition, position_id)
        audit = await session.scalar(
            select(TrackedPositionAlertAudit).where(
                TrackedPositionAlertAudit.event_id == resolved.audit_event_id
            )
        )
        item = (
            await session.get(
                TrackedPositionNotificationItem,
                resolved.notification_item_id,
            )
            if resolved.notification_item_id is not None
            else None
        )
    assert resolved.action_id is None
    assert closed is not None
    assert closed.status == "expired"
    assert closed.status_reason == "all_contributing_rules_resolved"
    assert closed.is_current is False
    assert position is not None
    assert position.exit_state_json["open_action_cycle_id"] is None
    assert position.exit_state_json["last_closed_action_cycle"]["action_cycle_id"] == (
        closed.action_cycle_id
    )
    assert audit is not None
    assert audit.action_decision_id == closed.id
    assert item is not None
    assert item.status == "suppressed"
    assert item.route == "data_status"
    assert item.suppression_reason == "action_cycle_resolved"
    assert item.payload_json["notification_kind"] == "data_status"

    reopened_command = replace(
        first_command,
        expected_position_state_version=resolved.position_state_version,
        sealed_snapshot_hash="8" * 64,
        trade_session=date(2026, 7, 16),
        repeat_slot="2026-07-16:close",
        occurred_at=datetime(2026, 7, 16, 15, 0),
        eligible_data_time=datetime(2026, 7, 16, 14, 59),
        cutoff_time=datetime(2026, 7, 16, 15, 0),
    )
    reopened = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        reopened_command,
        rollout_policy=ACTIVE_ROLLOUT,
    )
    async with app.state.db.session() as session:
        current = await session.get(TrackedPositionActionDecision, reopened.action_id)

    assert current is not None
    assert current.is_current is True
    assert current.action_cycle_id != closed.action_cycle_id
    assert current.target_remaining_fraction == closed.target_remaining_fraction


@pytest.mark.asyncio
async def test_active_workflow_requires_current_eligible_evidence_for_every_rule_before_terminalizing(
    app,
) -> None:
    position_id = await _position(app)
    first_rule = EvaluatedRiskRule(
        rule_id="confirmed_trend_weakening",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=1,
    )
    second_rule = replace(
        first_rule,
        rule_id="trailing_take_profit",
        reason="protect profit",
    )
    initial_command = replace(
        _command(position_id),
        sealed_snapshot_hash="1" * 64,
        evaluations=(first_rule, second_rule),
    )
    initial = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        initial_command,
        rollout_policy=ACTIVE_ROLLOUT,
    )
    first_resolution = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        replace(
            initial_command,
            expected_position_state_version=initial.position_state_version,
            sealed_snapshot_hash="2" * 64,
            trade_session=date(2026, 7, 15),
            repeat_slot="2026-07-15:close",
            occurred_at=datetime(2026, 7, 15, 15, 0),
            eligible_data_time=datetime(2026, 7, 15, 14, 59),
            cutoff_time=datetime(2026, 7, 15, 15, 0),
            evaluations=(
                first_rule,
                replace(second_rule, condition_met=False, recovery_met=True),
            ),
        ),
        rollout_policy=ACTIVE_ROLLOUT,
    )
    partial_snapshot = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        replace(
            initial_command,
            expected_position_state_version=first_resolution.position_state_version,
            sealed_snapshot_hash="3" * 64,
            trade_session=date(2026, 7, 16),
            repeat_slot="2026-07-16:close",
            occurred_at=datetime(2026, 7, 16, 15, 0),
            eligible_data_time=datetime(2026, 7, 16, 14, 59),
            cutoff_time=datetime(2026, 7, 16, 15, 0),
            evaluations=(replace(first_rule, condition_met=False, recovery_met=True),),
        ),
        rollout_policy=ACTIVE_ROLLOUT,
    )

    async with app.state.db.session() as session:
        still_current = await session.get(
            TrackedPositionActionDecision,
            initial.action_id,
        )
    assert still_current is not None
    assert still_current.status == "proposed"
    assert still_current.is_current is True

    complete_snapshot = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        replace(
            initial_command,
            expected_position_state_version=partial_snapshot.position_state_version,
            sealed_snapshot_hash="4" * 64,
            trade_session=date(2026, 7, 17),
            repeat_slot="2026-07-17:close",
            occurred_at=datetime(2026, 7, 17, 15, 0),
            eligible_data_time=datetime(2026, 7, 17, 14, 59),
            cutoff_time=datetime(2026, 7, 17, 15, 0),
            evaluations=(
                replace(first_rule, condition_met=False, recovery_met=True),
                replace(second_rule, condition_met=False, recovery_met=True),
            ),
        ),
        rollout_policy=ACTIVE_ROLLOUT,
    )
    async with app.state.db.session() as session:
        expired = await session.get(TrackedPositionActionDecision, initial.action_id)

    assert complete_snapshot.action_id is None
    assert expired is not None
    assert expired.status == "expired"
    assert expired.is_current is False


@pytest.mark.asyncio
async def test_active_workflow_closes_old_cycle_and_opens_new_target_in_one_snapshot(
    app,
) -> None:
    position_id = await _position(app)
    old_rule = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=1,
    )
    first_command = replace(
        _command(position_id),
        sealed_snapshot_hash="5" * 64,
        evaluations=(old_rule,),
    )
    first = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        first_command,
        rollout_policy=ACTIVE_ROLLOUT,
    )
    hard_stop = replace(
        old_rule,
        rule_id="hard_stop",
        target_remaining_fraction=0.0,
        reason="exit fully",
        hard_stop=True,
    )
    replacement = await lifecycle_workflow.execute_position_lifecycle_evaluation(
        app.state.db.session,
        replace(
            first_command,
            expected_position_state_version=first.position_state_version,
            sealed_snapshot_hash="6" * 64,
            trade_session=date(2026, 7, 15),
            repeat_slot="2026-07-15:close",
            occurred_at=datetime(2026, 7, 15, 15, 0),
            eligible_data_time=datetime(2026, 7, 15, 14, 59),
            cutoff_time=datetime(2026, 7, 15, 15, 0),
            evaluations=(
                replace(old_rule, condition_met=False, recovery_met=True),
                hard_stop,
            ),
        ),
        rollout_policy=ACTIVE_ROLLOUT,
    )

    async with app.state.db.session() as session:
        old_action = await session.get(TrackedPositionActionDecision, first.action_id)
        new_action = await session.get(
            TrackedPositionActionDecision,
            replacement.action_id,
        )
        position = await session.get(TrackedPosition, position_id)
        item = await session.get(
            TrackedPositionNotificationItem,
            replacement.notification_item_id,
        )

    assert old_action is not None
    assert old_action.status == "expired"
    assert old_action.is_current is False
    assert new_action is not None
    assert new_action.status == "proposed"
    assert new_action.is_current is True
    assert new_action.target_remaining_fraction == 0.0
    assert new_action.action_cycle_id != old_action.action_cycle_id
    assert position is not None
    assert position.exit_state_json["last_closed_action_cycle"]["action_cycle_id"] == (
        old_action.action_cycle_id
    )
    assert position.exit_state_json["open_action_cycle_id"] == new_action.action_cycle_id
    assert item is not None
    assert item.action_decision_id == new_action.id
    assert item.payload_json["notification_kind"] == "action"


@pytest.mark.asyncio
async def test_active_entry_converges_to_strictest_target_under_reversed_concurrency(
    app,
) -> None:
    first_position_id = await _position(app)
    second_position_id = await _position(app)
    weaker = EvaluatedRiskRule(
        rule_id="trailing_take_profit",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.5,
        reason="reduce to half",
        confirmation_required=1,
        recovery_required=2,
    )
    stricter = EvaluatedRiskRule(
        rule_id="hard_stop",
        data_state="eligible",
        data_reason_code="decision_eligible",
        condition_met=True,
        recovery_met=False,
        target_remaining_fraction=0.0,
        reason="exit fully",
        hard_stop=True,
        confirmation_required=1,
        recovery_required=2,
    )

    def command_for(
        position_id: int,
        rule: EvaluatedRiskRule,
        *,
        snapshot_digit: str,
    ) -> LifecycleEvaluationCommand:
        return replace(
            _command(position_id),
            sealed_snapshot_hash=snapshot_digit * 64,
            repeat_slot=f"2026-07-14:close:{rule.rule_id}",
            evaluations=(rule,),
        )

    first_weaker = command_for(first_position_id, weaker, snapshot_digit="4")
    first_stricter = command_for(first_position_id, stricter, snapshot_digit="5")
    second_weaker = command_for(second_position_id, weaker, snapshot_digit="6")
    second_stricter = command_for(second_position_id, stricter, snapshot_digit="7")
    results = await asyncio.gather(
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            first_weaker,
            rollout_policy=ACTIVE_ROLLOUT,
        ),
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            first_stricter,
            rollout_policy=ACTIVE_ROLLOUT,
        ),
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            second_stricter,
            rollout_policy=ACTIVE_ROLLOUT,
        ),
        lifecycle_workflow.execute_position_lifecycle_evaluation(
            app.state.db.session,
            second_weaker,
            rollout_policy=ACTIVE_ROLLOUT,
        ),
    )

    async with app.state.db.session() as session:
        actions = list(
            (
                await session.scalars(
                    select(TrackedPositionActionDecision).order_by(
                        TrackedPositionActionDecision.tracked_position_id,
                        TrackedPositionActionDecision.id,
                    )
                )
            ).all()
        )

    assert len(results) == 4
    for position_id in (first_position_id, second_position_id):
        position_actions = [
            action for action in actions if action.tracked_position_id == position_id
        ]
        current = [action for action in position_actions if action.is_current]
        assert len(current) == 1
        assert current[0].target_remaining_fraction == 0.0
        assert all(
            not action.is_current or action.target_remaining_fraction == 0.0
            for action in position_actions
        )
