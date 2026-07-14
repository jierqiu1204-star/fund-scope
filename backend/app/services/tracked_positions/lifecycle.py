from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any


class EvaluationDataState(StrEnum):
    ELIGIBLE = "eligible"
    DATA_WAITING = "data_waiting"
    NO_DATA = "no_data"
    ERROR = "error"


class AlertState(StrEnum):
    NORMAL = "normal"
    PENDING = "pending"
    FIRING = "firing"
    RECOVERING = "recovering"
    RESOLVED = "resolved"


class AlertEvent(StrEnum):
    UNCHANGED = "unchanged"
    PENDING_STARTED = "pending_started"
    PENDING_CANCELLED = "pending_cancelled"
    FIRING_STARTED = "firing_started"
    HARD_STOP_BYPASS = "hard_stop_bypass"
    RECOVERY_STARTED = "recovery_started"
    RELAPSED = "relapsed"
    RESOLVED = "resolved"
    DATA_FROZEN = "data_frozen"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_EXECUTED = "partially_executed"
    EXECUTED = "executed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class ExecutionProvenance(StrEnum):
    NONE = "none"
    OWNER_CONFIRMED = "owner_confirmed"
    SIMULATED = "simulated"
    LEGACY_UNVERIFIED = "legacy_unverified"


class ActionTerminalCause(StrEnum):
    ALL_RULES_RESOLVED = "all_contributing_rules_resolved"
    VALID_UNTIL_ELAPSED = "valid_until_elapsed"
    OWNER_CANCELLED = "owner_cancelled"
    OWNER_EXECUTED = "owner_executed"
    EXPOSURE_NET_ADD = "exposure_net_add"
    POLICY_RETIRED = "policy_retired"
    POSITION_CLOSED = "position_closed"


class ExposureMutationKind(StrEnum):
    OWNER_TRADE = "owner_trade"
    CORPORATE_ACTION = "corporate_action"
    SYSTEM_ESTIMATE = "system_estimate"


@dataclass(frozen=True)
class EvaluationDataOutcome:
    state: EvaluationDataState
    reason_code: str

    @property
    def decision_eligible(self) -> bool:
        return self.state is EvaluationDataState.ELIGIBLE

    @classmethod
    def eligible(cls) -> EvaluationDataOutcome:
        return cls(EvaluationDataState.ELIGIBLE, "decision_eligible")

    @classmethod
    def invalid(cls, state: EvaluationDataState, reason_code: str) -> EvaluationDataOutcome:
        if state is EvaluationDataState.ELIGIBLE:
            raise ValueError("invalid data state must not be eligible")
        if not reason_code.strip():
            raise ValueError("reason_code is required")
        return cls(state, reason_code.strip())


@dataclass(frozen=True)
class AlertRuleState:
    state: AlertState = AlertState.NORMAL
    alert_episode_id: str | None = None
    confirmation_count: int = 0
    recovery_count: int = 0


@dataclass(frozen=True)
class ExposureBaseline:
    normalized_quantity: float
    source: str
    adjustment_factor: float


@dataclass(frozen=True)
class PositionExposureState:
    position_episode_id: str
    exposure_version: int
    baseline: ExposureBaseline
    current_normalized_quantity: float
    current_adjustment_factor: float
    active: bool = True


@dataclass(frozen=True)
class ExposureMutationResult:
    previous: PositionExposureState
    current: PositionExposureState
    event: str
    supersede_prior_actions: bool = False
    reset_rule_state: bool = False


@dataclass(frozen=True)
class AbsolutePositionTarget:
    target_remaining_fraction: float
    target_stage: str
    baseline_normalized_quantity: float
    calculated_target_normalized_quantity: float
    effective_target_normalized_quantity: float
    calculated_target_raw_quantity: float
    effective_target_raw_quantity: float
    recommended_sell_normalized_quantity: float
    recommended_sell_raw_quantity: float
    recommended_buy_normalized_quantity: float
    target_account_weight: float | None
    target_already_satisfied: bool


@dataclass(frozen=True)
class RuleActionCandidate:
    rule_id: str
    target_remaining_fraction: float
    reason: str
    eligible: bool = True


@dataclass(frozen=True)
class ActionAggregationResult:
    target: AbsolutePositionTarget | None
    contributing_rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    disposition: str
    supersedes_current: bool = False
    prior_target_stage: str | None = None


