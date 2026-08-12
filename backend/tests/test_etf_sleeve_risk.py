from __future__ import annotations

from datetime import date

from app.services.risk_alerts import (
    ETF_RISK_STATE_DATA_HALT,
    ETF_RISK_STATE_NORMAL,
    ETF_RISK_STATE_REDUCE_ONLY,
    EtfSleevePositionEvidence,
    assess_etf_liquidity_capacity,
    calculate_position_sizing,
    calculate_sleeve_drawdown,
    calculate_tracked_etf_sleeve_nav,
    evaluate_etf_owner_risk_state,
)


def test_sleeve_nav_uses_weighted_equity_instead_of_average_position_return() -> None:
    result = calculate_tracked_etf_sleeve_nav(
        configured_capital=100_000,
        capital_confirmed=True,
        opening_reconciled=True,
        cash_balance=0,
        realized_pnl=0,
        positions=(
            EtfSleevePositionEvidence(
                position_id=1,
                remaining_cost_basis=90_000,
                market_value=81_000,
                quantity=9_000,
                decision_eligible=True,
                execution_complete=True,
            ),
            EtfSleevePositionEvidence(
                position_id=2,
                remaining_cost_basis=10_000,
                market_value=11_000,
                quantity=1_000,
                decision_eligible=True,
                execution_complete=True,
            ),
        ),
    )

    assert result.status == "ready"
    assert result.equity == 92_000
    assert result.unrealized_pnl == -8_000
    assert calculate_sleeve_drawdown(result.equity, (100_000, 98_000)) == -0.08


def test_sleeve_nav_fails_closed_without_confirmed_capital_or_complete_marks() -> None:
    unconfirmed = calculate_tracked_etf_sleeve_nav(
        configured_capital=10_000,
        capital_confirmed=False,
        opening_reconciled=False,
        cash_balance=7_000,
        realized_pnl=0,
        positions=(),
    )
    incomplete = calculate_tracked_etf_sleeve_nav(
        configured_capital=10_000,
        capital_confirmed=True,
        opening_reconciled=True,
        cash_balance=7_000,
        realized_pnl=0,
        positions=(
            EtfSleevePositionEvidence(
                position_id=1,
                remaining_cost_basis=3_000,
                market_value=None,
                quantity=300,
                decision_eligible=False,
                execution_complete=True,
            ),
        ),
    )

    assert unconfirmed.status == "unavailable"
    assert unconfirmed.equity is None
    assert "capital_not_explicitly_confirmed" in unconfirmed.unavailable_reasons
    assert "holdings_reconciliation_missing" in unconfirmed.unavailable_reasons
    assert incomplete.status == "unavailable"
    assert incomplete.equity is None
    assert incomplete.valuation_coverage == 0.0
    assert "position_mark_ineligible:1" in incomplete.unavailable_reasons


def test_sleeve_drawdown_requires_prior_comparable_equity() -> None:
    assert calculate_sleeve_drawdown(100_000, ()) is None
    assert calculate_sleeve_drawdown(float("nan"), (100_000,)) is None


def test_owner_risk_state_degrades_immediately_and_recovers_on_distinct_sessions() -> None:
    triggered = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 10),
        nav_status="ready",
        sleeve_drawdown=-0.06,
        distinct_stop_signal_cycles=0,
        confirmed_stop_execution_cycles=0,
    )
    first_recovery = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 11),
        nav_status="ready",
        sleeve_drawdown=-0.02,
        distinct_stop_signal_cycles=0,
        confirmed_stop_execution_cycles=0,
        previous_state=triggered.state,
        previous_evaluated_session=triggered.evaluated_session,
        previous_recovery_sessions=triggered.recovery_sessions,
        cooldown_sessions_remaining=0,
    )
    same_day = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 11),
        nav_status="ready",
        sleeve_drawdown=-0.02,
        distinct_stop_signal_cycles=0,
        confirmed_stop_execution_cycles=0,
        previous_state=first_recovery.state,
        previous_evaluated_session=first_recovery.evaluated_session,
        previous_recovery_sessions=first_recovery.recovery_sessions,
        cooldown_sessions_remaining=first_recovery.cooldown_sessions_remaining,
    )
    recovered = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 12),
        nav_status="ready",
        sleeve_drawdown=-0.02,
        distinct_stop_signal_cycles=0,
        confirmed_stop_execution_cycles=0,
        previous_state=same_day.state,
        previous_evaluated_session=same_day.evaluated_session,
        previous_recovery_sessions=same_day.recovery_sessions,
        cooldown_sessions_remaining=same_day.cooldown_sessions_remaining,
    )

    assert triggered.state == ETF_RISK_STATE_REDUCE_ONLY
    assert first_recovery.state == ETF_RISK_STATE_REDUCE_ONLY
    assert same_day.recovery_sessions == first_recovery.recovery_sessions
    assert recovered.state == ETF_RISK_STATE_NORMAL


