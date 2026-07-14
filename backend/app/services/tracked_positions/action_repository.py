from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    utcnow,
)
from app.services.tracked_positions.lifecycle import (
    TERMINAL_ACTION_STATUSES,
    ActionStatus,
    ActionTerminalCause,
    calculate_absolute_position_target,
    plan_action_terminal_transition,
)
from app.services.tracked_positions.lifecycle_rollout import LifecycleRolloutPolicy


class StalePositionStateError(RuntimeError):
    pass


class LifecycleWritesDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistActionDecisionCommand:
    owner_id: int
    position_id: int
    expected_exit_state_version: int
    position_episode_id: str
    exposure_version: int
    policy_version: str
    action_cycle_id: str
    target_remaining_fraction: float
    baseline_normalized_quantity: float
    baseline_account_weight: float | None
    baseline_adjustment_factor: float
    baseline_source: str
    current_normalized_quantity: float
    input_snapshot_hash: str
    data_state: str
    contributing_rules: tuple[str, ...]
    alert_episode_ids: tuple[str, ...]
    valid_until: datetime | None = None


@dataclass(frozen=True)
class PersistedActionDecision:
    action: TrackedPositionActionDecision
    outcome: str
    exit_state_version: int


@dataclass(frozen=True)
class TerminalizeActionCommand:
    owner_id: int
    position_id: int
    action_id: int
    expected_exit_state_version: int
    cause: ActionTerminalCause
    occurred_at: datetime
    data_eligible: bool = False
    all_contributing_rules_resolved: bool = False
    superseded_by_action_id: int | None = None


@dataclass(frozen=True)
class TerminalizedAction:
    action: TrackedPositionActionDecision
    outcome: str
    exit_state_version: int


def _identity_predicates(command: PersistActionDecisionCommand, target_stage: str):
    return (
        TrackedPositionActionDecision.user_id == command.owner_id,
        TrackedPositionActionDecision.tracked_position_id == command.position_id,
        TrackedPositionActionDecision.position_episode_id == command.position_episode_id,
        TrackedPositionActionDecision.exposure_version == command.exposure_version,
        TrackedPositionActionDecision.policy_version == command.policy_version,
        TrackedPositionActionDecision.action_cycle_id == command.action_cycle_id,
        TrackedPositionActionDecision.target_stage == target_stage,
    )


async def _existing_action(
    session: AsyncSession,
    command: PersistActionDecisionCommand,
    target_stage: str,
) -> TrackedPositionActionDecision | None:
    return await session.scalar(
        select(TrackedPositionActionDecision).where(*_identity_predicates(command, target_stage))
    )


async def persist_action_decision(
    session: AsyncSession,
    command: PersistActionDecisionCommand,
    *,
    rollout_policy: LifecycleRolloutPolicy,
) -> PersistedActionDecision:
    if not rollout_policy.v2_action_writes_enabled:
        raise LifecycleWritesDisabledError("v2 action writes are disabled by rollout policy")
    target = calculate_absolute_position_target(
        baseline_normalized_quantity=command.baseline_normalized_quantity,
        current_normalized_quantity=command.current_normalized_quantity,
        current_adjustment_factor=command.baseline_adjustment_factor,
        target_remaining_fraction=command.target_remaining_fraction,
        baseline_account_weight=command.baseline_account_weight,
    )
    existing = await _existing_action(session, command, target.target_stage)
    if existing is not None:
        return PersistedActionDecision(existing, "existing", command.expected_exit_state_version)

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

    current = await session.scalar(
        select(TrackedPositionActionDecision)
        .where(
            TrackedPositionActionDecision.tracked_position_id == command.position_id,
            TrackedPositionActionDecision.is_current,
        )
        .with_for_update()
    )
    if current is not None and current.target_remaining_fraction <= command.target_remaining_fraction:
        return PersistedActionDecision(current, "existing", position.exit_state_version)

    now = utcnow()
    outcome = "superseded" if current is not None else "created"
    try:
        async with session.begin_nested():
            cas_result = await session.execute(
                update(TrackedPosition)
                .where(
                    TrackedPosition.id == command.position_id,
                    TrackedPosition.user_id == command.owner_id,
                    TrackedPosition.exit_state_version == command.expected_exit_state_version,
                )
                .values(exit_state_version=command.expected_exit_state_version + 1)
                .execution_options(synchronize_session=False)
            )
            if cas_result.rowcount != 1:
                raise StalePositionStateError("tracked position state version is stale")

            if current is not None:
                current.is_current = False
                current.status = "superseded"
                current.status_reason = "stricter_target"
                current.superseded_at = now
                await session.flush()

            action = TrackedPositionActionDecision(
                user_id=command.owner_id,
                tracked_position_id=command.position_id,
                position_episode_id=command.position_episode_id,
                exposure_version=command.exposure_version,
                policy_version=command.policy_version,
                action_cycle_id=command.action_cycle_id,
                target_stage=target.target_stage,
                target_remaining_fraction=command.target_remaining_fraction,
                baseline_normalized_quantity=command.baseline_normalized_quantity,
                baseline_account_weight=command.baseline_account_weight,
                baseline_adjustment_factor=command.baseline_adjustment_factor,
                baseline_source=command.baseline_source,
                target_normalized_quantity=target.calculated_target_normalized_quantity,
                target_account_weight=target.target_account_weight,
                input_snapshot_hash=command.input_snapshot_hash,
                data_state=command.data_state,
                status="proposed",
                execution_provenance="none",
                cumulative_executed_quantity=0.0,
                contributing_rules_json=sorted(set(command.contributing_rules)),
                alert_episode_ids_json=sorted(set(command.alert_episode_ids)),
                valid_until=command.valid_until,
                is_current=True,
            )
            session.add(action)
            await session.flush()
            if current is not None:
                current.superseded_by_action_id = action.id

            next_state = dict(position.exit_state_json or {})
            next_state.update(
                {
                    "position_episode_id": command.position_episode_id,
                    "exposure_version": command.exposure_version,
                    "policy_version": command.policy_version,
                    "open_action_cycle_id": command.action_cycle_id,
                    "current_action_id": action.id,
                }
            )
            await session.execute(
                update(TrackedPosition)
                .where(
                    TrackedPosition.id == command.position_id,
                    TrackedPosition.exit_state_version == command.expected_exit_state_version + 1,
                )
                .values(exit_state_json=next_state)
                .execution_options(synchronize_session=False)
            )
        session.expire(position)
        return PersistedActionDecision(
            action,
            outcome,
            command.expected_exit_state_version + 1,
        )
    except IntegrityError:
        existing = await _existing_action(session, command, target.target_stage)
        if existing is not None:
            return PersistedActionDecision(existing, "existing", position.exit_state_version)
        raise