OPEN_ACTION_STATUSES = frozenset(
    {
        ActionStatus.PROPOSED,
        ActionStatus.ACKNOWLEDGED,
        ActionStatus.PARTIALLY_EXECUTED,
    }
)
TERMINAL_ACTION_STATUSES = frozenset(
    {
        ActionStatus.EXECUTED,
        ActionStatus.EXPIRED,
        ActionStatus.CANCELLED,
        ActionStatus.SUPERSEDED,
    }
)


@dataclass(frozen=True)
class ActionTerminalPlan:
    next_status: ActionStatus
    reason_code: str
    close_action_cycle: bool
    timestamp_field: str


def plan_action_terminal_transition(
    current_status: ActionStatus,
    cause: ActionTerminalCause,
    *,
    data_eligible: bool = False,
    all_contributing_rules_resolved: bool = False,
) -> ActionTerminalPlan:
    if current_status not in OPEN_ACTION_STATUSES:
        raise ValueError("terminal actions are immutable")
    if cause is ActionTerminalCause.ALL_RULES_RESOLVED:
        if not data_eligible or not all_contributing_rules_resolved:
            raise ValueError("resolution requires eligible data and all contributing rules resolved")
        return ActionTerminalPlan(ActionStatus.EXPIRED, cause.value, True, "expired_at")
    if cause is ActionTerminalCause.VALID_UNTIL_ELAPSED:
        return ActionTerminalPlan(ActionStatus.EXPIRED, cause.value, False, "expired_at")
    if cause is ActionTerminalCause.OWNER_CANCELLED:
        return ActionTerminalPlan(ActionStatus.CANCELLED, cause.value, False, "cancelled_at")
    if cause is ActionTerminalCause.OWNER_EXECUTED:
        return ActionTerminalPlan(ActionStatus.EXECUTED, cause.value, False, "executed_at")
    return ActionTerminalPlan(ActionStatus.SUPERSEDED, cause.value, True, "superseded_at")


