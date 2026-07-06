from app.services.risk_alerts import (
    ACTION_CLASS_GUARD_ONLY,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_TREND_WEAKENING,
    POSITION_ACTION_HOLD,
    POSITION_ACTION_REDUCE,
    calculate_position_sizing,
)
from app.services.tracked_positions.service import PositionAnalysis, merge_exit_state
from app.schemas.tracked_positions import TrackedPositionExitSignal


def test_unconfirmed_trend_weakening_is_guard_only_for_sizing() -> None:
    sizing = calculate_position_sizing(
        asset_type="etf",
        alert_type=ALERT_TREND_WEAKENING,
        current_market_value=5000,
        current_price=1.0,
        etf_trading_capital=10000,
        allow_full_exit=True,
    )

    assert sizing.action == POSITION_ACTION_HOLD
    assert "风险警戒" in (sizing.reason or "")


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
