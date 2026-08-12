from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.models.entities import TrackedPosition
from app.schemas.etf_quotes import TrackedEtfIntradaySnapshotOut
from app.schemas.tracked_positions import TrackedPositionExitSignal
from app.services.intraday_etf.service import quote_decision_eligible_flag
from app.services.risk_alerts import (
    ACTION_CLASS_ACTIONABLE_EXIT,
    ACTION_CLASS_GUARD_ONLY,
    ACTION_CLASS_NONE,
    ACTION_CLASS_SOFT_WATCH,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_MA5_CLOSE_BREAK_EXIT,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    AlertDecision,
    evaluate_exit_execution_evidence,
    map_exit_signal_to_position_action,
)
from app.services.tracked_positions.service import (
    _annotate_exit_signal_email_eligibility,
    _audit_decision_context,
    _decision_email_data_eligible,
)


def test_hard_stop_always_maps_to_absolute_zero_target() -> None:
    decision = map_exit_signal_to_position_action(alert_type=ALERT_HARD_STOP, allow_full_exit=False)

    assert decision.action == "exit"
    assert decision.action_class == ACTION_CLASS_ACTIONABLE_EXIT
    assert decision.target_remaining_fraction == 0.0


def test_ma5_close_break_maps_to_absolute_zero_target() -> None:
    decision = map_exit_signal_to_position_action(
        alert_type=ALERT_MA5_CLOSE_BREAK_EXIT,
        allow_full_exit=False,
    )

    assert decision.action == "exit"
    assert decision.action_class == ACTION_CLASS_ACTIONABLE_EXIT
    assert decision.target_remaining_fraction == 0.0


def test_legacy_quote_without_explicit_eligibility_fails_closed() -> None:
    assert quote_decision_eligible_flag(SimpleNamespace(raw_json={})) is False
    assert (
        quote_decision_eligible_flag(SimpleNamespace(raw_json={"decision_eligible": True})) is True
    )


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
        bid_price=0.899,
        ask_price=0.901,
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


def _hard_stop_decision() -> AlertDecision:
    return AlertDecision(
        alert_type=ALERT_HARD_STOP,
        trigger_label="硬止损提醒",
        reasons=["触及动态硬止损线"],
        risk_flags=[],
        advisor_summary=None,
        signal_item=None,
        advisor_report=None,
    )


def test_daily_etf_email_requires_fresh_explicit_executable_quote() -> None:
    fresh_quote = TrackedEtfIntradaySnapshotOut(
        current_price=0.9,
        price_source="intraday_quote",
        reliability_level="fresh_consensus",
        email_eligible=True,
        decision_eligible=True,
        is_stale=False,
        bid_price=0.899,
        ask_price=0.901,
    )

    assert _decision_email_data_eligible(
        _tracked_etf(),
        _hard_stop_decision(),
        fresh_quote,
        evaluation_mode="daily",
    )
    assert not _decision_email_data_eligible(
        _tracked_etf(),
        _hard_stop_decision(),
        None,
        evaluation_mode="daily",
    )


def test_daily_fund_email_keeps_confirmed_nav_path() -> None:
    position = TrackedPosition(
        user_id=1,
        asset_type="fund",
        asset_code="270042",
        asset_name="测试基金",
        buy_date=date(2026, 7, 1),
        buy_amount=1_000,
    )

    assert _decision_email_data_eligible(
        position,
        _hard_stop_decision(),
        None,
        evaluation_mode="daily",
    )


def test_ma5_close_break_email_uses_adjusted_daily_evidence_without_intraday_fallback() -> None:
    position = _tracked_etf()
    ma5_context = {
        "decision_eligible": True,
        "price_basis": "total_return_adjusted",
        "condition_met": True,
        "should_alert": True,
        "trade_date": "2026-08-10",
    }
    position.exit_state_json = {"exit_signal_threshold_context": {"ma5_close_break": ma5_context}}
    decision = AlertDecision(
        alert_type=ALERT_MA5_CLOSE_BREAK_EXIT,
        trigger_label="收盘跌破五日线退出提醒",
        reasons=["复权收盘价跌破复权 MA5"],
        risk_flags=[],
        advisor_summary=None,
        signal_item=None,
        advisor_report=None,
        alert_source="total_return_adjusted_daily_close",
    )

    assert _decision_email_data_eligible(
        position,
        decision,
        None,
        evaluation_mode="daily",
    )


def test_sell_execution_evidence_uses_bid_and_records_gap() -> None:
    evidence = evaluate_exit_execution_evidence(
        signal_price=0.90,
        bid_price=0.88,
        ask_price=0.89,
        entry_price=1.00,
        hard_stop_pct=-10.0,
        quote_eligible=True,
    )

    assert evidence.status == "observable"
    assert evidence.executable_reference_price == 0.88
    assert evidence.price_basis == "intraday_bid"
    assert evidence.signal_to_executable_gap_bps == pytest.approx(-222.2222, abs=0.001)
    assert evidence.gap_through_stop_bps == pytest.approx(-222.2222, abs=0.001)
    assert evidence.slippage_reserve_bps == 5.0


def test_alert_audit_context_keeps_execution_non_automatic() -> None:
    position = _tracked_etf()
    position.entry_price = 1.0
    position.exit_state_json = {"dynamic_thresholds": {"hard_stop_pct": -10.0}}
    quote = TrackedEtfIntradaySnapshotOut(
        current_price=0.90,
        price_source="intraday_quote",
        reliability_level="fresh_consensus",
        email_eligible=True,
        decision_eligible=True,
        is_stale=False,
        bid_price=0.88,
        ask_price=0.89,
    )

    context = _audit_decision_context(
        position,
        _hard_stop_decision(),
        quote,
        evaluation_mode="daily",
        outcome="sent",
    )

    assert context["email_eligible"] is True
    assert context["execution_risk"]["executable_reference_price"] == 0.88
    assert context["execution_risk"]["automatic_execution"] is False
    assert context["execution_risk"]["execution_provenance"] == "none"


@pytest.mark.parametrize(
    ("bid_price", "ask_price", "reason_code"),
    [
        (None, 0.91, "missing_executable_bid_ask"),
        (0.92, 0.91, "crossed_or_invalid_bid_ask"),
        (float("nan"), 0.91, "missing_executable_bid_ask"),
    ],
)
def test_sell_execution_evidence_fails_closed(
    bid_price: float | None,
    ask_price: float | None,
    reason_code: str,
) -> None:
    evidence = evaluate_exit_execution_evidence(
        signal_price=0.90,
        bid_price=bid_price,
        ask_price=ask_price,
        entry_price=1.00,
        hard_stop_pct=-10.0,
        quote_eligible=True,
    )

    assert evidence.status == "not_observable"
    assert evidence.executable_reference_price is None
    assert evidence.reason_code == reason_code
