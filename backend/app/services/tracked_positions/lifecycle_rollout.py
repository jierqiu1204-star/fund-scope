from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionLifecycleShadowEvidence,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash

_CUTOVER_EVIDENCE_COLLECTOR_TOKEN = object()
_MAX_CUTOVER_EVALUATIONS = 10_000
_MIN_CUTOVER_DISTINCT_SESSIONS = 3


class LifecycleRolloutMode(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    READ_ONLY = "read_only"
    ACTIONS_ONLY = "actions_only"
    ACTIVE = "active"
    DISPLAY_ONLY = "display_only"


@dataclass(frozen=True)
class LifecycleRolloutPolicy:
    mode: LifecycleRolloutMode
    v2_action_writes_enabled: bool
    v2_read_priority_enabled: bool
    notification_generation_enabled: bool

    def __post_init__(self) -> None:
        mode = LifecycleRolloutMode(self.mode)
        flags = (
            self.v2_action_writes_enabled,
            self.v2_read_priority_enabled,
            self.notification_generation_enabled,
        )
        expected = {
            LifecycleRolloutMode.DISABLED: (False, False, False),
            LifecycleRolloutMode.SHADOW: (False, False, False),
            LifecycleRolloutMode.READ_ONLY: (False, True, False),
            LifecycleRolloutMode.ACTIONS_ONLY: (True, True, False),
            LifecycleRolloutMode.ACTIVE: (True, True, True),
            LifecycleRolloutMode.DISPLAY_ONLY: (False, True, False),
        }[mode]
        if any(type(value) is not bool for value in flags) or flags != expected:
            raise ValueError("rollout policy flags do not match mode")
        object.__setattr__(self, "mode", mode)

    @property
    def legacy_relative_action_writes_enabled(self) -> bool:
        return False

    @classmethod
    def for_mode(cls, mode: LifecycleRolloutMode | str) -> LifecycleRolloutPolicy:
        resolved = LifecycleRolloutMode(mode)
        if resolved is LifecycleRolloutMode.ACTIVE:
            return cls(resolved, True, True, True)
        if resolved is LifecycleRolloutMode.ACTIONS_ONLY:
            return cls(resolved, True, True, False)
        if resolved in {
            LifecycleRolloutMode.READ_ONLY,
            LifecycleRolloutMode.DISPLAY_ONLY,
        }:
            return cls(resolved, False, True, False)
        if resolved is LifecycleRolloutMode.SHADOW:
            return cls(resolved, False, False, False)
        return cls(resolved, False, False, False)

    @property
    def shadow_evidence_writes_enabled(self) -> bool:
        return self.mode is LifecycleRolloutMode.SHADOW

    @property
    def production_state_writes_enabled(self) -> bool:
        return self.mode in {
            LifecycleRolloutMode.ACTIONS_ONLY,
            LifecycleRolloutMode.ACTIVE,
        }


@dataclass(frozen=True)
class PositionActionProjection:
    source: str
    action_id: int | None
    status: str | None
    label: str | None
    target_remaining_fraction: float | None
    execution_provenance: str | None
    executable: bool


@dataclass(frozen=True)
class CutoverGateObservation:
    evaluation_count: int
    error_count: int
    repeated_same_stage_actions: int = 0
    actions_from_ineligible_data: int = 0
    smtp_to_executed_transitions: int = 0
    strictest_target_mismatches: int = 0
    unexplained_legacy_differences: int = 0
    policy_version: str | None = None
    sealed_snapshot_hashes: tuple[str, ...] = ()
    session_start: date | None = None
    session_end: date | None = None
    distinct_session_count: int = 0
    collected_scope_valid: bool = False
    collection_hash: str = ""
    _collector_token: object | None = field(default=None, repr=False, compare=False)
    _attested_values: tuple[object, ...] | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class CutoverGateResult:
    passed: bool
    error_rate: float
    failures: tuple[str, ...]


def _observation_values(observation: CutoverGateObservation) -> tuple[object, ...]:
    return (
        observation.evaluation_count,
        observation.error_count,
        observation.repeated_same_stage_actions,
        observation.actions_from_ineligible_data,
        observation.smtp_to_executed_transitions,
        observation.strictest_target_mismatches,
        observation.unexplained_legacy_differences,
        observation.policy_version,
        observation.sealed_snapshot_hashes,
        observation.session_start,
        observation.session_end,
        observation.distinct_session_count,
        observation.collected_scope_valid,
        observation.collection_hash,
    )


async def collect_cutover_gate_observation(
    session: AsyncSession,
    *,
    policy_version: str,
    session_start: date,
    session_end: date,
    max_evaluations: int,
) -> CutoverGateObservation:
    normalized_policy = policy_version.strip()
    if not normalized_policy:
        raise ValueError("policy_version is required")
    if session_start > session_end:
        raise ValueError("session_start must not be after session_end")
    if (
        not isinstance(max_evaluations, int)
        or isinstance(max_evaluations, bool)
        or not 1 <= max_evaluations <= _MAX_CUTOVER_EVALUATIONS
    ):
        raise ValueError(
            f"max_evaluations must be between 1 and {_MAX_CUTOVER_EVALUATIONS}"
        )

    rows = list(
        (
            await session.scalars(
                select(TrackedPositionLifecycleShadowEvidence)
                .where(
                    TrackedPositionLifecycleShadowEvidence.policy_version
                    == normalized_policy,
                    TrackedPositionLifecycleShadowEvidence.trade_session >= session_start,
                    TrackedPositionLifecycleShadowEvidence.trade_session <= session_end,
                    TrackedPositionLifecycleShadowEvidence.exposure_version >= 1,
                )
                .order_by(
                    TrackedPositionLifecycleShadowEvidence.trade_session,
                    TrackedPositionLifecycleShadowEvidence.occurred_at,
                    TrackedPositionLifecycleShadowEvidence.id,
                )
                .limit(max_evaluations + 1)
            )
        ).all()
    )
    if len(rows) > max_evaluations:
        raise ValueError("collected evidence exceeds max_evaluations")
    normalized_hashes = tuple(sorted({row.sealed_snapshot_hash for row in rows}))
    collected_scope_valid = bool(normalized_hashes) and all(
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in normalized_hashes
    )
    stage_counts: dict[tuple[object, ...], int] = {}
    error_count = 0
    actions_from_ineligible_data = 0
    strictest_target_mismatches = 0
    unexplained_legacy_differences = 0
    for row in rows:
        evidence = dict(row.action_evidence_json or {})
        disposition = str(evidence.get("disposition") or "")
        actionable = disposition in {"create", "supersede"}
        if row.data_state == "error":
            error_count += 1
        if actionable and row.data_state != "eligible":
            actions_from_ineligible_data += 1
        candidates = evidence.get("candidate_target_fractions")
        selected = evidence.get("target_remaining_fraction")
        if actionable and isinstance(candidates, list) and candidates:
            finite_candidates = [
                float(value)
                for value in candidates
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]
            if finite_candidates and (
                not isinstance(selected, (int, float))
                or isinstance(selected, bool)
                or float(selected) != min(finite_candidates)
            ):
                strictest_target_mismatches += 1
        legacy_difference = evidence.get("legacy_difference")
        if (
            evidence.get("legacy_comparison_status") != "compared"
            or not isinstance(legacy_difference, bool)
            or (
                legacy_difference
                and evidence.get("legacy_difference_explained") is not True
            )
        ):
            unexplained_legacy_differences += 1
        if actionable:
            action_cycle_id = evidence.get("action_cycle_id")
            target_stage = evidence.get("target_stage")
            if not action_cycle_id or not target_stage:
                error_count += 1
            else:
                key = (
                    row.user_id,
                    row.tracked_position_id,
                    row.position_episode_id,
                    row.exposure_version,
                    action_cycle_id,
                    target_stage,
                )
                stage_counts[key] = stage_counts.get(key, 0) + 1

    repeated_same_stage_actions = sum(max(0, count - 1) for count in stage_counts.values())
    smtp_to_executed_transitions = int(
        await session.scalar(
            select(func.count())
            .select_from(TrackedPositionAlertAudit)
            .join(
                TrackedPositionActionDecision,
                TrackedPositionAlertAudit.action_decision_id
                == TrackedPositionActionDecision.id,
            )
            .where(
                TrackedPositionAlertAudit.policy_version == normalized_policy,
                TrackedPositionAlertAudit.alert_date >= session_start,
                TrackedPositionAlertAudit.alert_date <= session_end,
                TrackedPositionAlertAudit.outcome == "action_transition",
                TrackedPositionAlertAudit.to_state.in_(
                    {"partially_executed", "executed"}
                ),
                or_(
                    func.lower(TrackedPositionAlertAudit.execution_provenance).like(
                        "smtp%"
                    ),
                    func.lower(TrackedPositionAlertAudit.execution_provenance).like(
                        "email%"
                    ),
                ),
            )
        )
        or 0
    )
    evidence_row_hashes = [
        stable_contract_hash(
            {
                "id": row.id,
                "user_id": row.user_id,
                "tracked_position_id": row.tracked_position_id,
                "policy_version": row.policy_version,
                "position_episode_id": row.position_episode_id,
                "exposure_version": row.exposure_version,
                "stream_sequence": row.stream_sequence,
                "predecessor_event_id": row.predecessor_event_id,
                "event_id": row.event_id,
                "event_schema_version": row.event_schema_version,
                "trade_session": row.trade_session,
                "repeat_slot": row.repeat_slot,
                "sealed_snapshot_hash": row.sealed_snapshot_hash,
                "production_position_state_version": (
                    row.production_position_state_version
                ),
                "rule_states_json": row.rule_states_json,
                "transitions_json": row.transitions_json,
                "action_evidence_json": row.action_evidence_json,
                "data_state": row.data_state,
                "occurred_at": row.occurred_at,
                "created_at": row.created_at,
            }
        )
        for row in rows
    ]
    collection_hash = stable_contract_hash(
        {
            "policy_version": normalized_policy,
            "sealed_snapshot_hashes": normalized_hashes,
            "session_start": session_start.isoformat(),
            "session_end": session_end.isoformat(),
            "event_ids": [row.event_id for row in rows],
            "evidence_row_hashes": evidence_row_hashes,
        }
    )
    observation = CutoverGateObservation(
        evaluation_count=len(rows),
        error_count=error_count,
        repeated_same_stage_actions=repeated_same_stage_actions,
        actions_from_ineligible_data=actions_from_ineligible_data,
        smtp_to_executed_transitions=smtp_to_executed_transitions,
        strictest_target_mismatches=strictest_target_mismatches,
        unexplained_legacy_differences=unexplained_legacy_differences,
        policy_version=normalized_policy,
        sealed_snapshot_hashes=normalized_hashes,
        session_start=session_start,
        session_end=session_end,
        distinct_session_count=len(
            {row.trade_session for row in rows if row.data_state == "eligible"}
        ),
        collected_scope_valid=collected_scope_valid,
        collection_hash=collection_hash,
        _collector_token=_CUTOVER_EVIDENCE_COLLECTOR_TOKEN,
    )
    object.__setattr__(observation, "_attested_values", _observation_values(observation))
    return observation


def evaluate_cutover_gates(
    observation: CutoverGateObservation,
    *,
    max_error_rate: float,
) -> CutoverGateResult:
    if observation._collector_token is not _CUTOVER_EVIDENCE_COLLECTOR_TOKEN:
        raise ValueError("cutover observation must come from immutable evidence")
    if observation._attested_values != _observation_values(observation):
        raise ValueError("cutover observation was modified after collection")
    if observation.evaluation_count <= 0:
        raise ValueError("cutover gates require at least one evaluation")
    counts = {
        "error_count": observation.error_count,
        "repeated_same_stage_actions": observation.repeated_same_stage_actions,
        "actions_from_ineligible_data": observation.actions_from_ineligible_data,
        "smtp_to_executed_transitions": observation.smtp_to_executed_transitions,
        "strictest_target_mismatches": observation.strictest_target_mismatches,
        "unexplained_legacy_differences": observation.unexplained_legacy_differences,
    }
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        raise ValueError("cutover gate counts must be non-negative integers")
    if not 0 <= max_error_rate <= 1:
        raise ValueError("max_error_rate must be between 0 and 1")

    failures = [] if observation.collected_scope_valid else ["invalid_collected_scope"]
    if observation.distinct_session_count < _MIN_CUTOVER_DISTINCT_SESSIONS:
        failures.append("insufficient_distinct_sessions")
    failures.extend(
        [
        name
        for name in (
            "repeated_same_stage_actions",
            "actions_from_ineligible_data",
            "smtp_to_executed_transitions",
            "strictest_target_mismatches",
            "unexplained_legacy_differences",
        )
        if counts[name] != 0
        ]
    )
    error_rate = observation.error_count / observation.evaluation_count
    if error_rate > max_error_rate:
        failures.append("error_rate_exceeded")
    return CutoverGateResult(
        passed=not failures,
        error_rate=error_rate,
        failures=tuple(failures),
    )
def safe_rollback_policy() -> LifecycleRolloutPolicy:
    return LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.DISPLAY_ONLY)


