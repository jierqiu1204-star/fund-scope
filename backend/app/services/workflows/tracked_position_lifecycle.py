from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, cast
from weakref import WeakKeyDictionary

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionAlertAudit,
    TrackedPositionLifecycleShadowEvidence,
    TrackedPositionNotificationItem,
)
from app.services.risk_alerts import EvaluatedRiskRule
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    StalePositionStateError,
    TerminalizeActionCommand,
    persist_action_decision,
    terminalize_current_action,
)
from app.services.tracked_positions.lifecycle import (
    OPEN_ACTION_STATUSES,
    ActionStatus,
    ActionTerminalCause,
    AlertEvent,
    AlertRuleState,
    AlertState,
    EvaluationDataOutcome,
    EvaluationDataState,
    RuleActionCandidate,
    aggregate_action_candidates,
    stable_contract_hash,
    stable_contract_json,
    transition_alert_state,
)
from app.services.tracked_positions.lifecycle_read_repository import get_current_action
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)
from app.services.workflows.tracked_position_notifications import (
    NotificationPolicyContext,
    PersistNotificationItemCommand,
    decide_notification,
    next_trading_repeat_slot,
    persist_notification_item,
)

AUDIT_EVENT_SCHEMA_VERSION = "etf_alert_action_event_v1"
MAX_AUDIT_CONTEXT_BYTES = 8192
_SQLITE_WRITE_LOCKS: WeakKeyDictionary[object, WeakKeyDictionary[object, asyncio.Lock]] = (
    WeakKeyDictionary()
)


@dataclass(frozen=True)
class LifecycleEvaluationCommand:
    owner_id: int
    position_id: int
    expected_position_state_version: int
    policy_version: str
    sealed_snapshot_hash: str
    trade_session: date
    repeat_slot: str
    recipient: str
    evaluations: tuple[EvaluatedRiskRule, ...]
    occurred_at: datetime
    eligible_data_time: datetime
    cutoff_time: datetime
    actor_id: int | None = None
    request_id: str | None = None
    causation_id: str | None = None
    data_source: str = "unknown"
    quote_freshness: str = "unknown"
    notification_repeat: bool = False
    user_silenced: bool = False


@dataclass(frozen=True)
class LifecycleEvaluationResult:
    outcome: str
    position_state_version: int
    action_id: int | None
    audit_event_id: str | None
    notification_item_id: int | None


class LifecycleEvaluationConflictError(RuntimeError):
    pass


def _event_id(
    command: LifecycleEvaluationCommand,
    *,
    position_episode_id: str,
    exposure_version: int,
) -> str:
    return stable_contract_hash(
        {
            "schema": AUDIT_EVENT_SCHEMA_VERSION,
            "position_id": command.position_id,
            "position_episode_id": position_episode_id,
            "exposure_version": exposure_version,
            "policy_version": command.policy_version,
            "sealed_snapshot_hash": command.sealed_snapshot_hash,
            "repeat_slot": command.repeat_slot,
        }
    )


def _episode_id(command: LifecycleEvaluationCommand, rule_id: str) -> str:
    return stable_contract_hash(
        {
            "kind": "alert_episode",
            "position_id": command.position_id,
            "policy_version": command.policy_version,
            "rule_id": rule_id,
            "sealed_snapshot_hash": command.sealed_snapshot_hash,
        }
    )


def _action_cycle_id(command: LifecycleEvaluationCommand) -> str:
    return stable_contract_hash(
        {
            "kind": "action_cycle",
            "position_id": command.position_id,
            "policy_version": command.policy_version,
            "sealed_snapshot_hash": command.sealed_snapshot_hash,
        }
    )


def _load_rule_state(value: object) -> AlertRuleState:
    if not isinstance(value, dict):
        return AlertRuleState()
    try:
        state = AlertState(str(value.get("state", AlertState.NORMAL.value)))
    except ValueError:
        state = AlertState.NORMAL
    return AlertRuleState(
        state=state,
        alert_episode_id=(
            str(value["alert_episode_id"]) if value.get("alert_episode_id") else None
        ),
        confirmation_count=int(value.get("confirmation_count") or 0),
        recovery_count=int(value.get("recovery_count") or 0),
    )


def _dump_rule_state(value: AlertRuleState) -> dict[str, object]:
    return {
        "state": value.state.value,
        "alert_episode_id": value.alert_episode_id,
        "confirmation_count": value.confirmation_count,
        "recovery_count": value.recovery_count,
    }


