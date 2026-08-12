from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
    TrackedPositionActionTransitionReceipt,
    TrackedPositionAlertAudit,
    utcnow,
)
from app.services.tracked_positions.action_repository import StalePositionStateError
from app.services.tracked_positions.exposure_repository import (
    UNSET,
    ExposureMutationCommand,
    ExposureMutationIntent,
    ExposureMutationSource,
    apply_exposure_mutation,
)
from app.services.tracked_positions.lifecycle import (
    OPEN_ACTION_STATUSES,
    ActionStatus,
    stable_contract_hash,
)
from app.services.tracked_positions.sleeve_repository import (
    AppendSleeveLedgerEventCommand,
    SleeveLedgerConflictError,
    SleeveRepositoryValidationError,
    append_owner_ledger_event,
    latest_owner_ledger_event,
)

TRANSITION_CONTRACT_VERSION = "tracked_position_action_transition_v1"
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
SHARE_ABSOLUTE_TOLERANCE = 1e-6
SHARE_RELATIVE_TOLERANCE = 1e-8
OWNER_PRICE_SOURCES = frozenset(
    {"owner_reported", "broker_confirmation", "trade_statement", "owner_broker_statement"}
)


class ActionTransitionKind(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    EXECUTE = "execute"
    CANCEL = "cancel"


class ActionTransitionConflictError(RuntimeError):
    pass


class ActionTransitionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ActionExecutionFacts:
    executed_at: datetime
    quantity: float
    price: float
    price_source: str
    fees: float
    resulting_shares: float
    close_fact: bool


@dataclass(frozen=True)
class ActionTransitionCommand:
    owner_id: int
    position_id: int
    action_id: int
    transition: ActionTransitionKind
    idempotency_key: str
    expected_position_state_version: int
    occurred_at: datetime
    actor_id: int
    execution: ActionExecutionFacts | None = None


@dataclass(frozen=True)
class ActionTransitionReceipt:
    action_id: int
    transition: str
    action_status: str
    target_remaining_fraction: float
    target_normalized_quantity: float
    cumulative_executed_quantity: float
    execution_provenance: str
    position_state_version: int
    resulting_shares: float | None
    close_fact: bool | None
    executed_at: str | None
    idempotent_replay: bool = False

    def as_response(self) -> dict[str, Any]:
        return asdict(self)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _share_tolerance(quantity: float) -> float:
    return max(SHARE_ABSOLUTE_TOLERANCE, abs(quantity) * SHARE_RELATIVE_TOLERANCE)


def _request_payload(command: ActionTransitionCommand) -> dict[str, Any]:
    execution = command.execution
    return {
        "contract_version": TRANSITION_CONTRACT_VERSION,
        "owner_id": command.owner_id,
        "position_id": command.position_id,
        "action_id": command.action_id,
        "transition": command.transition.value,
        "expected_position_state_version": command.expected_position_state_version,
        "execution": (
            None
            if execution is None
            else {
                "executed_at": _utc_naive(execution.executed_at).isoformat(timespec="microseconds"),
                "quantity": execution.quantity,
                "price": execution.price,
                "price_source": execution.price_source.strip(),
                "fees": execution.fees,
                "resulting_shares": execution.resulting_shares,
                "close_fact": execution.close_fact,
            }
        ),
    }


def _request_hash(command: ActionTransitionCommand) -> str:
    return stable_contract_hash(_request_payload(command))


def _receipt_from_json(payload: dict[str, Any], *, replay: bool) -> ActionTransitionReceipt:
    return ActionTransitionReceipt(
        action_id=int(payload["action_id"]),
        transition=str(payload["transition"]),
        action_status=str(payload["action_status"]),
        target_remaining_fraction=float(payload["target_remaining_fraction"]),
        target_normalized_quantity=float(payload["target_normalized_quantity"]),
        cumulative_executed_quantity=float(payload["cumulative_executed_quantity"]),
        execution_provenance=str(payload["execution_provenance"]),
        position_state_version=int(payload["position_state_version"]),
        resulting_shares=(
            None if payload.get("resulting_shares") is None else float(payload["resulting_shares"])
        ),
        close_fact=(None if payload.get("close_fact") is None else bool(payload["close_fact"])),
        executed_at=(None if payload.get("executed_at") is None else str(payload["executed_at"])),
        idempotent_replay=replay,
    )


async def _existing_receipt(
    session: AsyncSession,
    *,
    owner_id: int,
    idempotency_key: str,
    request_hash: str,
) -> ActionTransitionReceipt | None:
    row = await session.scalar(
        select(TrackedPositionActionTransitionReceipt).where(
            TrackedPositionActionTransitionReceipt.user_id == owner_id,
            TrackedPositionActionTransitionReceipt.idempotency_key == idempotency_key,
        )
    )
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise ActionTransitionConflictError(
            "Idempotency-Key was already used with a different payload"
        )
    return _receipt_from_json(dict(row.response_json), replay=True)


def _validate_command(command: ActionTransitionCommand) -> None:
    if not command.idempotency_key.strip() or len(command.idempotency_key) > 128:
        raise ActionTransitionValidationError("Idempotency-Key must contain 1 to 128 characters")
    if command.expected_position_state_version < 0:
        raise ActionTransitionValidationError("expected position state version must be non-negative")
    if command.transition is ActionTransitionKind.EXECUTE and command.execution is None:
        raise ActionTransitionValidationError("execution facts are required")
    if command.transition is not ActionTransitionKind.EXECUTE and command.execution is not None:
        raise ActionTransitionValidationError("execution facts are only valid for execute")
    if command.execution is not None:
        _validate_execution_numbers(command.execution)


def _validate_execution_numbers(facts: ActionExecutionFacts) -> None:
    for name, value in (
        ("quantity", facts.quantity),
        ("price", facts.price),
        ("fees", facts.fees),
        ("resulting_shares", facts.resulting_shares),
    ):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ActionTransitionValidationError(f"{name} must be finite")
    if facts.quantity <= 0:
        raise ActionTransitionValidationError("quantity must be positive")
    if facts.price <= 0:
        raise ActionTransitionValidationError("price must be positive")
    if facts.fees < 0 or facts.resulting_shares < 0:
        raise ActionTransitionValidationError("fees and resulting_shares must be non-negative")
    if facts.fees >= facts.quantity * facts.price:
        raise ActionTransitionValidationError("fees must be below gross sell proceeds")
    if not isinstance(facts.price_source, str) or facts.price_source.strip() not in OWNER_PRICE_SOURCES:
        raise ActionTransitionValidationError("price_source is not an owner execution source")


def _validate_current_action(
    position: TrackedPosition,
    action: TrackedPositionActionDecision,
    *,
    now: datetime,
) -> dict[str, Any]:
    state = dict(position.exit_state_json or {})
    if position.exit_state_version < 0:
        raise ActionTransitionConflictError("tracked position has invalid state version")
    if (
        state.get("position_episode_id") != action.position_episode_id
        or state.get("exposure_version") != action.exposure_version
        or state.get("policy_version") != action.policy_version
    ):
        raise ActionTransitionConflictError(
            "action is not in the current position exposure and policy"
        )
    if not action.is_current or state.get("current_action_id") != action.id:
        raise ActionTransitionConflictError("action is not the current position action")
    if ActionStatus(action.status) not in OPEN_ACTION_STATUSES:
        raise ActionTransitionConflictError("action is terminal and cannot accept a new transition")
    if action.valid_until is not None and now >= _utc_naive(action.valid_until):
        raise ActionTransitionConflictError("action has expired")
    return state


def _validate_execution(
    position: TrackedPosition,
    action: TrackedPositionActionDecision,
    state: dict[str, Any],
    facts: ActionExecutionFacts,
    *,
    server_now: datetime,
) -> tuple[datetime, float, float, float, float]:
    executed_at = _utc_naive(facts.executed_at)
    decision_time = _utc_naive(action.created_at)
    if executed_at < decision_time:
        raise ActionTransitionValidationError("execution time is before the action decision")
    if executed_at > server_now + MAX_FUTURE_CLOCK_SKEW:
        raise ActionTransitionValidationError("execution time is beyond allowed future clock skew")

    before_shares = (
        position.confirmed_shares
        if position.confirmed_shares is not None
        else position.estimated_shares
    )
    if before_shares is None or not math.isfinite(before_shares) or before_shares <= 0:
        raise ActionTransitionConflictError("current position shares are unavailable")
    tolerance = _share_tolerance(before_shares)
    if facts.quantity > before_shares + tolerance:
        raise ActionTransitionValidationError("execution quantity exceeds current shares")
    expected_result = max(0.0, before_shares - facts.quantity)
    if not math.isclose(
        facts.resulting_shares,
        expected_result,
        rel_tol=0.0,
        abs_tol=tolerance,
    ):
        raise ActionTransitionValidationError("resulting shares are inconsistent with before minus fill")
    is_closed = facts.resulting_shares <= _share_tolerance(before_shares)
    if facts.close_fact != is_closed:
        raise ActionTransitionValidationError("close_fact is inconsistent with resulting shares")

    factor = state.get("current_adjustment_factor", action.baseline_adjustment_factor)
    if (
        isinstance(factor, bool)
        or not isinstance(factor, int | float)
        or not math.isfinite(factor)
        or factor <= 0
    ):
        raise ActionTransitionConflictError("current adjustment factor is unavailable")
    normalized_before = before_shares / factor
    normalized_execution = facts.quantity / factor
    normalized_result = facts.resulting_shares / factor
    return executed_at, normalized_before, normalized_execution, normalized_result, before_shares


async def _cas_state(
    session: AsyncSession,
    position: TrackedPosition,
    *,
    expected_version: int,
    state: dict[str, Any],
) -> int:
    result = await session.execute(
        update(TrackedPosition)
        .where(
            TrackedPosition.id == position.id,
            TrackedPosition.user_id == position.user_id,
            TrackedPosition.exit_state_version == expected_version,
        )
        .values(
            exit_state_json=state,
            exit_state_version=expected_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise StalePositionStateError("tracked position state version is stale")
    return expected_version + 1


async def _persist_audit_and_receipt(
    session: AsyncSession,
    command: ActionTransitionCommand,
    action: TrackedPositionActionDecision,
    *,
    request_hash: str,
    previous_status: str,
    receipt: ActionTransitionReceipt,
) -> None:
    event_id = stable_contract_hash(
        {
            "schema": TRANSITION_CONTRACT_VERSION,
            "owner_id": command.owner_id,
            "idempotency_key": command.idempotency_key,
        }
    )
    execution = command.execution
    session.add(
        TrackedPositionAlertAudit(
            tracked_position_id=command.position_id,
            outcome="action_transition",
            alert_date=command.occurred_at.date(),
            alert_type="position_action_transition",
            trigger_label=command.transition.value,
            data_source="owner_api",
            quote_freshness="not_applicable",
            threshold_context_json={
                "target_remaining_fraction": action.target_remaining_fraction,
                "target_normalized_quantity": action.target_normalized_quantity,
            },
            decision_context_json={
                "request_hash": request_hash,
                "quantity": None if execution is None else execution.quantity,
                "resulting_shares": None if execution is None else execution.resulting_shares,
                "close_fact": None if execution is None else execution.close_fact,
            },
            event_id=event_id,
            event_schema_version=TRANSITION_CONTRACT_VERSION,
            action_decision_id=action.id,
            policy_version=action.policy_version,
            data_state=action.data_state,
            from_state=previous_status,
            to_state=receipt.action_status,
            actor_id=command.actor_id,
            request_id=command.idempotency_key,
            causation_id=f"action:{action.id}",
            occurred_at=command.occurred_at,
            execution_provenance=receipt.execution_provenance,
        )
    )
    response_json = receipt.as_response()
    response_json["idempotent_replay"] = False
    session.add(
        TrackedPositionActionTransitionReceipt(
            action_decision_id=action.id,
            user_id=command.owner_id,
            tracked_position_id=command.position_id,
            idempotency_key=command.idempotency_key,
            transition=command.transition.value,
            request_hash=request_hash,
            response_json=response_json,
        )
    )
    await session.flush()


async def _append_sleeve_sell_if_initialized(
    session: AsyncSession,
    *,
    command: ActionTransitionCommand,
    position: TrackedPosition,
    facts: ActionExecutionFacts,
    adjustment_factor: float,
) -> None:
    head = await latest_owner_ledger_event(session, owner_id=command.owner_id)
    if head is None:
        return
    identity_hash = stable_contract_hash(
        {
            "contract": "tracked_etf_sleeve_action_execution_v1",
            "owner_id": command.owner_id,
            "action_id": command.action_id,
            "idempotency_key": command.idempotency_key,
        }
    )
    try:
        await append_owner_ledger_event(
            session,
            AppendSleeveLedgerEventCommand(
                owner_id=command.owner_id,
                idempotency_key=f"sleeve-exec:{identity_hash}",
                event_type="sell",
                effective_date=_utc_naive(facts.executed_at).date(),
                occurred_at=_utc_naive(facts.executed_at),
                provenance="owner_confirmed",
                expected_predecessor_event_hash=head.event_hash,
                tracked_position_id=position.id,
                asset_code=position.asset_code,
                cash_delta=facts.quantity * facts.price - facts.fees,
                quantity_delta=-facts.quantity,
                quantity_after=facts.resulting_shares,
                execution_price=facts.price,
                fees=facts.fees,
                adjustment_factor=adjustment_factor,
                reason_code="owner_confirmed_action_execution",
            ),
        )
    except SleeveLedgerConflictError as exc:
        raise ActionTransitionConflictError(str(exc)) from exc
    except SleeveRepositoryValidationError as exc:
        raise ActionTransitionValidationError(str(exc)) from exc


async def _perform_transition(
    session: AsyncSession,
    command: ActionTransitionCommand,
    *,
    request_hash: str,
) -> ActionTransitionReceipt:
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
    if position.exit_state_version != command.expected_position_state_version:
        raise StalePositionStateError("tracked position state version is stale")
    server_now = _utc_naive(command.occurred_at)
    state = _validate_current_action(position, action, now=server_now)
    previous_status = action.status
    resulting_shares: float | None = None
    close_fact: bool | None = None
    executed_at_text: str | None = None

    if command.transition is ActionTransitionKind.ACKNOWLEDGE:
        if action.status != ActionStatus.PROPOSED.value:
            raise ActionTransitionConflictError("only a proposed action can be acknowledged")
        next_version = await _cas_state(
            session,
            position,
            expected_version=command.expected_position_state_version,
            state=state,
        )
        action.status = ActionStatus.ACKNOWLEDGED.value
        action.acknowledged_at = server_now
    elif command.transition is ActionTransitionKind.CANCEL:
        state["current_action_id"] = None
        state["declined_action_cycle"] = {
            "action_id": action.id,
            "action_cycle_id": action.action_cycle_id,
            "target_remaining_fraction": action.target_remaining_fraction,
            "exposure_version": action.exposure_version,
            "policy_version": action.policy_version,
            "cancelled_at": server_now.isoformat(),
        }
        next_version = await _cas_state(
            session,
            position,
            expected_version=command.expected_position_state_version,
            state=state,
        )
        action.status = ActionStatus.CANCELLED.value
        action.status_reason = "owner_cancelled"
        action.cancelled_at = server_now
        action.is_current = False
    else:
        assert command.execution is not None
        executed_at, before_normalized, executed_normalized, result_normalized, _ = (
            _validate_execution(
                position,
                action,
                state,
                command.execution,
                server_now=server_now,
            )
        )
        facts = command.execution
        resulting_shares = facts.resulting_shares
        close_fact = facts.close_fact
        mutation_intent = (
            ExposureMutationIntent.CLOSE if close_fact else ExposureMutationIntent.NET_REDUCE
        )
        new_confirmed: float | None | object = UNSET
        new_estimated: float | None | object = UNSET
        if position.confirmed_shares is not None or close_fact:
            new_confirmed = facts.resulting_shares
        if position.estimated_shares is not None or close_fact:
            new_estimated = facts.resulting_shares
        mutation = await apply_exposure_mutation(
            session,
            ExposureMutationCommand(
                owner_id=command.owner_id,
                position_id=command.position_id,
                expected_exit_state_version=command.expected_position_state_version,
                intent=mutation_intent,
                source=ExposureMutationSource.ACTION_EXECUTION,
                occurred_at=executed_at,
                request_id=f"action-execution:{command.idempotency_key}",
                actor_id=command.actor_id,
                reason_code="owner_confirmed_execution",
                new_confirmed_shares=new_confirmed,
                new_estimated_shares=new_estimated,
                new_status="closed" if close_fact else UNSET,
                adjustment_factor=float(
                    state.get("current_adjustment_factor", action.baseline_adjustment_factor)
                ),
                linked_action_id=action.id,
                protected_action_id=action.id,
                execution_provenance="owner_confirmed",
                audit_context={
                    "execution_quantity": executed_normalized,
                    "before_normalized_quantity": before_normalized,
                    "resulting_normalized_quantity": result_normalized,
                },
            ),
        )
        next_version = mutation.exit_state_version
        action.cumulative_executed_quantity = (
            action.cumulative_executed_quantity + executed_normalized
        )
        reached_target = result_normalized <= (
            action.target_normalized_quantity + _share_tolerance(action.baseline_normalized_quantity)
        )
        if action.target_remaining_fraction == 0.0 and reached_target and not close_fact:
            raise ActionTransitionValidationError("0% target execution requires close_fact")
        action.execution_provenance = "owner_confirmed"
        if reached_target:
            action.status = ActionStatus.EXECUTED.value
            action.status_reason = "owner_confirmed_target_reached"
            action.executed_at = executed_at
            action.is_current = False
            if not close_fact:
                refreshed_state = dict(mutation.position.exit_state_json or {})
                refreshed_state["current_action_id"] = None
                mutation.position.exit_state_json = refreshed_state
        else:
            action.status = ActionStatus.PARTIALLY_EXECUTED.value
            action.status_reason = "owner_confirmed_partial_fill"
        session.add(
            TrackedPositionActionExecution(
                action_decision_id=action.id,
                user_id=command.owner_id,
                tracked_position_id=command.position_id,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                execution_provenance="owner_confirmed",
                execution_quantity=executed_normalized,
                execution_price=facts.price,
                price_source=facts.price_source.strip(),
                fees=facts.fees,
                before_normalized_quantity=before_normalized,
                resulting_normalized_quantity=result_normalized,
                resulting_position_state_version=next_version,
                executed_at=executed_at,
                actor_id=command.actor_id,
                request_id=command.idempotency_key,
            )
        )
        await _append_sleeve_sell_if_initialized(
            session,
            command=command,
            position=position,
            facts=facts,
            adjustment_factor=float(
                state.get("current_adjustment_factor", action.baseline_adjustment_factor)
            ),
        )
        executed_at_text = executed_at.isoformat()

    receipt = ActionTransitionReceipt(
        action_id=action.id,
        transition=command.transition.value,
        action_status=action.status,
        target_remaining_fraction=action.target_remaining_fraction,
        target_normalized_quantity=action.target_normalized_quantity,
        cumulative_executed_quantity=action.cumulative_executed_quantity,
        execution_provenance=action.execution_provenance,
        position_state_version=next_version,
        resulting_shares=resulting_shares,
        close_fact=close_fact,
        executed_at=executed_at_text,
    )
    await _persist_audit_and_receipt(
        session,
        command,
        action,
        request_hash=request_hash,
        previous_status=previous_status,
        receipt=receipt,
    )
    return receipt


async def transition_position_action(
    session: AsyncSession,
    command: ActionTransitionCommand,
) -> ActionTransitionReceipt:
    _validate_command(command)
    normalized_command = replace(command, idempotency_key=command.idempotency_key.strip())
    request_hash = _request_hash(normalized_command)
    existing = await _existing_receipt(
        session,
        owner_id=normalized_command.owner_id,
        idempotency_key=normalized_command.idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None:
        return existing
    try:
        async with session.begin_nested():
            return await _perform_transition(
                session,
                normalized_command,
                request_hash=request_hash,
            )
    except IntegrityError:
        session.expire_all()
        existing = await _existing_receipt(
            session,
            owner_id=normalized_command.owner_id,
            idempotency_key=normalized_command.idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            return existing
        raise
