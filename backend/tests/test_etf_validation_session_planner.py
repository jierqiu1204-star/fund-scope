from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_validation_session_planner import (
    expand_validation_source_search,
    theoretical_session_span,
)


def _sessions(count: int) -> tuple[date, ...]:
    values: list[date] = []
    cursor = date(2025, 1, 2)
    holidays = {date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31)}
    while len(values) < count:
        if cursor.weekday() < 5 and cursor not in holidays:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [(1, 60), (3, 100), (5, 140), (10, 240)],
)
def test_theoretical_span_covers_twenty_non_overlapping_t_plus_one_outcomes(
    horizon: int,
    expected: int,
) -> None:
    assert theoretical_session_span(horizon_sessions=horizon, required_dates=20) == expected


@pytest.mark.asyncio
async def test_planner_uses_exchange_session_indexes_across_holidays() -> None:
    sessions = _sessions(260)

    async def load(start: date, end: date) -> tuple[date, ...]:
        return tuple(session for session in sessions if start <= session <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=10,
        required_dates=20,
        retention_cap_sessions=260,
    )

    assert plan.status == "ready"
    assert plan.theoretical_session_span == 240
    assert plan.searched_session_span == 240
    assert plan.completed_count == 229
    assert plan.non_overlapping_count >= 20
    assert len(plan.selected_source_dates) == 20
    assert plan.pending_count == 11
    assert plan.overlapping_count > 0
    assert plan.source_date_shortfall == 0
    assert all(day in sessions for day in plan.selected_source_dates)


@pytest.mark.asyncio
async def test_sparse_sources_expand_beyond_theoretical_span_until_ready() -> None:
    sessions = _sessions(320)
    sparse_dates = set(sessions[::4])
    calls: list[tuple[date, date]] = []

    async def load(start: date, end: date) -> tuple[date, ...]:
        calls.append((start, end))
        return tuple(day for day in sparse_dates if start <= day <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=5,
        required_dates=20,
        retention_cap_sessions=300,
        expansion_page_sessions=28,
    )

    assert plan.status == "ready"
    assert plan.theoretical_session_span == 140
    assert 140 < plan.searched_session_span <= 300
    assert plan.completed_count >= 20
    assert plan.non_overlapping_count >= 20
    assert len(plan.selected_source_dates) == 20
    assert plan.source_date_shortfall == 0
    assert plan.query_count == len(calls) > 1
    assert all(calls[index][1] < calls[index - 1][0] for index in range(1, len(calls)))


@pytest.mark.asyncio
async def test_planner_reports_exact_shortfall_at_retention_cap() -> None:
    sessions = _sessions(300)
    sparse_dates = set(sessions[::30])

    async def load(start: date, end: date) -> tuple[date, ...]:
        return tuple(day for day in sparse_dates if start <= day <= end)

    plan = await expand_validation_source_search(
        trading_sessions=sessions,
        load_compatible_source_dates=load,
        horizon_sessions=10,
        required_dates=20,
        retention_cap_sessions=240,
    )

    assert plan.status == "insufficient"
    assert plan.searched_session_span == 240
    assert plan.non_overlapping_count < 20
    assert plan.source_date_shortfall == 20 - plan.non_overlapping_count
    assert plan.reason == "sparse_compatible_sources_at_retention_cap"
