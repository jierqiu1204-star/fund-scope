from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    utcnow,
)
from app.services.tracked_positions.action_repository import StalePositionStateError
from app.services.tracked_positions.lifecycle import (
    ExposureBaseline,
    ExposureMutationKind,
    PositionExposureState,
    initialize_position_exposure,
    mutate_position_exposure,
    stable_contract_hash,
)


class ExposureMutationIntent(StrEnum):
    CORRECTION = "correction"
    NET_ADD = "net_add"
    NET_REDUCE = "net_reduce"
    CLOSE = "close"
    REOPEN = "reopen"
    SYSTEM_ESTIMATE = "system_estimate"
    CORPORATE_ACTION = "corporate_action"
    TRACKING_STATUS = "tracking_status"


class ExposureMutationSource(StrEnum):
    CREATE = "create"
    PATCH = "patch"
    SHARE_CONFIRMATION = "share_confirmation"
    IMPORT = "import"
    RECALCULATION = "recalculation"
    SCRIPT = "script"
    ACTION_EXECUTION = "action_execution"


class _Unset:
    pass


UNSET = _Unset()


@dataclass(frozen=True)
class ExposureMutationCommand:
    owner_id: int
    position_id: int
    expected_exit_state_version: int
    intent: ExposureMutationIntent
    source: ExposureMutationSource
    occurred_at: datetime
    request_id: str
    actor_id: int | None = None
    reason_code: str | None = None
    new_confirmed_shares: float | None | _Unset = UNSET
    new_estimated_shares: float | None | _Unset = UNSET
    new_buy_amount: float | _Unset = UNSET
    new_status: str | _Unset = UNSET
    adjustment_factor: float = 1.0
    new_confirmed_nav_date: date | None | _Unset = UNSET
    new_entry_price: float | None | _Unset = UNSET
    new_entry_price_date: date | None | _Unset = UNSET
    linked_action_id: int | None = None
    protected_action_id: int | None = None
    execution_provenance: str = "none"
    audit_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExposureMutationReceipt:
    position: TrackedPosition
    event: str
    exit_state_version: int
    audit_event_id: str | None
    superseded_action_id: int | None = None


@dataclass(frozen=True)
class InitializeTrackedPositionCommand:
    owner_id: int
    asset_type: str
    asset_code: str
    asset_name: str
    buy_date: date
    order_time_bucket: str
    confirmed_nav_date: date | None
    confirmed_nav: float | None
    confirmed_shares: float | None
    buy_amount: float
    entry_price: float | None
    entry_price_date: date | None
    estimated_shares: float | None
    note: str | None
    occurred_at: datetime
    request_id: str


def _validate_optional_number(name: str, value: object, *, allow_zero: bool = True) -> None:
    if isinstance(value, _Unset) or value is None:
        return
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    minimum_ok = value >= 0 if allow_zero else value > 0
    if not minimum_ok:
        comparator = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {comparator}")


def _raw_quantity(confirmed: float | None, estimated: float | None) -> float | None:
    return confirmed if confirmed is not None else estimated


def _load_exposure_state(position: TrackedPosition) -> PositionExposureState | None:
    state = dict(position.exit_state_json or {})
    baseline = state.get("exposure_baseline")
    episode_id = state.get("position_episode_id")
    exposure_version = state.get("exposure_version")
    if not isinstance(baseline, dict) or not episode_id or not isinstance(exposure_version, int):
        return None
    quantity = baseline.get("normalized_quantity")
    adjustment_factor = baseline.get("adjustment_factor", 1.0)
    current_factor = state.get("current_adjustment_factor", adjustment_factor)
    current_quantity = state.get("current_normalized_quantity")
    if not isinstance(current_quantity, (int, float)):
        raw = _raw_quantity(position.confirmed_shares, position.estimated_shares)
        current_quantity = float(raw) / float(current_factor) if raw is not None else 0.0
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value)
        for value in (quantity, adjustment_factor, current_factor, current_quantity)
    ):
        return None
    return PositionExposureState(
        position_episode_id=str(episode_id),
        exposure_version=exposure_version,
        baseline=ExposureBaseline(
            normalized_quantity=float(quantity),
            source=str(baseline.get("source") or "unknown"),
            adjustment_factor=float(adjustment_factor),
        ),
        current_normalized_quantity=float(current_quantity),
        current_adjustment_factor=float(current_factor),
        active=state.get("position_episode_status", "active") != "closed",
    )


