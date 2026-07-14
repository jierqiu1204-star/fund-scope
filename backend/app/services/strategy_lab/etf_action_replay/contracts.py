from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

MAX_REPLAY_CANDIDATES = 3


class TargetSemantics(StrEnum):
    ABSOLUTE_EXPOSURE_BASELINE = "absolute_exposure_baseline"
    RELATIVE_CURRENT_POSITION = "relative_current_position"


class LifecycleEventType(StrEnum):
    ACTION_DECISION = "action_decision"
    NOTIFICATION_REPEAT = "notification_repeat"
    NOTIFICATION_RETRY = "notification_retry"
    RECOVERY_NOTIFICATION = "recovery_notification"
    SOFT_WATCH = "soft_watch"


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    event_type: LifecycleEventType
    occurred_at: datetime
    action_cycle_id: str | None = None
    action_decision_id: str | None = None
    target_remaining_fraction: float | None = None
    target_semantics: TargetSemantics | None = None


@dataclass(frozen=True)
class ReplayTradeIntent:
    event_id: str
    occurred_at: datetime
    action_cycle_id: str
    action_decision_id: str
    target_remaining_fraction: float
    target_stage: str


class PointInTimeDataGapError(ValueError):
    """Historical replay input is incomplete or ambiguous at the requested cutoff."""


@dataclass(frozen=True)
class PointInTimeMembership:
    asset_code: str
    known_at: date
    eligible_from: date
    eligible_through: date | None
    eligible: bool


@dataclass(frozen=True)
class PointInTimeObservation:
    asset_code: str
    session_date: date
    values: Mapping[str, Any]


def truncate_observations_at_cutoff(
    observations: Iterable[PointInTimeObservation],
    *,
    cutoff: date,
) -> tuple[PointInTimeObservation, ...]:
    return tuple(
        sorted(
            (row for row in observations if row.session_date <= cutoff),
            key=lambda row: (row.session_date, row.asset_code),
        )
    )


def point_in_time_cross_section(
    *,
    observations: Iterable[PointInTimeObservation],
    memberships: Iterable[PointInTimeMembership],
    session_date: date,
) -> tuple[PointInTimeObservation, ...]:
    rows_by_code: dict[str, PointInTimeObservation] = {}
    for row in observations:
        if row.session_date != session_date:
            continue
        code = row.asset_code.strip()
        if not code:
            raise PointInTimeDataGapError("blank asset code at replay cutoff")
        if code in rows_by_code:
            raise PointInTimeDataGapError(f"duplicate observation for {code} at {session_date}")
        rows_by_code[code] = row

    if not rows_by_code:
        raise PointInTimeDataGapError(f"no observations at replay cutoff {session_date}")

    membership_rows = tuple(memberships)
    eligible_rows: list[PointInTimeObservation] = []
    for code, row in rows_by_code.items():
        applicable = [
            membership
            for membership in membership_rows
            if membership.asset_code.strip() == code
            and membership.known_at <= session_date
            and membership.eligible_from <= session_date
            and (
                membership.eligible_through is None
                or membership.eligible_through >= session_date
            )
        ]
        if not applicable:
            raise PointInTimeDataGapError(
                f"missing point-in-time membership for {code} at {session_date}"
            )
        if len(applicable) != 1:
            raise PointInTimeDataGapError(
                f"ambiguous point-in-time membership for {code} at {session_date}"
            )
        if applicable[0].eligible:
            eligible_rows.append(row)

    return tuple(sorted(eligible_rows, key=lambda row: row.asset_code))