async def read_position_action_projection(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    rollout_policy: LifecycleRolloutPolicy,
) -> PositionActionProjection:
    position = await session.scalar(
        select(TrackedPosition).where(
            TrackedPosition.id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    if position is None:
        raise LookupError("tracked position not found for owner")

    if rollout_policy.v2_read_priority_enabled:
        action = await session.scalar(
            select(TrackedPositionActionDecision).where(
                TrackedPositionActionDecision.tracked_position_id == position_id,
                TrackedPositionActionDecision.user_id == owner_id,
                TrackedPositionActionDecision.is_current,
            )
        )
        if action is not None:
            return PositionActionProjection(
                source="v2",
                action_id=action.id,
                status=action.status,
                label=None,
                target_remaining_fraction=action.target_remaining_fraction,
                execution_provenance=action.execution_provenance,
                executable=(
                    rollout_policy.v2_action_writes_enabled
                    and action.status
                    in {"proposed", "acknowledged", "partially_executed"}
                ),
            )

    state = dict(position.exit_state_json or {})
    legacy = state.get("latest_position_action")
    if isinstance(legacy, dict) and legacy:
        label = legacy.get("recommended_action_label") or legacy.get("position_action")
        return PositionActionProjection(
            source="legacy_read_only",
            action_id=None,
            status="legacy_unverified",
            label=str(label) if label is not None else None,
            target_remaining_fraction=None,
            execution_provenance="legacy_unverified",
            executable=False,
        )

    return PositionActionProjection(
        source="none",
        action_id=None,
        status=None,
        label=None,
        target_remaining_fraction=None,
        execution_provenance=None,
        executable=False,
    )
