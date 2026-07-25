from __future__ import annotations

from datetime import date, timedelta

from app.services.short_research.history_readiness import (
    build_etf_history_readiness,
    derived_replay_depth_sessions,
    history_depth_scope,
)


def _sessions(count: int) -> tuple[date, ...]:
    start = date(2025, 1, 2)
    return tuple(start + timedelta(days=index) for index in range(count))


def test_current_day_data_leaves_freshness_gap_but_stays_in_warmup_lane() -> None:
    sessions = _sessions(300)
    readiness = build_etf_history_readiness(
        decision_eligible_codes=("510001",),
        trading_sessions=sessions,
        eligible_dates_by_code={"510001": frozenset(sessions[-30:])},
        contract_hash="a" * 64,
        horizons=(1, 3, 5, 10),
        attempted_codes=("510001",),
    )

    assert readiness.daily_freshness.covered_count == 1
    assert readiness.daily_freshness.eligible_count == 1
    assert readiness.history_depth_61.covered_count == 0
    assert readiness.history_depth_61.pending_codes == ("510001",)
    assert readiness.contract_depth.required_sessions == 300
    assert readiness.contract_depth.covered_count == 0
    assert readiness.telemetry_depth_180.authoritative is False
    assert readiness.telemetry_depth_500.required_sessions == 500
    assert readiness.telemetry_depth_500.authoritative is False
    assert readiness.score_eligible_codes == ()


def test_replay_depth_is_derived_from_warmup_horizon_and_independent_dates() -> None:
    assert derived_replay_depth_sessions(horizons=(5,)) == 200
    assert derived_replay_depth_sessions(horizons=(1, 3, 5, 10)) == 300
    assert derived_replay_depth_sessions(horizons=(1,)) == 120


def test_scope_and_denominators_are_independent_and_universe_exact() -> None:
    sessions = _sessions(300)
    first_hash = "b" * 64
    second_hash = "c" * 64
    readiness = build_etf_history_readiness(
        decision_eligible_codes=("510001", "510002"),
        trading_sessions=sessions,
        eligible_dates_by_code={
            "510001": frozenset(sessions),
            "510002": frozenset(sessions[-60:]),
            "599999": frozenset(sessions),
        },
        contract_hash=first_hash,
        horizons=(5,),
        attempted_codes=("510001",),
    )

    assert history_depth_scope(first_hash) == f"history_depth_required:{first_hash}"
    assert history_depth_scope(first_hash) != history_depth_scope(second_hash)
    assert readiness.daily_freshness.expected_count == 2
    assert readiness.daily_freshness.attempted_count == 1
    assert readiness.daily_freshness.covered_count == 2
    assert readiness.history_depth_61.covered_count == 1
    assert readiness.history_depth_61.excluded_count == 1
    assert readiness.contract_depth.scope == history_depth_scope(first_hash)
    assert readiness.contract_depth.expected_count == 2
    assert readiness.contract_depth.covered_count == 1
    assert readiness.score_eligible_codes == ("510001",)
