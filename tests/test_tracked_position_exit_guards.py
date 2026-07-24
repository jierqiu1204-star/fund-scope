from datetime import date, timedelta

from app.schemas.tracked_positions import TrackedPositionExitSignal
from app.services.risk_alerts import (
    ACTION_CLASS_GUARD_ONLY,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_EXIT_WATCH,
    ALERT_HARD_STOP,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_TREND_WEAKENING,
    BUCKET_THRESHOLD_SOURCE_APPROVED,
    BUCKET_THRESHOLD_SOURCE_INSUFFICIENT,
    POSITION_ACTION_EXIT,
    POSITION_ACTION_NO_ADD,
    POSITION_ACTION_REDUCE,
    POSITION_ACTION_REENTRY_CANDIDATE,
    REENTRY_STATE_BLOCKED,
    REENTRY_STATE_CANDIDATE,
    REENTRY_STATE_WAITING_COOLDOWN,
    build_bucket_threshold_context,
    calculate_position_sizing,
    evaluate_reentry_state,
)
from app.services.tracked_positions.service import PositionAnalysis, merge_exit_state


def test_unconfirmed_trend_weakening_is_guard_only_for_sizing() -> None:
    sizing = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_TREND_WEAKENING,
        current_market_value=5000,
        current_price=1.0,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )

    assert sizing.action == POSITION_ACTION_NO_ADD
    assert sizing.action_class == ACTION_CLASS_GUARD_ONLY
    assert "暂停加仓" in sizing.label


def test_confirmed_trend_weakening_can_reduce_position() -> None:
    sizing = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_CONFIRMED_TREND_WEAKENING,
        current_market_value=5000,
        current_price=1.0,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )

    assert sizing.action == POSITION_ACTION_REDUCE
    assert sizing.recommended_trade_amount is not None


def test_hard_stop_exit_watch_trailing_and_take_profit_have_explicit_actions() -> None:
    hard_stop = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_HARD_STOP,
        current_market_value=3000,
        current_price=1.5,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )
    exit_watch = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_EXIT_WATCH,
        current_market_value=3000,
        current_price=1.5,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )
    trailing = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_TRAILING_TAKE_PROFIT,
        current_market_value=3000,
        current_price=1.5,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )
    watch = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_TAKE_PROFIT_WATCH,
        current_market_value=3000,
        current_price=1.5,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )

    assert hard_stop.action == POSITION_ACTION_EXIT
    assert exit_watch.action == POSITION_ACTION_EXIT
    assert trailing.action == "trim"
    assert watch.action == "hold"
    assert trailing.recommended_trade_amount == 750
    assert watch.recommended_trade_amount is None


def test_reentry_candidate_requires_cooldown_rank_entry_timing_and_reliable_data() -> None:
    today = date(2026, 7, 6)

    waiting = evaluate_reentry_state(
        last_action=POSITION_ACTION_REDUCE,
        last_action_date=today - timedelta(days=1),
        today=today,
        ranking_bucket="top10",
        entry_timing_label="健康回踩",
        theme_trend="strong",
        data_reliability="verified",
    )
    weak_rank = evaluate_reentry_state(
        last_action=POSITION_ACTION_REDUCE,
        last_action_date=today - timedelta(days=4),
        today=today,
        ranking_bucket="top50",
        entry_timing_label="健康回踩",
        theme_trend="strong",
        data_reliability="verified",
    )
    stale_data = evaluate_reentry_state(
        last_action=POSITION_ACTION_REDUCE,
        last_action_date=today - timedelta(days=4),
        today=today,
        ranking_bucket="top10",
        entry_timing_label="健康回踩",
        theme_trend="strong",
        data_reliability="stale",
    )
    candidate = evaluate_reentry_state(
        last_action=POSITION_ACTION_REDUCE,
        last_action_date=today - timedelta(days=4),
        today=today,
        ranking_bucket="top10",
        entry_timing_label="健康回踩",
        theme_trend="strong",
        data_reliability="verified",
    )

    assert waiting.state == REENTRY_STATE_WAITING_COOLDOWN
    assert weak_rank.state == REENTRY_STATE_BLOCKED
    assert stale_data.state == REENTRY_STATE_BLOCKED
    assert candidate.state == REENTRY_STATE_CANDIDATE
    assert candidate.action == POSITION_ACTION_REENTRY_CANDIDATE


def test_bucket_threshold_context_uses_approved_params_only_with_enough_evidence() -> None:
    insufficient = build_bucket_threshold_context(
        asset_bucket="theme",
        theme_group="semiconductor",
        volatility_pct=4.2,
        current_profit_pct=6.0,
        data_reliability="verified",
        sample_count=12,
        intraday_coverage=0.5,
        approved_params={
            "hard_stop_pct": -3.2,
            "profit_start_pct": 3.0,
            "trailing_giveback_pct": 1.8,
            "take_profit_watch_pct": 2.6,
            "trend_confirm_days": 2,
        },
    )
    approved = build_bucket_threshold_context(
        asset_bucket="defensive",
        theme_group="bond",
        volatility_pct=0.8,
        current_profit_pct=1.0,
        data_reliability="verified",
        sample_count=45,
        intraday_coverage=0.8,
        approved_params={
            "hard_stop_pct": -2.0,
            "profit_start_pct": 2.5,
            "trailing_giveback_pct": 1.2,
            "take_profit_watch_pct": 2.0,
            "trend_confirm_days": 3,
        },
    )

    assert insufficient.threshold_source == BUCKET_THRESHOLD_SOURCE_INSUFFICIENT
    assert insufficient.volatility_bucket == "high"
    assert approved.threshold_source == BUCKET_THRESHOLD_SOURCE_APPROVED
    assert approved.hard_stop_pct == -2.0
    assert approved.volatility_bucket == "low"


def test_merge_exit_state_persists_guard_and_threshold_context() -> None:
    class PositionStub:
        exit_state_json = {}

    position = PositionStub()
    signal = TrackedPositionExitSignal(
        alert_type=ALERT_TREND_WEAKENING,
        label="趋势警戒（仅网页）",
        level="watch",
        action_class=ACTION_CLASS_GUARD_ONLY,
        guard_state="trend_weakening_unconfirmed",
        guard_reasons=["趋势转弱未确认"],
        no_alert_reason="只做风险警戒",
        threshold_context={"current_pnl_pct": 1.2, "confirmed_trend_weakening": False},
    )
    analysis = PositionAnalysis(
        chart=[],
        exit_signal=signal,
        max_profit_pct=5.0,
        profit_giveback_pct=3.8,
        holding_days=12,
        technical_metrics={},
    )

    merge_exit_state(position, analysis)  # type: ignore[arg-type]

    assert position.exit_state_json["max_profit_pct"] == 5.0
    assert position.exit_state_json["profit_giveback_pct"] == 3.8
    assert position.exit_state_json["action_class"] == ACTION_CLASS_GUARD_ONLY
    assert position.exit_state_json["guard_state"] == "trend_weakening_unconfirmed"
    assert position.exit_state_json["exit_signal_threshold_context"]["current_pnl_pct"] == 1.2