def test_owner_risk_state_data_halt_never_claims_normal_without_nav() -> None:
    result = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 11),
        nav_status="unavailable",
        sleeve_drawdown=None,
        distinct_stop_signal_cycles=0,
        confirmed_stop_execution_cycles=0,
    )

    assert result.state == ETF_RISK_STATE_DATA_HALT
    assert "sleeve_nav_unavailable" in result.reason_codes


def test_unchanged_rolling_stop_cycles_do_not_retrigger_recovery_forever() -> None:
    triggered = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 10),
        nav_status="ready",
        sleeve_drawdown=-0.01,
        distinct_stop_signal_cycles=2,
        confirmed_stop_execution_cycles=0,
    )
    first_recovery = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 11),
        nav_status="ready",
        sleeve_drawdown=-0.01,
        distinct_stop_signal_cycles=2,
        confirmed_stop_execution_cycles=0,
        previous_state=triggered.state,
        previous_evaluated_session=triggered.evaluated_session,
        previous_signal_stop_cycles=2,
    )
    recovered = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 12),
        nav_status="ready",
        sleeve_drawdown=-0.01,
        distinct_stop_signal_cycles=2,
        confirmed_stop_execution_cycles=0,
        previous_state=first_recovery.state,
        previous_evaluated_session=first_recovery.evaluated_session,
        previous_recovery_sessions=first_recovery.recovery_sessions,
        previous_signal_stop_cycles=2,
    )
    new_cycle = evaluate_etf_owner_risk_state(
        trade_session=date(2026, 8, 13),
        nav_status="ready",
        sleeve_drawdown=-0.01,
        distinct_stop_signal_cycles=3,
        confirmed_stop_execution_cycles=0,
        previous_state=recovered.state,
        previous_evaluated_session=recovered.evaluated_session,
        previous_signal_stop_cycles=2,
    )

    assert triggered.state == ETF_RISK_STATE_REDUCE_ONLY
    assert first_recovery.reason_codes == ("risk_recovery_pending",)
    assert recovered.state == ETF_RISK_STATE_NORMAL
    assert new_cycle.state == ETF_RISK_STATE_REDUCE_ONLY
    assert new_cycle.reason_codes == ("repeated_distinct_stop_signals",)


def test_liquidity_capacity_blocks_entry_but_preserves_exit_assessment() -> None:
    turnovers = tuple(float(1_000_000 + index * 10_000) for index in range(20))
    buy = assess_etf_liquidity_capacity(
        side="buy",
        trade_amount=50_000,
        daily_turnovers=turnovers,
        quote_eligible=True,
        bid_price=1.0,
        ask_price=1.01,
        premium_discount_pct=0.1,
    )
    sell = assess_etf_liquidity_capacity(
        side="sell",
        trade_amount=50_000,
        daily_turnovers=turnovers,
        quote_eligible=True,
        bid_price=1.0,
        ask_price=1.01,
        premium_discount_pct=0.1,
        limit_state="limit_down",
    )

    assert buy.status == "blocked"
    assert buy.entry_allowed is False
    assert "entry_participation_exceeds_limit" in buy.reason_codes
    assert sell.status == "stressed"
    assert sell.entry_allowed is False
    assert sell.normal_liquidation_days is not None
    assert "sell_limit_down" in sell.reason_codes


def test_liquidity_capacity_is_unavailable_without_adv_or_executable_quote() -> None:
    result = assess_etf_liquidity_capacity(
        side="buy",
        trade_amount=3_000,
        daily_turnovers=(),
        quote_eligible=False,
        bid_price=None,
        ask_price=None,
        premium_discount_pct=None,
    )

    assert result.status == "unavailable"
    assert result.entry_allowed is False
    assert "turnover_history_insufficient" in result.reason_codes
    assert "quote_not_decision_eligible" in result.reason_codes


def test_unconfirmed_capital_blocks_add_but_never_hides_exit_action() -> None:
    add = calculate_position_sizing(
        asset_type="etf",
        alert_type=None,
        current_market_value=1_000,
        current_price=1.0,
        etf_trading_capital=10_000,
        capital_confirmed=False,
        allow_full_exit=True,
        target_portfolio_weight=0.2,
        entry_timing_label="健康回踩",
    )
    exit_action = calculate_position_sizing(
        asset_type="etf",
        alert_type="hard_stop",
        current_market_value=1_000,
        current_price=1.0,
        etf_trading_capital=10_000,
        capital_confirmed=False,
        allow_full_exit=True,
        exposure_baseline_quantity=1_000,
    )

    assert add.action == "no_add"
    assert add.recommended_trade_amount is None
    assert exit_action.action == "exit"
    assert exit_action.recommended_trade_amount is None
