from __future__ import annotations

from datetime import date, datetime

import pytest

from app.services.strategy_lab.etf_action_replay import (
    AbsoluteActionLifecycleAdapter,
    LifecycleEvent,
    LifecycleEventType,
    PointInTimeDataGapError,
    PointInTimeMembership,
    PointInTimeObservation,
    TargetSemantics,
    point_in_time_cross_section,
    truncate_observations_at_cutoff,
)

T = date(2022, 6, 30)


def _membership(
    code: str,
    *,
    known_at: date = date(2022, 1, 1),
    eligible_from: date = date(2020, 1, 1),
    eligible_through: date | None = None,
    eligible: bool = True,
) -> PointInTimeMembership:
    return PointInTimeMembership(
        asset_code=code,
        known_at=known_at,
        eligible_from=eligible_from,
        eligible_through=eligible_through,
        eligible=eligible,
    )


def _observation(code: str, session_date: date, score: float) -> PointInTimeObservation:
    return PointInTimeObservation(
        asset_code=code,
        session_date=session_date,
        values={"score": score},
    )


def test_point_in_time_universe_ignores_future_and_current_membership_rows() -> None:
    observations = [
        _observation("510001", T, 90.0),
        _observation("510002", T, 80.0),
        _observation("510001", date(2022, 7, 1), -999.0),
        _observation("999999", date(2026, 1, 1), 1000.0),
    ]
    historical_memberships = [_membership("510001"), _membership("510002")]
    with_current_rows = [
        *historical_memberships,
        _membership(
            "510001",
            known_at=date(2026, 1, 1),
            eligible_from=date(2020, 1, 1),
            eligible=False,
        ),
        _membership(
            "999999",
            known_at=date(2026, 1, 1),
            eligible_from=date(2025, 1, 1),
        ),
    ]

    truncated = point_in_time_cross_section(
        observations=observations,
        memberships=historical_memberships,
        session_date=T,
    )
    full = point_in_time_cross_section(
        observations=observations,
        memberships=with_current_rows,
        session_date=T,
    )

    assert full == truncated
    assert [item.asset_code for item in full] == ["510001", "510002"]
    assert [item.values["score"] for item in full] == [90.0, 80.0]


def test_membership_gap_fails_closed_instead_of_using_current_list() -> None:
    observations = [_observation("510001", T, 90.0)]
    only_current_membership = [
        _membership(
            "510001",
            known_at=date(2026, 1, 1),
            eligible_from=date(2020, 1, 1),
        )
    ]

    with pytest.raises(PointInTimeDataGapError, match="510001"):
        point_in_time_cross_section(
            observations=observations,
            memberships=only_current_membership,
            session_date=T,
        )


def test_overlapping_membership_rows_fail_closed() -> None:
    observations = [_observation("510001", T, 90.0)]

    with pytest.raises(PointInTimeDataGapError, match="ambiguous"):
        point_in_time_cross_section(
            observations=observations,
            memberships=[_membership("510001"), _membership("510001")],
            session_date=T,
        )


def test_cutoff_truncation_excludes_future_rows() -> None:
    rows = [
        _observation("510001", T, 90.0),
        _observation("510001", date(2022, 7, 1), -999.0),
    ]

    assert truncate_observations_at_cutoff(rows, cutoff=T) == (rows[0],)


def test_future_rows_do_not_change_rank_alert_or_action_at_t() -> None:
    memberships = [_membership("510001"), _membership("510002")]
    rows_at_t = [
        _observation("510001", T, 90.0),
        _observation("510002", T, 80.0),
    ]
    rows_with_future = [
        *rows_at_t,
        _observation("510001", date(2022, 7, 1), 1.0),
        _observation("510002", date(2022, 7, 1), 999.0),
    ]

    def evaluate(rows: list[PointInTimeObservation]) -> tuple[str, bool, float]:
        cross_section = point_in_time_cross_section(
            observations=rows,
            memberships=memberships,
            session_date=T,
        )
        ranked = sorted(
            cross_section,
            key=lambda item: (-float(item.values["score"]), item.asset_code),
        )
        leader = ranked[0]
        alert_firing = float(leader.values["score"]) >= 85.0
        adapter = AbsoluteActionLifecycleAdapter()
        intent = adapter.consume(
            LifecycleEvent(
                event_id="event-at-t",
                event_type=LifecycleEventType.ACTION_DECISION,
                occurred_at=datetime(2022, 6, 30, 15, 0),
                action_cycle_id="cycle-at-t",
                action_decision_id="action-at-t",
                target_remaining_fraction=0.5 if alert_firing else 1.0,
                target_semantics=TargetSemantics.ABSOLUTE_EXPOSURE_BASELINE,
            )
        )
        assert intent is not None
        return leader.asset_code, alert_firing, intent.target_remaining_fraction

    assert evaluate(rows_at_t) == evaluate(rows_with_future) == ("510001", True, 0.5)
