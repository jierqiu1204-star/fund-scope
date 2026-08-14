"""Causal contract tests for the late-day T+1 tracked-position exit policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.tracked_positions.late_day_t1_policy import (
    AWAITING_T1,
    HARD_STOP,
    MA5_FAILURE,
    MORNING_GIVEBACK,
    QUOTE_UNAVAILABLE,
    THRESHOLD_NOT_TRIGGERED,
    TIMED_EXIT,
    LateDayT1Input,
    MorningQuote,
    evaluate_late_day_t1,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
ENTRY_DATE = date(2026, 8, 13)
T1_DATE = date(2026, 8, 14)


def _at(hour: int, minute: int, *, day: date = T1_DATE) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI)


def _input(**changes: object) -> LateDayT1Input:
    evaluation_at = _at(9, 45)
    value = LateDayT1Input(
        entry_date=ENTRY_DATE,
        t1_date=T1_DATE,
        entry_price=10.0,
        evaluation_at=evaluation_at,
        current_quote_time=evaluation_at,
        current_price=10.0,
        current_quote_eligible=True,
        morning_quotes=(MorningQuote(observed_at=evaluation_at, price=10.0),),
        hard_stop_pct=-4.0,
        volatility_unit_pct=1.5,
    )
    return replace(value, **changes)


def test_signal_day_never_emits_exit() -> None:
    evaluation = _at(14, 50, day=ENTRY_DATE)

    result = evaluate_late_day_t1(
        _input(
            evaluation_at=evaluation,
            current_quote_time=evaluation,
            current_price=8.0,
        )
    )

    assert result.actionable is False
    assert result.reason_code == AWAITING_T1


def test_hard_stop_has_highest_trigger_priority() -> None:
    result = evaluate_late_day_t1(
        _input(
            current_price=9.5,
            morning_quotes=(
                MorningQuote(observed_at=_at(9, 31), price=10.2),
                MorningQuote(observed_at=_at(9, 45), price=9.5),
            ),
        )
    )

    assert result.actionable is True
    assert result.reason_code == HARD_STOP
    assert result.alert_type == "late_day_t1_exit"


def test_morning_high_giveback_protects_profit() -> None:
    result = evaluate_late_day_t1(
        _input(
            current_price=10.05,
            morning_quotes=(
                MorningQuote(observed_at=_at(9, 31), price=10.20),
                MorningQuote(observed_at=_at(9, 45), price=10.05),
            ),
        )
    )

    assert result.actionable is True
    assert result.reason_code == MORNING_GIVEBACK


def _ma5_cross_quotes() -> tuple[MorningQuote, ...]:
    closes = (10.00, 10.01, 10.02, 10.03, 10.04, 9.99)
    session_open = _at(9, 30)
    return tuple(
        MorningQuote(
            observed_at=session_open + timedelta(minutes=9 + 10 * index),
            price=price,
        )
        for index, price in enumerate(closes)
    )


def test_closed_ten_minute_ma5_failure_exits_before_time_limit() -> None:
    evaluation = _at(10, 30)
    result = evaluate_late_day_t1(
        _input(
            evaluation_at=evaluation,
            current_quote_time=evaluation,
            current_price=9.99,
            morning_quotes=_ma5_cross_quotes(),
            hard_stop_pct=-10.0,
            volatility_unit_pct=3.0,
        )
    )

    assert result.actionable is True
    assert result.reason_code == MA5_FAILURE
    assert result.threshold_context["closed_10m_bar_count"] == 6


def test_no_trigger_before_deadline_remains_observation() -> None:
    result = evaluate_late_day_t1(_input())

    assert result.actionable is False
    assert result.reason_code == THRESHOLD_NOT_TRIGGERED
    assert result.data_eligible is True


def test_ten_thirty_deadline_forces_full_exit() -> None:
    evaluation = _at(10, 30)
    result = evaluate_late_day_t1(
        _input(
            evaluation_at=evaluation,
            current_quote_time=evaluation,
            current_price=10.0,
            morning_quotes=(MorningQuote(observed_at=evaluation, price=10.0),),
        )
    )

    assert result.actionable is True
    assert result.reason_code == TIMED_EXIT


def test_stale_or_future_quote_fails_closed() -> None:
    evaluation = _at(9, 45)
    result = evaluate_late_day_t1(
        _input(
            evaluation_at=evaluation,
            current_quote_time=evaluation + timedelta(minutes=1),
        )
    )

    assert result.actionable is False
    assert result.reason_code == QUOTE_UNAVAILABLE
    assert result.data_eligible is False


def test_future_morning_quotes_do_not_change_current_decision() -> None:
    evaluation = _at(9, 45)
    future = MorningQuote(observed_at=_at(10, 20), price=12.0)
    baseline = evaluate_late_day_t1(_input())
    with_future = evaluate_late_day_t1(
        _input(
            evaluation_at=evaluation,
            morning_quotes=(*_input().morning_quotes, future),
        )
    )

    assert with_future.reason_code == baseline.reason_code
    assert with_future.threshold_context["morning_high_price"] == baseline.threshold_context[
        "morning_high_price"
    ]