def _store_exposure_state(state: dict[str, Any], exposure: PositionExposureState) -> None:
    state.update(
        {
            "position_episode_id": exposure.position_episode_id,
            "position_episode_status": "active" if exposure.active else "closed",
            "exposure_version": exposure.exposure_version,
            "exposure_baseline": {
                "normalized_quantity": exposure.baseline.normalized_quantity,
                "source": exposure.baseline.source,
                "adjustment_factor": exposure.baseline.adjustment_factor,
            },
            "current_normalized_quantity": exposure.current_normalized_quantity,
            "current_adjustment_factor": exposure.current_adjustment_factor,
        }
    )


def _event_id(command: ExposureMutationCommand) -> str:
    return stable_contract_hash(
        {
            "schema": "tracked_position_exposure_mutation_v1",
            "owner_id": command.owner_id,
            "position_id": command.position_id,
            "expected_version": command.expected_exit_state_version,
            "intent": command.intent.value,
            "source": command.source.value,
            "request_id": command.request_id,
        }
    )


async def initialize_tracked_position(
    session: AsyncSession,
    command: InitializeTrackedPositionCommand,
) -> TrackedPosition:
    _validate_optional_number("confirmed_shares", command.confirmed_shares)
    _validate_optional_number("estimated_shares", command.estimated_shares)
    _validate_optional_number("buy_amount", command.buy_amount, allow_zero=False)
    _validate_optional_number("entry_price", command.entry_price, allow_zero=False)
    raw_quantity = _raw_quantity(command.confirmed_shares, command.estimated_shares)
    state: dict[str, Any] = {
        "alert_rule_states": {},
        "current_action_id": None,
        "open_action_cycle_id": None,
    }
    exposure_version = 0
    if raw_quantity is not None and raw_quantity > 0:
        exposure = initialize_position_exposure(
            raw_quantity=float(raw_quantity),
            adjustment_factor=1.0,
            baseline_source=(
                "confirmed_shares"
                if command.confirmed_shares is not None
                else "estimated_shares"
            ),
            position_episode_id=str(uuid4()),
        )
        _store_exposure_state(state, exposure)
        state["evaluation_data_outcome"] = {
            "state": "eligible",
            "reason_code": "position_quantity_available",
        }
        exposure_version = exposure.exposure_version
    else:
        state["position_episode_status"] = "active"
        state["evaluation_data_outcome"] = {
            "state": "data_waiting",
            "reason_code": "position_quantity_unavailable",
        }

    position = TrackedPosition(
        user_id=command.owner_id,
        asset_type=command.asset_type,
        asset_code=command.asset_code,
        asset_name=command.asset_name,
        buy_date=command.buy_date,
        order_time_bucket=command.order_time_bucket,
        confirmed_nav_date=command.confirmed_nav_date,
        confirmed_nav=command.confirmed_nav,
        confirmed_shares=command.confirmed_shares,
        buy_amount=round(command.buy_amount, 2),
        entry_price=command.entry_price,
        entry_price_date=command.entry_price_date,
        estimated_shares=command.estimated_shares,
        exit_state_json=state,
        exit_state_version=1,
        status="active",
        note=command.note,
    )
    session.add(position)
    await session.flush()
    event_id = stable_contract_hash(
        {
            "schema": "tracked_position_exposure_mutation_v1",
            "position_id": position.id,
            "source": ExposureMutationSource.CREATE.value,
            "request_id": command.request_id,
        }
    )
    session.add(
        TrackedPositionAlertAudit(
            tracked_position_id=position.id,
            outcome="exposure_mutation",
            alert_date=command.occurred_at.date(),
            alert_type="position_exposure_mutation",
            trigger_label="initialized",
            data_source=ExposureMutationSource.CREATE.value,
            quote_freshness="not_applicable",
            threshold_context_json={"intent": "initialize"},
            decision_context_json={
                "event": "initialized",
                "previous_exposure_version": 0,
                "next_exposure_version": exposure_version,
            },
            event_id=event_id,
            event_schema_version="tracked_position_exposure_mutation_v1",
            data_state=(state.get("evaluation_data_outcome") or {}).get("state"),
            from_state="exposure:0",
            to_state=f"exposure:{exposure_version}",
            actor_id=command.owner_id,
            request_id=command.request_id,
            occurred_at=command.occurred_at,
            execution_provenance="none",
        )
    )
    await session.flush()
    return position


