from __future__ import annotations

import pytest

from app.services.risk_alerts import (
    ACTION_CLASS_ACTIONABLE_EXIT,
    ACTION_CLASS_GUARD_ONLY,
    ACTION_CLASS_NONE,
    ACTION_CLASS_SOFT_WATCH,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    map_exit_signal_to_position_action,
)


def test_hard_stop_always_maps_to_absolute_zero_target() -> None:
    decision = map_exit_signal_to_position_action(alert_type=ALERT_HARD_STOP, allow_full_exit=False)

    assert decision.action == "exit"
    assert decision.action_class == ACTION_CLASS_ACTIONABLE_EXIT
    assert decision.target_remaining_fraction == 0.0


@pytest.mark.parametrize(
    ("allow_full_exit", "expected_action", "expected_target"),
    [(True, "exit", 0.0), (False, "reduce", 0.5)],
)
def test_eligible_exit_watch_uses_versioned_absolute_target(
    allow_full_exit: bool,
    expected_action: str,
    expected_target: float,
) -> None:
    decision = map_exit_signal_to_position_action(
        alert_type=ALERT_EXIT_WATCH,
        allow_full_exit=allow_full_exit,
        evidence_eligible=True,
    )

    assert decision.action == expected_action
    assert decision.target_remaining_fraction == expected_target


def test_ineligible_exit_watch_produces_no_action() -> None:
    decision = map_exit_signal_to_position_action(
        alert_type=ALERT_EXIT_WATCH,
        allow_full_exit=True,
        evidence_eligible=False,
    )

    assert decision.action == "hold"
    assert decision.action_class == ACTION_CLASS_NONE
    assert decision.target_remaining_fraction is None


@pytest.mark.parametrize(
    ("alert_type", "expected_action", "expected_class", "expected_target"),
    [
        (ALERT_TRAILING_TAKE_PROFIT, "reduce", ACTION_CLASS_ACTIONABLE_EXIT, 0.5),
        (ALERT_CONFIRMED_TREND_WEAKENING, "reduce", ACTION_CLASS_ACTIONABLE_EXIT, 0.5),
        (ALERT_TREND_WEAKENING, "no_add", ACTION_CLASS_GUARD_ONLY, 1.0),
        (ALERT_TAKE_PROFIT_WATCH, "hold", ACTION_CLASS_SOFT_WATCH, 1.0),
        (None, "hold", ACTION_CLASS_NONE, 1.0),
    ],
)
def test_frozen_rule_mapping(
    alert_type: str | None,
    expected_action: str,
    expected_class: str,
    expected_target: float,
) -> None:
    decision = map_exit_signal_to_position_action(alert_type=alert_type, allow_full_exit=True)

    assert decision.action == expected_action
    assert decision.action_class == expected_class
    assert decision.target_remaining_fraction == expected_target