async def terminalize_current_action(
    session: AsyncSession,
    command: TerminalizeActionCommand,
) -> TerminalizedAction:
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
    action = await session.scalar(
        select(TrackedPositionActionDecision)
        .where(
            TrackedPositionActionDecision.id == command.action_id,
            TrackedPositionActionDecision.tracked_position_id == command.position_id,
            TrackedPositionActionDecision.user_id == command.owner_id,
        )
        .with_for_update()
    )
    if action is None:
        raise LookupError("action decision not found for owner and position")
    current_status = ActionStatus(action.status)
    if current_status in TERMINAL_ACTION_STATUSES:
        return TerminalizedAction(action, "existing_terminal", position.exit_state_version)
    if not action.is_current:
        raise ValueError("open action is not the current position action")

    plan = plan_action_terminal_transition(
        current_status,
        command.cause,
        data_eligible=command.data_eligible,
        all_contributing_rules_resolved=command.all_contributing_rules_resolved,
    )
    state = dict(position.exit_state_json or {})
    state["current_action_id"] = None
    if plan.close_action_cycle:
        closed_cycle_id = state.get("open_action_cycle_id") or action.action_cycle_id
        state["open_action_cycle_id"] = None
        state["last_closed_action_cycle"] = {
            "action_cycle_id": closed_cycle_id,
            "reason": plan.reason_code,
            "closed_at": command.occurred_at.isoformat(),
            "exposure_version": action.exposure_version,
            "policy_version": action.policy_version,
        }

    cas = await session.execute(
        update(TrackedPosition)
        .where(
            TrackedPosition.id == command.position_id,
            TrackedPosition.user_id == command.owner_id,
            TrackedPosition.exit_state_version == command.expected_exit_state_version,
        )
        .values(
            exit_state_json=state,
            exit_state_version=command.expected_exit_state_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if cas.rowcount != 1:
        raise StalePositionStateError("tracked position state version is stale")

    action.status = plan.next_status.value
    action.status_reason = plan.reason_code
    action.is_current = False
    action.superseded_by_action_id = command.superseded_by_action_id
    setattr(action, plan.timestamp_field, command.occurred_at)
    await session.flush()
    return TerminalizedAction(
        action,
        "terminalized",
        command.expected_exit_state_version + 1,
    )


async def expire_current_action_if_due(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    action_id: int,
    expected_exit_state_version: int,
    now: datetime,
) -> TerminalizedAction:
    action = await session.scalar(
        select(TrackedPositionActionDecision).where(
            TrackedPositionActionDecision.id == action_id,
            TrackedPositionActionDecision.tracked_position_id == position_id,
            TrackedPositionActionDecision.user_id == owner_id,
        )
    )
    if action is None:
        raise LookupError("action decision not found for owner and position")
    position = await session.scalar(
        select(TrackedPosition).where(
            TrackedPosition.id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    if position is None:
        raise LookupError("tracked position not found for owner")
    if ActionStatus(action.status) in TERMINAL_ACTION_STATUSES:
        return TerminalizedAction(action, "existing_terminal", position.exit_state_version)
    if action.valid_until is None or now < action.valid_until:
        return TerminalizedAction(action, "not_due", position.exit_state_version)
    return await terminalize_current_action(
        session,
        TerminalizeActionCommand(
            owner_id=owner_id,
            position_id=position_id,
            action_id=action_id,
            expected_exit_state_version=expected_exit_state_version,
            cause=ActionTerminalCause.VALID_UNTIL_ELAPSED,
            occurred_at=now,
        ),
    )