async def apply_exposure_mutation(
    session: AsyncSession,
    command: ExposureMutationCommand,
) -> ExposureMutationReceipt:
    _validate_optional_number("confirmed_shares", command.new_confirmed_shares)
    _validate_optional_number("estimated_shares", command.new_estimated_shares)
    _validate_optional_number("buy_amount", command.new_buy_amount, allow_zero=False)
    _validate_optional_number("entry_price", command.new_entry_price, allow_zero=False)
    if not math.isfinite(command.adjustment_factor) or command.adjustment_factor <= 0:
        raise ValueError("adjustment_factor must be finite and positive")
    if not command.request_id.strip():
        raise ValueError("request_id is required")

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
    event_id = _event_id(command)
    existing_audit = await session.scalar(
        select(TrackedPositionAlertAudit).where(TrackedPositionAlertAudit.event_id == event_id)
    )
    if existing_audit is not None:
        return ExposureMutationReceipt(
            position=position,
            event=str(existing_audit.decision_context_json.get("event") or "duplicate"),
            exit_state_version=position.exit_state_version,
            audit_event_id=event_id,
            superseded_action_id=existing_audit.action_decision_id,
        )
    if position.exit_state_version != command.expected_exit_state_version:
        raise StalePositionStateError("tracked position state version is stale")

    confirmed = (
        position.confirmed_shares
        if isinstance(command.new_confirmed_shares, _Unset)
        else command.new_confirmed_shares
    )
    estimated = (
        position.estimated_shares
        if isinstance(command.new_estimated_shares, _Unset)
        else command.new_estimated_shares
    )
    current_exposure = _load_exposure_state(position)
    state = dict(position.exit_state_json or {})
    event = command.intent.value
    supersede_actions = False
    reset_rules = False
    next_exposure = current_exposure

    if command.intent is ExposureMutationIntent.SYSTEM_ESTIMATE:
        if current_exposure is None:
            raw = _raw_quantity(confirmed, estimated)
            if raw is not None and raw > 0:
                next_exposure = initialize_position_exposure(
                    raw_quantity=float(raw),
                    adjustment_factor=command.adjustment_factor,
                    baseline_source="verified_estimated_shares",
                    position_episode_id=str(uuid4()),
                )
                event = "system_estimate_initialized"
            else:
                state["evaluation_data_outcome"] = {
                    "state": "data_waiting",
                    "reason_code": "position_quantity_unavailable",
                }
                event = "system_estimate_waiting"
        else:
            event = "system_estimate_updated"
    elif command.intent is ExposureMutationIntent.TRACKING_STATUS:
        event = "tracking_status_updated"
    else:
        raw = _raw_quantity(confirmed, estimated)
        if raw is None:
            raise ValueError("a quantity is required for exposure mutation")
        if current_exposure is None:
            if raw <= 0:
                raise ValueError("a positive quantity is required to initialize exposure")
            next_exposure = initialize_position_exposure(
                raw_quantity=float(raw),
                adjustment_factor=command.adjustment_factor,
                baseline_source="confirmed_shares" if confirmed is not None else "estimated_shares",
                position_episode_id=str(uuid4()),
            )
            event = "exposure_initialized"
        elif command.intent is ExposureMutationIntent.CORRECTION:
            normalized = float(raw) / command.adjustment_factor
            if not math.isclose(
                normalized,
                current_exposure.current_normalized_quantity,
                rel_tol=1e-8,
                abs_tol=1e-8,
            ):
                if not command.reason_code:
                    raise ValueError("quantity correction requires reason_code")
                next_exposure = PositionExposureState(
                    position_episode_id=current_exposure.position_episode_id,
                    exposure_version=current_exposure.exposure_version + 1,
                    baseline=ExposureBaseline(
                        normalized_quantity=normalized,
                        source="confirmed_correction",
                        adjustment_factor=command.adjustment_factor,
                    ),
                    current_normalized_quantity=normalized,
                    current_adjustment_factor=command.adjustment_factor,
                )
                event = "correction_rebased"
                supersede_actions = True
                reset_rules = True
        else:
            mutation_kind = (
                ExposureMutationKind.CORPORATE_ACTION
                if command.intent is ExposureMutationIntent.CORPORATE_ACTION
                else ExposureMutationKind.OWNER_TRADE
            )
            result = mutate_position_exposure(
                current_exposure,
                raw_quantity=float(raw),
                adjustment_factor=command.adjustment_factor,
                baseline_source="confirmed_shares" if confirmed is not None else "estimated_shares",
                mutation_kind=mutation_kind,
                new_position_episode_id=(
                    str(uuid4()) if command.intent is ExposureMutationIntent.REOPEN else None
                ),
            )
            expected_event = {
                ExposureMutationIntent.NET_ADD: "net_add",
                ExposureMutationIntent.NET_REDUCE: "reduction",
                ExposureMutationIntent.CLOSE: "closed",
                ExposureMutationIntent.REOPEN: "reentry",
                ExposureMutationIntent.CORPORATE_ACTION: "corporate_action",
            }[command.intent]
            if result.event != expected_event:
                raise ValueError(
                    f"{command.intent.value} does not match quantity transition {result.event}"
                )
            next_exposure = result.current
            event = result.event
            supersede_actions = result.supersede_prior_actions
            reset_rules = result.reset_rule_state

    previous_exposure_version = current_exposure.exposure_version if current_exposure else 0
    next_exposure_version = next_exposure.exposure_version if next_exposure else 0
    if next_exposure is not None and command.intent is not ExposureMutationIntent.SYSTEM_ESTIMATE:
        _store_exposure_state(state, next_exposure)
    elif next_exposure is not None and current_exposure is None:
        _store_exposure_state(state, next_exposure)
    if reset_rules:
        state["alert_rule_states"] = {}
        state["current_action_id"] = None
        state["open_action_cycle_id"] = None

    current_action = None
    if supersede_actions:
        current_action = await session.scalar(
            select(TrackedPositionActionDecision).where(
                TrackedPositionActionDecision.tracked_position_id == position.id,
                TrackedPositionActionDecision.is_current,
            )
        )
        if current_action is not None and current_action.id != command.protected_action_id:
            current_action.status = "superseded"
            current_action.status_reason = (
                "exposure_net_add" if event == "net_add" else f"exposure_{event}"
            )
            current_action.is_current = False
            current_action.superseded_at = command.occurred_at
        elif current_action is not None:
            current_action = None

    values: dict[str, Any] = {
        "exit_state_json": state,
        "exit_state_version": command.expected_exit_state_version + 1,
        "updated_at": utcnow(),
    }
    for field, value in (
        ("confirmed_shares", command.new_confirmed_shares),
        ("estimated_shares", command.new_estimated_shares),
        ("buy_amount", command.new_buy_amount),
        ("status", command.new_status),
        ("confirmed_nav_date", command.new_confirmed_nav_date),
        ("entry_price", command.new_entry_price),
        ("entry_price_date", command.new_entry_price_date),
    ):
        if not isinstance(value, _Unset):
            values[field] = value
    cas = await session.execute(
        update(TrackedPosition)
        .where(
            TrackedPosition.id == command.position_id,
            TrackedPosition.user_id == command.owner_id,
            TrackedPosition.exit_state_version == command.expected_exit_state_version,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if cas.rowcount != 1:
        raise StalePositionStateError("tracked position state version is stale")

    audit = TrackedPositionAlertAudit(
        tracked_position_id=position.id,
        outcome="exposure_mutation",
        alert_date=command.occurred_at.date(),
        alert_type="position_exposure_mutation",
        trigger_label=event,
        data_source=command.source.value,
        quote_freshness="not_applicable",
        threshold_context_json={
            "intent": command.intent.value,
            "reason_code": command.reason_code,
        },
        decision_context_json={
            "event": event,
            "previous_exposure_version": previous_exposure_version,
            "next_exposure_version": next_exposure_version,
            **(command.audit_context or {}),
        },
        event_id=event_id,
        event_schema_version="tracked_position_exposure_mutation_v1",
        action_decision_id=(
            command.linked_action_id
            if command.linked_action_id is not None
            else (current_action.id if current_action is not None else None)
        ),
        policy_version=state.get("policy_version"),
        data_state=(state.get("evaluation_data_outcome") or {}).get("state", "eligible"),
        from_state=f"exposure:{previous_exposure_version}",
        to_state=f"exposure:{next_exposure_version}",
        actor_id=command.actor_id,
        request_id=command.request_id,
        occurred_at=command.occurred_at,
        execution_provenance=command.execution_provenance,
    )
    session.add(audit)
    await session.flush()
    session.expire(position)
    await session.refresh(position)
    return ExposureMutationReceipt(
        position=position,
        event=event,
        exit_state_version=command.expected_exit_state_version + 1,
        audit_event_id=event_id,
        superseded_action_id=current_action.id if current_action is not None else None,
    )
