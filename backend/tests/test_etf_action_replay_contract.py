from __future__ import annotations

from datetime import datetime

import pytest

from app.services.strategy_lab.etf_action_replay import (
    AbsoluteActionLifecycleAdapter,
    LegacyCurrentSemanticsDiagnosticAdapter,
    LifecycleEvent,
    LifecycleEventType,
    RelativeActionContractError,
    TargetSemantics,
)


def _action(
    *,
    event_id: str,
    decision_id: str,
    cycle_id: str = "cycle-1",
    target: float = 0.5,
    semantics: TargetSemantics = TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE,
) -> LifecycleEvent:
    return LifecycleEvent(
        event_id=event_id,
        event_type=LifecycleEventType.ACTION_DECISION,
        occurred_at=datetime(2026, 1, 5, 15, 0),
        action_cycle_id=cycle_id,
        action_decision_id=decision_id,
        target_remaining_fraction=target,
        target_semantics=semantics,
    )


def test_v2_adapter_trades_only_unique_absolute_action_stages() -> None:
    adapter = AbsoluteActionLifecycleAdapter()

    first = adapter.consume(_action(event_id="event-1", decision_id="action-1"))
    assert first is not None
    assert first.action_cycle_id == "cycle-1"
    assert first.target_remaining_fraction == 0.5
    assert first.target_stage == "remaining_5000bp"

    assert adapter.consume(_action(event_id="event-1-retry", decision_id="action-1")) is None
    assert adapter.consume(_action(event_id="event-2", decision_id="action-2")) is None

    stricter = adapter.consume(
        _action(event_id="event-3", decision_id="action-3", target=0.0)
    )
    assert stricter is not None
    assert stricter.target_stage == "remaining_0bp"
    assert adapter.consume(_action(event_id="event-4", decision_id="action-4")) is None


@pytest.mark.parametrize(
    "event_type",
    [
        LifecycleEventType.NOTIFICATION_REPEAT,
        LifecycleEventType.NOTIFICATION_RETRY,
        LifecycleEventType.RECOVERY_NOTIFICATION,
        LifecycleEventType.SOFT_WATCH,
    ],
)
def test_non_action_events_never_create_trade_intents(event_type: LifecycleEventType) -> None:
    adapter = AbsoluteActionLifecycleAdapter()
    event = LifecycleEvent(
        event_id=f"event-{event_type.value}",
        event_type=event_type,
        occurred_at=datetime(2026, 1, 6, 10, 0),
        action_cycle_id="cycle-1",
        action_decision_id="action-1",
        target_remaining_fraction=0.0,
        target_semantics=TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE,
    )

    assert adapter.consume(event) is None
    assert adapter.accepted_action_decision_ids == ()


def test_v2_adapter_rejects_relative_action_contract() -> None:
    adapter = AbsoluteActionLifecycleAdapter()

    with pytest.raises(RelativeActionContractError, match="relative actions"):
        adapter.consume(
            _action(
                event_id="event-relative",
                decision_id="action-relative",
                semantics=TargetSemantics.RELATIVE_CURRENT_POSITION,
            )
        )


def test_action_decision_identity_cannot_be_reused_with_different_target() -> None:
    adapter = AbsoluteActionLifecycleAdapter()
    adapter.consume(_action(event_id="event-1", decision_id="action-1", target=0.5))

    with pytest.raises(ValueError, match="conflicting action decision"):
        adapter.consume(_action(event_id="event-2", decision_id="action-1", target=0.0))


@pytest.mark.parametrize("target", [-0.01, 1.01, float("nan"), float("inf")])
def test_v2_adapter_rejects_invalid_absolute_target(target: float) -> None:
    adapter = AbsoluteActionLifecycleAdapter()

    with pytest.raises(ValueError, match="target_remaining_fraction"):
        adapter.consume(_action(event_id="event-invalid", decision_id="action-invalid", target=target))


def test_v2_adapter_requires_action_cycle_and_decision_identity() -> None:
    adapter = AbsoluteActionLifecycleAdapter()
    event = LifecycleEvent(
        event_id="event-missing",
        event_type=LifecycleEventType.ACTION_DECISION,
        occurred_at=datetime(2026, 1, 5, 15, 0),
        target_remaining_fraction=0.5,
        target_semantics=TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE,
    )

    with pytest.raises(ValueError, match="action_cycle_id"):
        adapter.consume(event)


def test_v2_adapter_accepts_persisted_string_enum_values() -> None:
    adapter = AbsoluteActionLifecycleAdapter()
    event = LifecycleEvent(
        event_id="event-persisted",
        event_type="action_decision",  # type: ignore[arg-type]
        occurred_at=datetime(2026, 1, 5, 15, 0),
        action_cycle_id="cycle-persisted",
        action_decision_id="action-persisted",
        target_remaining_fraction=0.5,
        target_semantics="absolute_exposure_baseline",  # type: ignore[arg-type]
    )

    intent = adapter.consume(event)

    assert intent is not None
    assert intent.target_stage == "remaining_5000bp"


def test_legacy_relative_adapter_is_permanently_diagnostic_only() -> None:
    adapter = LegacyCurrentSemanticsDiagnosticAdapter()

    assert adapter.policy_name == "legacy_current_semantics"
    assert adapter.promotion_eligible is False
    assert adapter.apply_relative_target(current_fraction=1.0, relative_remaining_fraction=0.5) == 0.5
    assert adapter.apply_relative_target(current_fraction=0.5, relative_remaining_fraction=0.5) == 0.25