def calculate_absolute_position_target(
    *,
    baseline_normalized_quantity: float,
    current_normalized_quantity: float,
    current_adjustment_factor: float,
    target_remaining_fraction: float,
    baseline_account_weight: float | None = None,
    tolerance: float = 1e-8,
) -> AbsolutePositionTarget:
    for name, value in (
        ("baseline_normalized_quantity", baseline_normalized_quantity),
        ("current_normalized_quantity", current_normalized_quantity),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if not math.isfinite(current_adjustment_factor) or current_adjustment_factor <= 0:
        raise ValueError("current_adjustment_factor must be finite and positive")
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and non-negative")
    stage = target_stage_for_fraction(target_remaining_fraction)
    if baseline_account_weight is not None and (
        not math.isfinite(baseline_account_weight) or not 0.0 <= baseline_account_weight <= 1.0
    ):
        raise ValueError("baseline_account_weight must be a finite fraction")

    calculated_target = baseline_normalized_quantity * target_remaining_fraction
    target_already_satisfied = current_normalized_quantity <= calculated_target + tolerance
    effective_target = min(current_normalized_quantity, calculated_target)
    sell_quantity = 0.0 if target_already_satisfied else current_normalized_quantity - calculated_target
    target_weight = (
        baseline_account_weight * target_remaining_fraction if baseline_account_weight is not None else None
    )
    return AbsolutePositionTarget(
        target_remaining_fraction=target_remaining_fraction,
        target_stage=stage,
        baseline_normalized_quantity=baseline_normalized_quantity,
        calculated_target_normalized_quantity=calculated_target,
        effective_target_normalized_quantity=effective_target,
        calculated_target_raw_quantity=calculated_target * current_adjustment_factor,
        effective_target_raw_quantity=effective_target * current_adjustment_factor,
        recommended_sell_normalized_quantity=sell_quantity,
        recommended_sell_raw_quantity=sell_quantity * current_adjustment_factor,
        recommended_buy_normalized_quantity=0.0,
        target_account_weight=target_weight,
        target_already_satisfied=target_already_satisfied,
    )


def aggregate_action_candidates(
    candidates: list[RuleActionCandidate],
    *,
    baseline_normalized_quantity: float,
    current_normalized_quantity: float,
    current_adjustment_factor: float,
    baseline_account_weight: float | None = None,
    current_action_target_fraction: float | None = None,
) -> ActionAggregationResult:
    eligible = [candidate for candidate in candidates if candidate.eligible]
    for candidate in eligible:
        if not candidate.rule_id.strip() or not candidate.reason.strip():
            raise ValueError("eligible action candidates require rule_id and reason")
        target_stage_for_fraction(candidate.target_remaining_fraction)
    actionable = [candidate for candidate in eligible if candidate.target_remaining_fraction < 1.0]
    if not actionable:
        return ActionAggregationResult(None, (), (), "none")

    ordered = sorted(actionable, key=lambda item: (item.rule_id, item.reason))
    rule_ids = tuple(dict.fromkeys(item.rule_id for item in ordered))
    reasons = tuple(dict.fromkeys(item.reason for item in ordered))
    proposed_fraction = min(item.target_remaining_fraction for item in actionable)
    prior_stage = None
    disposition = "create"
    supersedes_current = False
    effective_fraction = proposed_fraction
    if current_action_target_fraction is not None:
        prior_stage = target_stage_for_fraction(current_action_target_fraction)
        if proposed_fraction < current_action_target_fraction:
            disposition = "supersede"
            supersedes_current = True
        else:
            disposition = "reuse_current"
            effective_fraction = current_action_target_fraction

    target = calculate_absolute_position_target(
        baseline_normalized_quantity=baseline_normalized_quantity,
        current_normalized_quantity=current_normalized_quantity,
        current_adjustment_factor=current_adjustment_factor,
        target_remaining_fraction=effective_fraction,
        baseline_account_weight=baseline_account_weight,
    )
    if disposition == "create" and target.target_already_satisfied:
        disposition = "target_already_satisfied"
    return ActionAggregationResult(
        target=target,
        contributing_rule_ids=rule_ids,
        reasons=reasons,
        disposition=disposition,
        supersedes_current=supersedes_current,
        prior_target_stage=prior_stage,
    )


def _normalized_quantity(raw_quantity: float, adjustment_factor: float) -> float:
    if not math.isfinite(raw_quantity) or raw_quantity < 0:
        raise ValueError("raw_quantity must be finite and non-negative")
    if not math.isfinite(adjustment_factor) or adjustment_factor <= 0:
        raise ValueError("adjustment_factor must be finite and positive")
    return raw_quantity / adjustment_factor


def initialize_position_exposure(
    *,
    raw_quantity: float,
    adjustment_factor: float,
    baseline_source: str,
    position_episode_id: str,
) -> PositionExposureState:
    normalized_quantity = _normalized_quantity(raw_quantity, adjustment_factor)
    if normalized_quantity <= 0:
        raise ValueError("an active exposure requires a positive quantity")
    if not baseline_source.strip():
        raise ValueError("baseline_source is required")
    if not position_episode_id.strip():
        raise ValueError("position_episode_id is required")
    return PositionExposureState(
        position_episode_id=position_episode_id,
        exposure_version=1,
        baseline=ExposureBaseline(
            normalized_quantity=normalized_quantity,
            source=baseline_source.strip(),
            adjustment_factor=adjustment_factor,
        ),
        current_normalized_quantity=normalized_quantity,
        current_adjustment_factor=adjustment_factor,
    )


def mutate_position_exposure(
    current: PositionExposureState,
    *,
    raw_quantity: float,
    adjustment_factor: float,
    baseline_source: str,
    mutation_kind: ExposureMutationKind,
    new_position_episode_id: str | None = None,
    tolerance: float = 1e-8,
) -> ExposureMutationResult:
    normalized_quantity = _normalized_quantity(raw_quantity, adjustment_factor)
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and non-negative")

    if not current.active:
        if normalized_quantity <= tolerance:
            return ExposureMutationResult(current, current, "unchanged")
        if not new_position_episode_id:
            raise ValueError("new_position_episode_id is required for reentry")
        reopened = initialize_position_exposure(
            raw_quantity=raw_quantity,
            adjustment_factor=adjustment_factor,
            baseline_source=baseline_source,
            position_episode_id=new_position_episode_id,
        )
        return ExposureMutationResult(
            current,
            reopened,
            "reentry",
            supersede_prior_actions=True,
            reset_rule_state=True,
        )

    if mutation_kind is ExposureMutationKind.CORPORATE_ACTION:
        if not math.isclose(
            normalized_quantity,
            current.current_normalized_quantity,
            rel_tol=tolerance,
            abs_tol=tolerance,
        ):
            raise ValueError("corporate action must preserve normalized quantity")
        adjusted = PositionExposureState(
            position_episode_id=current.position_episode_id,
            exposure_version=current.exposure_version,
            baseline=current.baseline,
            current_normalized_quantity=normalized_quantity,
            current_adjustment_factor=adjustment_factor,
        )
        return ExposureMutationResult(current, adjusted, "corporate_action")

    delta = normalized_quantity - current.current_normalized_quantity
    if delta > tolerance:
        if not baseline_source.strip():
            raise ValueError("baseline_source is required")
        added = PositionExposureState(
            position_episode_id=current.position_episode_id,
            exposure_version=current.exposure_version + 1,
            baseline=ExposureBaseline(
                normalized_quantity=normalized_quantity,
                source=baseline_source.strip(),
                adjustment_factor=adjustment_factor,
            ),
            current_normalized_quantity=normalized_quantity,
            current_adjustment_factor=adjustment_factor,
        )
        return ExposureMutationResult(
            current,
            added,
            "net_add",
            supersede_prior_actions=True,
            reset_rule_state=True,
        )
    if normalized_quantity <= tolerance:
        closed = PositionExposureState(
            position_episode_id=current.position_episode_id,
            exposure_version=current.exposure_version,
            baseline=current.baseline,
            current_normalized_quantity=0.0,
            current_adjustment_factor=adjustment_factor,
            active=False,
        )
        return ExposureMutationResult(
            current,
            closed,
            "closed",
            supersede_prior_actions=True,
            reset_rule_state=True,
        )
    if delta < -tolerance:
        reduced = PositionExposureState(
            position_episode_id=current.position_episode_id,
            exposure_version=current.exposure_version,
            baseline=current.baseline,
            current_normalized_quantity=normalized_quantity,
            current_adjustment_factor=adjustment_factor,
        )
        return ExposureMutationResult(current, reduced, "reduction")
    return ExposureMutationResult(current, current, "unchanged")


@dataclass(frozen=True)
class AlertTransition:
    previous: AlertRuleState
    current: AlertRuleState
    event: AlertEvent
    emits_action: bool = False
    frozen: bool = False
    closed_alert_episode_id: str | None = None

    @property
    def from_state(self) -> AlertState:
        return self.previous.state

    @property
    def to_state(self) -> AlertState:
        return self.current.state


def transition_alert_state(
    current: AlertRuleState,
    *,
    data_outcome: EvaluationDataOutcome,
    condition_met: bool,
    recovery_met: bool = False,
    confirmation_required: int = 2,
    recovery_required: int = 2,
    hard_stop: bool = False,
    new_alert_episode_id: str | None = None,
) -> AlertTransition:
    if confirmation_required < 1 or recovery_required < 1:
        raise ValueError("confirmation and recovery thresholds must be positive")
    if not data_outcome.decision_eligible:
        return AlertTransition(
            previous=current,
            current=current,
            event=AlertEvent.DATA_FROZEN,
            frozen=True,
        )

    if current.state in {AlertState.NORMAL, AlertState.RESOLVED}:
        if not condition_met:
            return AlertTransition(current, current, AlertEvent.UNCHANGED)
        if not new_alert_episode_id:
            raise ValueError("new_alert_episode_id is required when an alert episode opens")
        if hard_stop or confirmation_required == 1:
            next_state = AlertRuleState(
                state=AlertState.FIRING,
                alert_episode_id=new_alert_episode_id,
                confirmation_count=1,
            )
            event = AlertEvent.HARD_STOP_BYPASS if hard_stop else AlertEvent.FIRING_STARTED
            return AlertTransition(current, next_state, event, emits_action=True)
        next_state = AlertRuleState(
            state=AlertState.PENDING,
            alert_episode_id=new_alert_episode_id,
            confirmation_count=1,
        )
        return AlertTransition(current, next_state, AlertEvent.PENDING_STARTED)

    if current.state is AlertState.PENDING:
        if not condition_met:
            next_state = AlertRuleState()
            return AlertTransition(
                current,
                next_state,
                AlertEvent.PENDING_CANCELLED,
                closed_alert_episode_id=current.alert_episode_id,
            )
        confirmation_count = current.confirmation_count + 1
        if hard_stop or confirmation_count >= confirmation_required:
            next_state = AlertRuleState(
                state=AlertState.FIRING,
                alert_episode_id=current.alert_episode_id,
                confirmation_count=confirmation_count,
            )
            event = AlertEvent.HARD_STOP_BYPASS if hard_stop else AlertEvent.FIRING_STARTED
            return AlertTransition(current, next_state, event, emits_action=True)
        next_state = AlertRuleState(
            state=AlertState.PENDING,
            alert_episode_id=current.alert_episode_id,
            confirmation_count=confirmation_count,
        )
        return AlertTransition(current, next_state, AlertEvent.UNCHANGED)

    if current.state is AlertState.FIRING:
        if condition_met:
            next_state = AlertRuleState(
                state=AlertState.FIRING,
                alert_episode_id=current.alert_episode_id,
                confirmation_count=current.confirmation_count,
            )
            return AlertTransition(current, next_state, AlertEvent.UNCHANGED)
        if not recovery_met:
            return AlertTransition(current, current, AlertEvent.UNCHANGED)
        if recovery_required == 1:
            next_state = AlertRuleState(
                state=AlertState.RESOLVED,
                alert_episode_id=current.alert_episode_id,
                confirmation_count=current.confirmation_count,
                recovery_count=1,
            )
            return AlertTransition(
                current,
                next_state,
                AlertEvent.RESOLVED,
                closed_alert_episode_id=current.alert_episode_id,
            )
        next_state = AlertRuleState(
            state=AlertState.RECOVERING,
            alert_episode_id=current.alert_episode_id,
            confirmation_count=current.confirmation_count,
            recovery_count=1,
        )
        return AlertTransition(current, next_state, AlertEvent.RECOVERY_STARTED)

    if condition_met:
        next_state = AlertRuleState(
            state=AlertState.FIRING,
            alert_episode_id=current.alert_episode_id,
            confirmation_count=current.confirmation_count,
        )
        return AlertTransition(current, next_state, AlertEvent.RELAPSED)
    recovery_count = current.recovery_count + 1 if recovery_met else 0
    if recovery_count >= recovery_required:
        next_state = AlertRuleState(
            state=AlertState.RESOLVED,
            alert_episode_id=current.alert_episode_id,
            confirmation_count=current.confirmation_count,
            recovery_count=recovery_count,
        )
        return AlertTransition(
            current,
            next_state,
            AlertEvent.RESOLVED,
            closed_alert_episode_id=current.alert_episode_id,
        )
    next_state = AlertRuleState(
        state=AlertState.RECOVERING,
        alert_episode_id=current.alert_episode_id,
        confirmation_count=current.confirmation_count,
        recovery_count=recovery_count,
    )
    return AlertTransition(current, next_state, AlertEvent.UNCHANGED)


@dataclass(frozen=True)
class NotificationItemPayload:
    alert_episode_id: str
    transition: str
    recipient: str
    channel: str
    repeat_slot: str

    @property
    def item_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.alert_episode_id,
            self.transition,
            self.recipient,
            self.channel,
            self.repeat_slot,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NotificationEnvelopePayload:
    owner_id: int
    trade_session: date
    route: str
    severity: str
    channel: str
    sealed_snapshot_hash: str
    digest_revision: int

    @property
    def envelope_key(self) -> tuple[int, str, str, str, str, str, int]:
        return (
            self.owner_id,
            self.trade_session.isoformat(),
            self.route,
            self.severity,
            self.channel,
            self.sealed_snapshot_hash,
            self.digest_revision,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "trade_session": self.trade_session.isoformat(),
        }


def target_stage_for_fraction(target_remaining_fraction: float) -> str:
    if not math.isfinite(target_remaining_fraction) or not 0.0 <= target_remaining_fraction <= 1.0:
        raise ValueError("target must be a finite fraction between 0 and 1")
    basis_points = int(round(target_remaining_fraction * 10_000))
    return f"remaining_{basis_points}bp"


def _normalize_contract_value(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_contract_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize_contract_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_contract_value(item) for item in value]
    return value


def stable_contract_json(value: Any) -> str:
    return json.dumps(
        _normalize_contract_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_contract_hash(value: Any) -> str:
    return hashlib.sha256(stable_contract_json(value).encode("utf-8")).hexdigest()
