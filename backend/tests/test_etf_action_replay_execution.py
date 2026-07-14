from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.services.strategy_lab.etf_action_replay import (
    DailyExecutionBar,
    ExecutionStatus,
    select_adjusted_open_fill,
)


def _bar(
    session_date: date,
    *,
    raw_open: float | None = 10.0,
    adjustment_factor: float | None = 1.2,
    volume: float | None = 100_000.0,
    **kwargs: object,
) -> DailyExecutionBar:
    return DailyExecutionBar(
        session_date=session_date,
        raw_open=raw_open,
        adjustment_factor=adjustment_factor,
        volume=volume,
        **kwargs,
    )


def test_base_fill_is_first_later_session_adjusted_open() -> None:
    signal_date = date(2026, 1, 5)

    result = select_adjusted_open_fill(
        signal_session=signal_date,
        bars=[
            _bar(signal_date, raw_open=9.8),
            _bar(date(2026, 1, 6), raw_open=10.0, adjustment_factor=1.2),
            _bar(date(2026, 1, 7), raw_open=11.0),
        ],
    )

    assert result.status is ExecutionStatus.FILLED
    assert result.fill is not None
    assert result.fill.session_date == date(2026, 1, 6)
    assert result.fill.raw_open == 10.0
    assert result.fill.adjustment_factor == 1.2
    assert result.fill.adjusted_open == 12.0
    assert result.fill.normalized_execution_price == 12.0
    assert result.fill.signal_to_fill_trading_sessions == 1
    assert result.outcome_start_session == result.fill.session_date


def test_t_plus_one_high_low_close_cannot_influence_fill_selection() -> None:
    base = _bar(
        date(2026, 1, 6),
        raw_open=10.0,
        adjustment_factor=1.2,
        raw_high=10.5,
        raw_low=9.5,
        raw_close=10.2,
    )
    changed_later_fields = replace(
        base,
        raw_high=1_000_000.0,
        raw_low=0.000001,
        raw_close=-999.0,
    )

    first = select_adjusted_open_fill(signal_session=date(2026, 1, 5), bars=[base])
    second = select_adjusted_open_fill(
        signal_session=date(2026, 1, 5), bars=[changed_later_fields]
    )

    assert first == second
    assert first.fill is not None
    assert first.fill.adjusted_open == 12.0


def test_untradable_sessions_defer_until_first_demonstrably_tradable_open() -> None:
    result = select_adjusted_open_fill(
        signal_session=date(2026, 1, 5),
        bars=[
            _bar(date(2026, 1, 6), suspended=True),
            _bar(date(2026, 1, 7), volume=0.0),
            _bar(date(2026, 1, 8), limit_locked=True),
            _bar(date(2026, 1, 9), raw_open=None),
            _bar(date(2026, 1, 12), raw_open=8.0, adjustment_factor=1.25),
        ],
    )

    assert result.status is ExecutionStatus.FILLED
    assert result.fill is not None
    assert result.fill.session_date == date(2026, 1, 12)
    assert result.fill.adjusted_open == 10.0
    assert result.fill.signal_to_fill_trading_sessions == 5
    assert [item.reason for item in result.deferred_sessions] == [
        "suspended",
        "zero_or_missing_volume",
        "limit_lock_without_demonstrable_liquidity",
        "missing_or_invalid_open",
    ]


def test_no_tradable_open_remains_pending_without_stale_close_fallback() -> None:
    result = select_adjusted_open_fill(
        signal_session=date(2026, 1, 5),
        bars=[
            _bar(date(2026, 1, 6), raw_open=None),
            _bar(date(2026, 1, 7), suspended=True),
        ],
    )

    assert result.status is ExecutionStatus.DEFERRED
    assert result.fill is None
    assert result.outcome_start_session is None


def test_delisting_rejects_pending_fill_and_does_not_use_later_row() -> None:
    result = select_adjusted_open_fill(
        signal_session=date(2026, 1, 5),
        bars=[
            _bar(date(2026, 1, 6), suspended=True),
            _bar(date(2026, 1, 7), delisted=True),
            _bar(date(2026, 1, 8), raw_open=10.0),
        ],
    )

    assert result.status is ExecutionStatus.REJECTED
    assert result.fill is None
    assert result.rejection_reason == "delisted_before_fill"
    assert result.outcome_start_session is None


def test_limit_locked_open_fills_only_with_demonstrable_liquidity() -> None:
    result = select_adjusted_open_fill(
        signal_session=date(2026, 1, 5),
        bars=[
            _bar(
                date(2026, 1, 6),
                limit_locked=True,
                demonstrably_tradable=True,
                raw_open=7.5,
                adjustment_factor=2.0,
            )
        ],
    )

    assert result.status is ExecutionStatus.FILLED
    assert result.fill is not None
    assert result.fill.adjusted_open == 15.0
