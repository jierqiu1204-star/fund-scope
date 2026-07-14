from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)

MAX_PAGE_SIZE = 100
T = TypeVar("T")


@dataclass(frozen=True)
class TimelineCursor:
    recorded_at: datetime
    row_id: int


@dataclass(frozen=True)
class TimelinePage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: TimelineCursor | None


@dataclass(frozen=True)
class LatestAuditDataState:
    state: str
    reason_code: str | None


def encode_timeline_cursor(cursor: TimelineCursor) -> str:
    payload = json.dumps(
        {"i": cursor.row_id, "t": cursor.recorded_at.isoformat(), "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_timeline_cursor(value: str) -> TimelineCursor:
    if not value or len(value) > 512:
        raise ValueError("invalid timeline cursor")
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        payload = json.loads(decoded)
        if set(payload) != {"i", "t", "v"} or payload["v"] != 1:
            raise ValueError
        row_id = payload["i"]
        if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
            raise ValueError
        recorded_at = datetime.fromisoformat(payload["t"])
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid timeline cursor") from exc
    return TimelineCursor(recorded_at=recorded_at, row_id=row_id)


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")


def _before(statement: Select, model, cursor: TimelineCursor | None) -> Select:
    if cursor is None:
        return statement
    return statement.where(
        tuple_(model.created_at, model.id) < tuple_(cursor.recorded_at, cursor.row_id)
    )


def _timeline_page(rows: list[T], limit: int) -> TimelinePage[T]:
    has_more = len(rows) > limit
    items = tuple(rows[:limit])
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = TimelineCursor(recorded_at=last.created_at, row_id=last.id)
    return TimelinePage(items=items, next_cursor=next_cursor)


async def get_current_action(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
) -> TrackedPositionActionDecision | None:
    return await session.scalar(
        select(TrackedPositionActionDecision)
        .where(
            TrackedPositionActionDecision.tracked_position_id == position_id,
            TrackedPositionActionDecision.user_id == owner_id,
            TrackedPositionActionDecision.is_current,
        )
        .limit(1)
    )


async def get_current_actions(
    session: AsyncSession,
    *,
    owner_id: int,
    position_ids: list[int],
) -> dict[int, TrackedPositionActionDecision]:
    if not position_ids:
        return {}
    rows = await session.scalars(
        select(TrackedPositionActionDecision).where(
            TrackedPositionActionDecision.tracked_position_id.in_(position_ids),
            TrackedPositionActionDecision.user_id == owner_id,
            TrackedPositionActionDecision.is_current,
        )
    )
    return {row.tracked_position_id: row for row in rows.all()}


async def list_action_history(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    before: TimelineCursor | None = None,
    limit: int = 50,
) -> TimelinePage[TrackedPositionActionDecision]:
    _validate_limit(limit)
    statement = select(TrackedPositionActionDecision).where(
        TrackedPositionActionDecision.tracked_position_id == position_id,
        TrackedPositionActionDecision.user_id == owner_id,
    )
    statement = _before(statement, TrackedPositionActionDecision, before).order_by(
        TrackedPositionActionDecision.created_at.desc(),
        TrackedPositionActionDecision.id.desc(),
    )
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    return _timeline_page(rows, limit)


async def list_alert_audit_page(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    before: TimelineCursor | None = None,
    limit: int = 50,
) -> TimelinePage[TrackedPositionAlertAudit]:
    _validate_limit(limit)
    statement = (
        select(TrackedPositionAlertAudit)
        .join(
            TrackedPosition,
            TrackedPosition.id == TrackedPositionAlertAudit.tracked_position_id,
        )
        .where(
            TrackedPositionAlertAudit.tracked_position_id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    statement = _before(statement, TrackedPositionAlertAudit, before).order_by(
        TrackedPositionAlertAudit.created_at.desc(),
        TrackedPositionAlertAudit.id.desc(),
    )
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    return _timeline_page(rows, limit)


async def count_alert_audits(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(TrackedPositionAlertAudit)
        .join(
            TrackedPosition,
            TrackedPosition.id == TrackedPositionAlertAudit.tracked_position_id,
        )
        .where(
            TrackedPositionAlertAudit.tracked_position_id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    return int(value or 0)


async def get_latest_audit_data_states(
    session: AsyncSession,
    *,
    owner_id: int,
    position_ids: list[int],
) -> dict[int, LatestAuditDataState]:
    if not position_ids:
        return {}
    row_number = func.row_number().over(
        partition_by=TrackedPositionAlertAudit.tracked_position_id,
        order_by=(
            TrackedPositionAlertAudit.created_at.desc(),
            TrackedPositionAlertAudit.id.desc(),
        ),
    )
    ranked = (
        select(
            TrackedPositionAlertAudit.tracked_position_id.label("position_id"),
            TrackedPositionAlertAudit.data_state.label("data_state"),
            TrackedPositionAlertAudit.decision_context_json.label("decision_context"),
            row_number.label("row_number"),
        )
        .join(
            TrackedPosition,
            TrackedPosition.id == TrackedPositionAlertAudit.tracked_position_id,
        )
        .where(
            TrackedPositionAlertAudit.tracked_position_id.in_(position_ids),
            TrackedPosition.user_id == owner_id,
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ranked.c.position_id,
                ranked.c.data_state,
                ranked.c.decision_context,
            ).where(ranked.c.row_number == 1)
        )
    ).all()
    result: dict[int, LatestAuditDataState] = {}
    for position_id, data_state, decision_context in rows:
        context = decision_context if isinstance(decision_context, dict) else {}
        reason_code = context.get("data_reason_code") or context.get("reason_code")
        if data_state:
            result[int(position_id)] = LatestAuditDataState(
                state=str(data_state),
                reason_code=str(reason_code) if reason_code else None,
            )
    return result


async def list_legacy_alert_page(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    before: TimelineCursor | None = None,
    limit: int = 50,
) -> TimelinePage[TrackedPositionAlert]:
    _validate_limit(limit)
    statement = (
        select(TrackedPositionAlert)
        .join(
            TrackedPosition,
            TrackedPosition.id == TrackedPositionAlert.tracked_position_id,
        )
        .where(
            TrackedPositionAlert.tracked_position_id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    statement = _before(statement, TrackedPositionAlert, before).order_by(
        TrackedPositionAlert.created_at.desc(),
        TrackedPositionAlert.id.desc(),
    )
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    return _timeline_page(rows, limit)


async def count_legacy_alerts(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(TrackedPositionAlert)
        .join(
            TrackedPosition,
            TrackedPosition.id == TrackedPositionAlert.tracked_position_id,
        )
        .where(
            TrackedPositionAlert.tracked_position_id == position_id,
            TrackedPosition.user_id == owner_id,
        )
    )
    return int(value or 0)


async def list_repeat_slot_items(
    session: AsyncSession,
    *,
    owner_id: int,
    position_id: int,
    repeat_slot: str,
    before: TimelineCursor | None = None,
    limit: int = 50,
) -> TimelinePage[TrackedPositionNotificationItem]:
    _validate_limit(limit)
    statement = select(TrackedPositionNotificationItem).where(
        TrackedPositionNotificationItem.tracked_position_id == position_id,
        TrackedPositionNotificationItem.user_id == owner_id,
        TrackedPositionNotificationItem.repeat_slot == repeat_slot,
    )
    statement = _before(statement, TrackedPositionNotificationItem, before).order_by(
        TrackedPositionNotificationItem.created_at.desc(),
        TrackedPositionNotificationItem.id.desc(),
    )
    rows = list((await session.scalars(statement.limit(limit + 1))).all())
    return _timeline_page(rows, limit)


async def list_pending_envelopes(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> tuple[TrackedPositionNotificationEnvelope, ...]:
    _validate_limit(limit)
    rows = await session.scalars(
        select(TrackedPositionNotificationEnvelope)
        .where(TrackedPositionNotificationEnvelope.status == "pending")
        .order_by(
            TrackedPositionNotificationEnvelope.lease_expires_at,
            TrackedPositionNotificationEnvelope.id,
        )
        .limit(limit)
    )
    return tuple(rows.all())
