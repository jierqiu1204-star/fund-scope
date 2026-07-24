from __future__ import annotations

from datetime import date

import pytest

from app.models.entities import TrackedPosition
from app.schemas.etf_quotes import TrackedEtfIntradaySnapshotOut
from app.schemas.tracked_positions import TrackedPositionExitSignal
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
from app.services.tracked_positions.service import (
    _annotate_exit_signal_email_eligibility,
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
        (ALERT_TRAILING_TAKE_PROFIT, "trim", ACTION_CLASS_ACTIONABLE_EXIT, 0.75),
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


def test_trailing_take_profit_uses_two_stages_and_never_clears_on_trend_alone() -> None:
    first_stage = map_exit_signal_to_position_action(
        alert_type=ALERT_TRAILING_TAKE_PROFIT,
        allow_full_exit=True,
        trend_weakening=True,
        current_remaining_fraction=1.0,
    )
    second_stage = map_exit_signal_to_position_action(
        alert_type=ALERT_TRAILING_TAKE_PROFIT,
        allow_full_exit=True,
        trend_weakening=True,
        current_remaining_fraction=0.75,
    )

    assert first_stage.action == "trim"
    assert first_stage.target_remaining_fraction == 0.75
    assert second_stage.action == "reduce"
    assert second_stage.target_remaining_fraction == 0.5


def _tracked_etf() -> TrackedPosition:
    return TrackedPosition(
        user_id=1,
        asset_type="etf",
        asset_code="510001",
        asset_name="测试ETF",
        buy_date=date(2026, 7, 1),
        buy_amount=1_000,
    )


def test_tracked_position_exit_email_does_not_require_research_rank_membership() -> None:
    signal = TrackedPositionExitSignal(
        alert_type=ALERT_HARD_STOP,
        label="硬止损提醒",
        level="urgent",
        action_class=ACTION_CLASS_ACTIONABLE_EXIT,
    )
    quote = TrackedEtfIntradaySnapshotOut(
        current_price=0.9,
        price_source="intraday_quote",
        reliability_level="fresh_consensus",
        email_eligible=True,
        decision_eligible=True,
        is_stale=False,
    )

    result = _annotate_exit_signal_email_eligibility(_tracked_etf(), signal, quote)

    assert result.email_eligible is True


def test_tracked_position_exit_email_still_fails_closed_on_its_own_quote_gate() -> None:
    signal = TrackedPositionExitSignal(
        alert_type=ALERT_HARD_STOP,
        label="硬止损提醒",
        level="urgent",
        action_class=ACTION_CLASS_ACTIONABLE_EXIT,
    )
    display_only_quote = TrackedEtfIntradaySnapshotOut(
        current_price=0.9,
        price_source="intraday_quote",
        reliability_level="display_only",
        email_eligible=False,
        decision_eligible=False,
        is_stale=True,
    )

    result = _annotate_exit_signal_email_eligibility(
        _tracked_etf(),
        signal,
        display_only_quote,
    )

    assert result.email_eligible is False
    assert "不会用日线兜底或旧行情触发" in str(result.email_eligibility_reason)
