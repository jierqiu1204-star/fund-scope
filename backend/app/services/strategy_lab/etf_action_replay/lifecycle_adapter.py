from __future__ import annotations

import math

from app.services.tracked_positions.lifecycle import target_stage_for_fraction

from .contracts import (
    LifecycleEvent,
    LifecycleEventType,
    ReplayTradeIntent,
    TargetSemantics,
)


class RelativeActionContractError(ValueError):
    pass


class AbsoluteActionLifecycleAdapter:
    """Shared action-fact adapter for live projections and historical replay."""

    def __init__(self) -> None:
        self._cycle_targets: dict[str, float] = {}
        self._seen_action_decisions: dict[str, tuple[str, float, TargetSemantics]] = {}
        self._accepted_action_decision_ids: list[str] = []

    @property
    def accepted_action_decision_ids(self) -> tuple[str, ...]:
        return tuple(self._accepted_action_decision_ids)

    def consume(self, event: LifecycleEvent) -> ReplayTradeIntent | None:
        if event.event_type != LifecycleEventType.ACTION_DECISION:
            return None
        if not event.event_id.strip():
            raise ValueError("event_id is required")
        if not event.action_cycle_id or not event.action_cycle_id.strip():
            raise ValueError("action_cycle_id is required")
        if not event.action_decision_id or not event.action_decision_id.strip():
            raise ValueError("action_decision_id is required")
        if event.target_semantics == TargetSemantics.RELATIVE_CURRENT_POSITION:
            raise RelativeActionContractError("relative actions are not accepted by the v2 adapter")
        if event.target_semantics != TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE:
            raise ValueError("absolute exposure-baseline target semantics are required")
        target = event.target_remaining_fraction
        if (
            target is None
            or isinstance(target, bool)
            or not math.isfinite(target)
            or not 0.0 <= target <= 1.0
        ):
            raise ValueError("target_remaining_fraction must be a finite fraction between 0 and 1")

        decision_identity = (
            event.action_cycle_id,
            target,
            TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE,
        )
        prior_identity = self._seen_action_decisions.get(event.action_decision_id)
        if prior_identity is not None and prior_identity != decision_identity:
            raise ValueError("conflicting action decision identity")
        if prior_identity is not None:
            return None
        self._seen_action_decisions[event.action_decision_id] = decision_identity
        previous_target = self._cycle_targets.get(event.action_cycle_id)
        if previous_target is not None and target >= previous_target:
            return None

        self._cycle_targets[event.action_cycle_id] = target
        if target >= 1.0:
            return None
        self._accepted_action_decision_ids.append(event.action_decision_id)
        return ReplayTradeIntent(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            action_cycle_id=event.action_cycle_id,
            action_decision_id=event.action_decision_id,
            target_remaining_fraction=target,
            target_stage=target_stage_for_fraction(target),
        )


class LegacyCurrentSemanticsDiagnosticAdapter:
    """Isolated reproducer for historical relative-compounding diagnostics."""

    __slots__ = ()
    policy_name = "legacy_current_semantics"

    @property
    def promotion_eligible(self) -> bool:
        return False

    def apply_relative_target(
        self,
        *,
        current_fraction: float,
        relative_remaining_fraction: float,
    ) -> float:
        for name, value in (
            ("current_fraction", current_fraction),
            ("relative_remaining_fraction", relative_remaining_fraction),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite fraction between 0 and 1")
        return current_fraction * relative_remaining_fraction