def _data_outcome(evaluation: EvaluatedRiskRule) -> EvaluationDataOutcome:
    state = EvaluationDataState(evaluation.data_state)
    if state is EvaluationDataState.ELIGIBLE:
        return EvaluationDataOutcome.eligible()
    return EvaluationDataOutcome.invalid(state, evaluation.data_reason_code)


def _bounded_context(value: dict[str, object]) -> dict[str, object]:
    if len(stable_contract_json(value).encode("utf-8")) > MAX_AUDIT_CONTEXT_BYTES:
        raise ValueError("audit context exceeds the bounded schema size")
    return value


async def orchestrate_position_lifecycle_evaluation(
    session: AsyncSession,
    command: LifecycleEvaluationCommand,
    *,
    rollout_policy: LifecycleRolloutPolicy,
) -> LifecycleEvaluationResult:
    if not command.policy_version.strip() or not command.repeat_slot.strip():
        raise ValueError("policy_version and repeat_slot are required")
    if len(command.sealed_snapshot_hash) != 64:
        raise ValueError("sealed_snapshot_hash must be a sha256 hex digest")
    if not command.evaluations:
        raise ValueError("at least one evaluated rule is required")

    position = await session.scalar(
        select(TrackedPosition)
        .where(
            TrackedPosition.id == command.position_id,
            TrackedPosition.user_id == command.owner_id,
        )
        .with_for_update()
    )
    if position is None:
        raise LookupError("tracked position not found for owner")
    position_asset_name = position.asset_name
    position_asset_code = position.asset_code
    state = dict(position.exit_state_json or {})
    position_episode_id = str(state.get("position_episode_id") or "").strip()
    exposure_version = state.get("exposure_version")
    if not position_episode_id or not isinstance(exposure_version, int) or exposure_version < 1:
        raise ValueError("position episode and exposure version are required")

    is_shadow = rollout_policy.mode is LifecycleRolloutMode.SHADOW
    event_id = _event_id(
        command,
        position_episode_id=position_episode_id,
        exposure_version=exposure_version,
    )
    if not is_shadow and not rollout_policy.production_state_writes_enabled:
        return LifecycleEvaluationResult(
            outcome=f"{rollout_policy.mode.value}_no_write",
            position_state_version=position.exit_state_version,
            action_id=None,
            audit_event_id=None,
            notification_item_id=None,
        )
    if is_shadow and position.exit_state_version != command.expected_position_state_version:
        raise StalePositionStateError("tracked position state version is stale")

    existing_event = await session.scalar(
        select(TrackedPositionLifecycleShadowEvidence).where(
            TrackedPositionLifecycleShadowEvidence.event_id == event_id
        )
        if is_shadow
        else select(TrackedPositionAlertAudit).where(
            TrackedPositionAlertAudit.event_id == event_id
        )
    )
    if existing_event is not None:
        return LifecycleEvaluationResult(
            outcome="duplicate",
            position_state_version=position.exit_state_version,
            action_id=(
                None
                if is_shadow
                else existing_event.action_decision_id
            ),
            audit_event_id=event_id,
            notification_item_id=(
                None
                if is_shadow
                else existing_event.notification_item_id
            ),
        )

    previous_policy = state.get("policy_version")
    policy_cutover = previous_policy is not None and previous_policy != command.policy_version
    latest_shadow: TrackedPositionLifecycleShadowEvidence | None = None
    if is_shadow:
        shadow_head = list(
            (
                await session.scalars(
                    select(TrackedPositionLifecycleShadowEvidence)
                    .where(
                        TrackedPositionLifecycleShadowEvidence.user_id
                        == command.owner_id,
                        TrackedPositionLifecycleShadowEvidence.tracked_position_id
                        == command.position_id,
                        TrackedPositionLifecycleShadowEvidence.policy_version
                        == command.policy_version,
                        TrackedPositionLifecycleShadowEvidence.position_episode_id
                        == position_episode_id,
                        TrackedPositionLifecycleShadowEvidence.exposure_version
                        == exposure_version,
                    )
                    .order_by(
                        TrackedPositionLifecycleShadowEvidence.stream_sequence.desc(),
                        TrackedPositionLifecycleShadowEvidence.id.desc(),
                    )
                    .limit(2)
                )
            ).all()
        )
        latest_shadow = shadow_head[0] if shadow_head else None
        if latest_shadow is not None:
            valid_first = (
                latest_shadow.stream_sequence == 1
                and latest_shadow.predecessor_event_id is None
            )
            valid_successor = (
                latest_shadow.stream_sequence > 1
                and len(shadow_head) == 2
                and shadow_head[1].stream_sequence
                == latest_shadow.stream_sequence - 1
                and latest_shadow.predecessor_event_id == shadow_head[1].event_id
            )
            if not (valid_first or valid_successor):
                raise ValueError("shadow lifecycle stream predecessor mismatch")
        if latest_shadow is not None and command.occurred_at <= latest_shadow.occurred_at:
            raise ValueError("shadow lifecycle event is out of order")
        rule_states = dict(latest_shadow.rule_states_json or {}) if latest_shadow else {}
    elif policy_cutover:
        rule_states = {}
    else:
        rule_states = dict(state.get("alert_rule_states") or {})
    transitions = []
    candidates: list[RuleActionCandidate] = []
    for evaluation in sorted(command.evaluations, key=lambda item: item.rule_id):
        previous = _load_rule_state(rule_states.get(evaluation.rule_id))
        transition = transition_alert_state(
            previous,
            data_outcome=_data_outcome(evaluation),
            condition_met=evaluation.condition_met,
            recovery_met=evaluation.recovery_met,
            confirmation_required=evaluation.confirmation_required,
            recovery_required=evaluation.recovery_required,
            hard_stop=evaluation.hard_stop,
            new_alert_episode_id=_episode_id(command, evaluation.rule_id),
        )
        transitions.append((evaluation, transition))
        rule_states[evaluation.rule_id] = _dump_rule_state(transition.current)
        if transition.emits_action and evaluation.target_remaining_fraction is not None:
            candidates.append(
                RuleActionCandidate(
                    rule_id=evaluation.rule_id,
                    target_remaining_fraction=evaluation.target_remaining_fraction,
                    reason=evaluation.reason,
                )
            )

    transition_changed = any(
        transition.current != transition.previous for _, transition in transitions
    )
    if is_shadow:
        state_changed = False
    else:
        state_changed = previous_policy != command.policy_version or transition_changed
        state["policy_version"] = command.policy_version
        state["alert_rule_states"] = rule_states
        state["last_sealed_snapshot_hash"] = command.sealed_snapshot_hash
        state["last_repeat_slot"] = command.repeat_slot

    baseline = dict(state.get("exposure_baseline") or {})
    baseline_quantity = baseline.get("normalized_quantity")
    adjustment_factor = baseline.get("adjustment_factor", 1.0)
    raw_quantity = (
        position.confirmed_shares
        if position.confirmed_shares is not None
        else position.estimated_shares
    )
    if not isinstance(baseline_quantity, (int, float)) or not math.isfinite(baseline_quantity):
        raise ValueError("position exposure baseline is unavailable")
    if not isinstance(adjustment_factor, (int, float)) or adjustment_factor <= 0:
        raise ValueError("position adjustment factor is unavailable")
    if not isinstance(raw_quantity, (int, float)) or not math.isfinite(raw_quantity):
        raise ValueError("position quantity is unavailable")
    current_quantity = float(raw_quantity) / float(adjustment_factor)

    shadow_current_target: float | None = None
    shadow_cycle_resolved = False
    prior_cycle_id: object | None = None
    prior_contributing_rules: tuple[str, ...] = ()
    if is_shadow:
        current_action = None
        prior_action_evidence = (
            dict(latest_shadow.action_evidence_json or {}) if latest_shadow else {}
        )
        prior_cycle_id = prior_action_evidence.get("action_cycle_id")
        prior_contributing_rules = tuple(
            str(rule_id)
            for rule_id in prior_action_evidence.get("contributing_rules", ())
            if isinstance(rule_id, str) and rule_id.strip()
        )
        shadow_cycle_resolved = bool(prior_cycle_id and prior_contributing_rules) and all(
            _load_rule_state(rule_states.get(rule_id)).state is AlertState.RESOLVED
            for rule_id in prior_contributing_rules
        )
        if not shadow_cycle_resolved:
            prior_target = prior_action_evidence.get("target_remaining_fraction")
            if isinstance(prior_target, (int, float)) and not isinstance(
                prior_target, bool
            ):
                if math.isfinite(float(prior_target)) and 0 <= float(prior_target) <= 1:
                    shadow_current_target = float(prior_target)
    else:
        current_action = await get_current_action(
            session,
            owner_id=command.owner_id,
            position_id=command.position_id,
        )
    next_version = position.exit_state_version
    terminalized_action = None
    if not is_shadow and current_action is not None and current_action.policy_version != command.policy_version:
        if ActionStatus(current_action.status) in OPEN_ACTION_STATUSES:
            current_action.status = "superseded"
            current_action.status_reason = "policy_retired"
            current_action.is_current = False
            current_action.superseded_at = command.occurred_at
        state["current_action_id"] = None
        state["open_action_cycle_id"] = None
        state["last_closed_action_cycle"] = {
            "action_cycle_id": current_action.action_cycle_id,
            "reason": "policy_retired",
            "closed_at": command.occurred_at.isoformat(),
            "exposure_version": current_action.exposure_version,
            "policy_version": current_action.policy_version,
        }
        current_action = None
        state_changed = True
    if not is_shadow and current_action is not None:
        contributing_rules = tuple(current_action.contributing_rules_json or ())
        contributing_rule_set = set(contributing_rules)
        evaluations_by_rule = {
            evaluation.rule_id: evaluation for evaluation, _ in transitions
        }
        all_contributing_rules_resolved = bool(contributing_rules) and all(
            _load_rule_state(rule_states.get(rule_id)).state is AlertState.RESOLVED
            for rule_id in contributing_rules
        )
        resolution_data_eligible = contributing_rule_set.issubset(
            evaluations_by_rule
        ) and all(
            evaluations_by_rule[rule_id].data_state
            == EvaluationDataState.ELIGIBLE.value
            for rule_id in contributing_rule_set
        )
        if (
            ActionStatus(current_action.status) in OPEN_ACTION_STATUSES
            and resolution_data_eligible
            and all_contributing_rules_resolved
        ):
            terminalized = await terminalize_current_action(
                session,
                TerminalizeActionCommand(
                    owner_id=command.owner_id,
                    position_id=command.position_id,
                    action_id=current_action.id,
                    expected_exit_state_version=next_version,
                    cause=ActionTerminalCause.ALL_RULES_RESOLVED,
                    occurred_at=command.occurred_at,
                    data_eligible=True,
                    all_contributing_rules_resolved=True,
                ),
            )
            terminalized_action = terminalized.action
            next_version = terminalized.exit_state_version
            state["current_action_id"] = None
            state["open_action_cycle_id"] = None
            state["last_closed_action_cycle"] = {
                "action_cycle_id": terminalized.action.action_cycle_id,
                "reason": ActionTerminalCause.ALL_RULES_RESOLVED.value,
                "closed_at": command.occurred_at.isoformat(),
                "exposure_version": terminalized.action.exposure_version,
                "policy_version": terminalized.action.policy_version,
            }
            current_action = None
            state_changed = True
            session.expire(position)
    aggregation = aggregate_action_candidates(
        candidates,
        baseline_normalized_quantity=float(baseline_quantity),
        current_normalized_quantity=current_quantity,
        current_adjustment_factor=float(adjustment_factor),
        baseline_account_weight=(
            float(baseline["account_weight"])
            if isinstance(baseline.get("account_weight"), (int, float))
            else None
        ),
        current_action_target_fraction=(
            current_action.target_remaining_fraction
            if current_action is not None
            else shadow_current_target
        ),
    )

    if is_shadow:
        target = aggregation.target
        evidence_target = target.target_remaining_fraction if target is not None else None
        if target is None and not shadow_cycle_resolved:
            evidence_target = shadow_current_target
        if shadow_cycle_resolved:
            action_cycle_id = _action_cycle_id(command) if target is not None else None
            disposition = "create" if target is not None else "close_resolved"
        else:
            action_cycle_id = (
                str(prior_cycle_id)
                if prior_cycle_id
                else (_action_cycle_id(command) if evidence_target is not None else None)
            )
            disposition = aggregation.disposition
        shadow_evidence = TrackedPositionLifecycleShadowEvidence(
            user_id=command.owner_id,
            tracked_position_id=command.position_id,
            policy_version=command.policy_version,
            position_episode_id=position_episode_id,
            exposure_version=exposure_version,
            stream_sequence=(latest_shadow.stream_sequence + 1 if latest_shadow else 1),
            predecessor_event_id=(latest_shadow.event_id if latest_shadow else None),
            event_id=event_id,
            event_schema_version=AUDIT_EVENT_SCHEMA_VERSION,
            trade_session=command.trade_session,
            repeat_slot=command.repeat_slot,
            sealed_snapshot_hash=command.sealed_snapshot_hash,
            production_position_state_version=position.exit_state_version,
            rule_states_json=rule_states,
            transitions_json=[
                {
                    "rule_id": evaluation.rule_id,
                    "event": transition.event.value,
                    "from_state": transition.from_state.value,
                    "to_state": transition.to_state.value,
                    "data_state": evaluation.data_state,
                    "data_reason_code": evaluation.data_reason_code,
                }
                for evaluation, transition in transitions
            ],
            action_evidence_json={
                "disposition": disposition,
                "action_cycle_id": action_cycle_id,
                "closed_action_cycle_id": (
                    str(prior_cycle_id) if shadow_cycle_resolved else None
                ),
                "terminal_reason": (
                    "all_contributing_rules_resolved" if shadow_cycle_resolved else None
                ),
                "target_remaining_fraction": evidence_target,
                "target_stage": target.target_stage if target is not None else None,
                "candidate_target_fractions": sorted(
                    {candidate.target_remaining_fraction for candidate in candidates}
                ),
                "contributing_rules": list(
                    aggregation.contributing_rule_ids
                    if target is not None
                    else prior_contributing_rules
                    if shadow_cycle_resolved
                    else aggregation.contributing_rule_ids
                ),
                "reasons": list(aggregation.reasons),
                "execution_provenance": "simulated_not_observed",
                "production_action_id": None,
                "notification_item_id": None,
                "legacy_comparison_status": "not_observed",
                "legacy_difference": None,
                "legacy_difference_explained": None,
            },
            data_state=(
                "eligible"
                if all(evaluation.data_state == "eligible" for evaluation, _ in transitions)
                else next(
                    evaluation.data_state
                    for evaluation, _ in transitions
                    if evaluation.data_state != "eligible"
                )
            ),
            occurred_at=command.occurred_at,
        )
        session.add(shadow_evidence)
        await session.flush()
        return LifecycleEvaluationResult(
            outcome="shadow_recorded",
            position_state_version=position.exit_state_version,
            action_id=None,
            audit_event_id=event_id,
            notification_item_id=None,
        )

    action = current_action
    state_synced = False
    if (
        aggregation.target is not None
        and aggregation.disposition in {"create", "supersede"}
        and rollout_policy.v2_action_writes_enabled
    ):
        cycle_id = (
            current_action.action_cycle_id
            if current_action is not None
            else str(state.get("open_action_cycle_id") or _action_cycle_id(command))
        )
        persisted = await persist_action_decision(
            session,
            PersistActionDecisionCommand(
                owner_id=command.owner_id,
                position_id=command.position_id,
                expected_exit_state_version=next_version,
                position_episode_id=str(state.get("position_episode_id") or ""),
                exposure_version=int(state.get("exposure_version") or 0),
                policy_version=command.policy_version,
                action_cycle_id=cycle_id,
                target_remaining_fraction=aggregation.target.target_remaining_fraction,
                baseline_normalized_quantity=float(baseline_quantity),
                baseline_account_weight=(
                    float(baseline["account_weight"])
                    if isinstance(baseline.get("account_weight"), (int, float))
                    else None
                ),
                baseline_adjustment_factor=float(adjustment_factor),
                baseline_source=str(baseline.get("source") or "unknown"),
                current_normalized_quantity=current_quantity,
                input_snapshot_hash=command.sealed_snapshot_hash,
                data_state="eligible",
                contributing_rules=aggregation.contributing_rule_ids,
                alert_episode_ids=tuple(
                    transition.current.alert_episode_id
                    for _, transition in transitions
                    if transition.current.alert_episode_id
                ),
            ),
            rollout_policy=rollout_policy,
        )
        action = persisted.action
        next_version = persisted.exit_state_version
        state["open_action_cycle_id"] = cycle_id
        state["current_action_id"] = action.id
        state_changed = True
    elif aggregation.disposition == "reuse_current" and action is not None:
        action.contributing_rules_json = sorted(
            set(action.contributing_rules_json or ()) | set(aggregation.contributing_rule_ids)
        )
        action.alert_episode_ids_json = sorted(
            set(action.alert_episode_ids_json or ())
            | {
                transition.current.alert_episode_id
                for _, transition in transitions
                if transition.current.alert_episode_id
            }
        )
    elif state_changed:
        if terminalized_action is not None:
            cas = cast(
                CursorResult[Any],
                await session.execute(
                    update(TrackedPosition)
                    .where(
                        TrackedPosition.id == command.position_id,
                        TrackedPosition.user_id == command.owner_id,
                        TrackedPosition.exit_state_version == next_version,
                    )
                    .values(exit_state_json=state)
                    .execution_options(synchronize_session=False)
                ),
            )
            if cas.rowcount != 1:
                raise StalePositionStateError("tracked position state version is stale")
            state_synced = True
        else:
            if position.exit_state_version != command.expected_position_state_version:
                raise StalePositionStateError("tracked position state version is stale")
            cas = cast(
                CursorResult[Any],
                await session.execute(
                    update(TrackedPosition)
                    .where(
                        TrackedPosition.id == command.position_id,
                        TrackedPosition.user_id == command.owner_id,
                        TrackedPosition.exit_state_version
                        == command.expected_position_state_version,
                    )
                    .values(
                        exit_state_json=state,
                        exit_state_version=command.expected_position_state_version + 1,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            if cas.rowcount != 1:
                raise StalePositionStateError("tracked position state version is stale")
            next_version = command.expected_position_state_version + 1
            state_synced = True

    if state_changed and not state_synced:
        await session.execute(
            update(TrackedPosition)
            .where(
                TrackedPosition.id == command.position_id,
                TrackedPosition.user_id == command.owner_id,
                TrackedPosition.exit_state_version == next_version,
            )
            .values(exit_state_json=state)
            .execution_options(synchronize_session=False)
        )

    notable = [
        pair
        for pair in transitions
        if pair[1].event not in {AlertEvent.UNCHANGED, AlertEvent.DATA_FROZEN}
    ]
    active_hard_stops = [
        pair
        for pair in transitions
        if pair[0].hard_stop and pair[1].current.state is AlertState.FIRING
    ]
    primary_evaluation, primary_transition = (notable or transitions)[0]
    notification_items: list[TrackedPositionNotificationItem] = []
    notification_outcomes: list[dict[str, object]] = []
    if (notable or command.notification_repeat) and rollout_policy.notification_generation_enabled:
        active_alerts = [
            pair
            for pair in transitions
            if pair[1].current.state in {AlertState.FIRING, AlertState.RECOVERING}
            and pair[1].current.alert_episode_id
        ]
        candidates_for_notification = notable or (
            active_hard_stops[:1]
            or active_alerts[:1]
            or [(primary_evaluation, primary_transition)]
        )
        has_hard_stop = bool(active_hard_stops)
        for evaluation, transition in candidates_for_notification:
            episode_id = (
                transition.current.alert_episode_id or transition.previous.alert_episode_id
            )
            if not episode_id:
                continue
            transition_name = (
                "repeat" if command.notification_repeat and not notable else transition.event.value
            )
            existing_same_slot = (
                await session.scalar(
                    select(TrackedPositionNotificationItem.id).where(
                        TrackedPositionNotificationItem.alert_episode_id == episode_id,
                        TrackedPositionNotificationItem.recipient == command.recipient,
                        TrackedPositionNotificationItem.channel == "email",
                        TrackedPositionNotificationItem.repeat_slot == command.repeat_slot,
                    )
                )
                is not None
            )
            soft_watch = evaluation.rule_id == "take_profit_watch"
            decision = decide_notification(
                NotificationPolicyContext(
                    trade_session=command.trade_session,
                    transition=transition_name,
                    notification_repeat=command.notification_repeat,
                    hard_stop=evaluation.hard_stop,
                    data_state=evaluation.data_state,
                    existing_same_slot=existing_same_slot,
                    user_silenced=command.user_silenced,
                    higher_severity_active=has_hard_stop and not evaluation.hard_stop,
                    soft_watch=soft_watch,
                )
            )
            payload: dict[str, object]
            if decision.notification_kind == "data_status":
                payload = {
                    "contract_version": "tracked_position_action_notification_v1",
                    "notification_kind": "data_status",
                    "asset_name": position_asset_name,
                    "asset_code": position_asset_code,
                    "data_state": evaluation.data_state,
                    "reason_code": evaluation.data_reason_code,
                    "checked_at": command.cutoff_time.isoformat(),
                }
            elif soft_watch:
                payload = {
                    "contract_version": "tracked_position_action_notification_v1",
                    "notification_kind": "watch",
                    "asset_name": position_asset_name,
                    "asset_code": position_asset_code,
                    "alert_title": evaluation.reason,
                    "position_action": "hold",
                    "action_class": "soft_watch",
                }
            elif action is not None:
                payload = {
                    "contract_version": "tracked_position_action_notification_v1",
                    "notification_kind": "action",
                    "asset_name": position_asset_name,
                    "asset_code": position_asset_code,
                    "alert_title": evaluation.reason,
                    "action_id": action.id,
                    "action_status": action.status,
                    "policy_version": action.policy_version,
                    "eligible_data_time": command.eligible_data_time.isoformat(),
                    "cutoff_time": command.cutoff_time.isoformat(),
                    "data_source": command.data_source,
                    "quote_freshness": command.quote_freshness,
                    "evidence_hash": action.input_snapshot_hash,
                    "target_semantics": "absolute_exposure_baseline",
                    "target_stage": action.target_stage,
                    "target_remaining_fraction": action.target_remaining_fraction,
                    "baseline_normalized_quantity": action.baseline_normalized_quantity,
                    "baseline_adjustment_factor": action.baseline_adjustment_factor,
                    "baseline_source": action.baseline_source,
                    "target_normalized_quantity": action.target_normalized_quantity,
                    "automatic_execution": False,
                }
            else:
                unavailable_reason = (
                    "action_cycle_resolved"
                    if terminalized_action is not None
                    else "action_contract_unavailable"
                )
                payload = {
                    "contract_version": "tracked_position_action_notification_v1",
                    "notification_kind": "data_status",
                    "asset_name": position_asset_name,
                    "asset_code": position_asset_code,
                    "data_state": evaluation.data_state,
                    "reason_code": unavailable_reason,
                    "checked_at": command.cutoff_time.isoformat(),
                }
                decision = decision.__class__(
                    emit=False,
                    reason_code=unavailable_reason,
                    transition=decision.transition,
                    notification_kind="data_status",
                    route="data_status",
                    severity="warning",
                    action_oriented=False,
                )
            status = "pending" if decision.emit else "suppressed"
            item = await persist_notification_item(
                session,
                PersistNotificationItemCommand(
                    owner_id=command.owner_id,
                    position_id=command.position_id,
                    action_id=(
                        action.id if action is not None and not soft_watch else None
                    ),
                    alert_episode_id=episode_id,
                    transition=decision.transition,
                    recipient=command.recipient,
                    channel="email",
                    repeat_slot=command.repeat_slot,
                    route=decision.route,
                    severity=decision.severity,
                    payload=_bounded_context(payload),
                    status=status,
                    suppression_reason=decision.reason_code,
                    next_eligible_repeat_slot=(
                        next_trading_repeat_slot(command.trade_session)
                        if status == "suppressed"
                        else None
                    ),
                ),
            )
            notification_items.append(item)
            notification_outcomes.append(
                {
                    "item_id": item.id,
                    "status": item.status,
                    "reason_code": item.suppression_reason,
                    "notification_kind": decision.notification_kind,
                }
            )

    notification_item = notification_items[0] if notification_items else None

    audit_action = action if action is not None else terminalized_action
    action_disposition = (
        "close_resolved"
        if terminalized_action is not None and action is None
        else aggregation.disposition
    )
    audit = TrackedPositionAlertAudit(
        tracked_position_id=command.position_id,
        outcome="transitioned" if notable else "unchanged",
        signal_type=primary_evaluation.rule_id,
        alert_date=command.trade_session,
        alert_type=primary_evaluation.rule_id,
        trigger_label=primary_evaluation.rule_id,
        data_source=command.data_source,
        quote_freshness=command.quote_freshness,
        threshold_context_json=_bounded_context(
            {
                "confirmation_required": primary_evaluation.confirmation_required,
                "recovery_required": primary_evaluation.recovery_required,
                "target_remaining_fraction": primary_evaluation.target_remaining_fraction,
            }
        ),
        decision_context_json=_bounded_context(
            {
                "sealed_snapshot_hash": command.sealed_snapshot_hash,
                "repeat_slot": command.repeat_slot,
                "rule_ids": [evaluation.rule_id for evaluation, _ in transitions],
                "events": [transition.event.value for _, transition in transitions],
                "action_disposition": action_disposition,
                "data_reason_code": primary_evaluation.data_reason_code,
                "notification_outcomes": notification_outcomes,
            }
        ),
        event_id=event_id,
        event_schema_version=AUDIT_EVENT_SCHEMA_VERSION,
        alert_episode_id=primary_transition.current.alert_episode_id,
        alert_transition=primary_transition.event.value,
        action_decision_id=audit_action.id if audit_action is not None else None,
        notification_item_id=notification_item.id if notification_item is not None else None,
        policy_version=command.policy_version,
        data_state=primary_evaluation.data_state,
        from_state=primary_transition.from_state.value,
        to_state=primary_transition.to_state.value,
        actor_id=command.actor_id,
        request_id=command.request_id,
        causation_id=command.causation_id,
        occurred_at=command.occurred_at,
        execution_provenance="none",
    )
    session.add(audit)
    await session.flush()

    return LifecycleEvaluationResult(
        outcome=audit.outcome,
        position_state_version=next_version,
        action_id=action.id if action is not None else None,
        audit_event_id=event_id,
        notification_item_id=notification_item.id if notification_item is not None else None,
    )


def _retry_identity(position: TrackedPosition) -> tuple[object, ...]:
    state = dict(position.exit_state_json or {})
    return (
        state.get("position_episode_id"),
        state.get("exposure_version"),
        state.get("exposure_baseline"),
        position.confirmed_shares,
        position.estimated_shares,
        position.status,
    )


def _sqlite_write_lock(session: AsyncSession) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    engine = session.get_bind()
    locks_by_engine = _SQLITE_WRITE_LOCKS.setdefault(loop, WeakKeyDictionary())
    lock = locks_by_engine.get(engine)
    if lock is None:
        lock = asyncio.Lock()
        locks_by_engine[engine] = lock
    return lock


async def execute_position_lifecycle_evaluation(
    session_factory: Callable[[], AsyncSession],
    command: LifecycleEvaluationCommand,
    *,
    rollout_policy: LifecycleRolloutPolicy,
    max_attempts: int = 3,
) -> LifecycleEvaluationResult:
    if not 1 <= max_attempts <= 3:
        raise ValueError("max_attempts must be between 1 and 3")

    async with session_factory() as session:
        position = await session.scalar(
            select(TrackedPosition).where(
                TrackedPosition.id == command.position_id,
                TrackedPosition.user_id == command.owner_id,
            )
        )
        if position is None:
            raise LookupError("tracked position not found for owner")
        if position.exit_state_version != command.expected_position_state_version:
            raise StalePositionStateError("tracked position state version is stale")
        initial_identity = _retry_identity(position)

    current_command = command
    last_conflict: Exception | None = None
    for attempt in range(max_attempts):
        async with session_factory() as session:
            write_lock = (
                _sqlite_write_lock(session)
                if session.get_bind().dialect.name == "sqlite"
                and (
                    rollout_policy.production_state_writes_enabled
                    or rollout_policy.shadow_evidence_writes_enabled
                )
                else None
            )
            lock_acquired = False
            try:
                if write_lock is not None:
                    await write_lock.acquire()
                    lock_acquired = True
                result = await orchestrate_position_lifecycle_evaluation(
                    session,
                    current_command,
                    rollout_policy=rollout_policy,
                )
                await session.commit()
                return result
            except (IntegrityError, OperationalError, StalePositionStateError) as exc:
                await session.rollback()
                if isinstance(exc, OperationalError) and "locked" not in str(exc).lower():
                    raise
                last_conflict = exc
            finally:
                if lock_acquired and write_lock is not None:
                    write_lock.release()

        if attempt + 1 >= max_attempts:
            break
        retry_jitter = int(command.sealed_snapshot_hash[0], 16) * 0.005
        await asyncio.sleep(0.01 * (attempt + 1) + retry_jitter)
        async with session_factory() as session:
            position = await session.scalar(
                select(TrackedPosition).where(
                    TrackedPosition.id == command.position_id,
                    TrackedPosition.user_id == command.owner_id,
                )
            )
            if position is None:
                raise LookupError("tracked position not found for owner")
            if _retry_identity(position) != initial_identity:
                raise StalePositionStateError("tracked position exposure changed during retry")
            current_command = replace(
                command,
                expected_position_state_version=position.exit_state_version,
            )

    raise LifecycleEvaluationConflictError(
        f"lifecycle evaluation did not converge after {max_attempts} attempts"
    ) from last_conflict
