from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.entities import TrackedPosition
from app.services.tracked_positions.service import (
    ACTIVE_STATUS,
    create_alert_if_needed,
    refresh_entry_if_waiting,
)


async def daily_tracked_position_alerts_job(
    session: AsyncSession,
    settings: Settings | None = None,
) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(TrackedPosition)
            .where(TrackedPosition.status == ACTIVE_STATUS)
            .order_by(TrackedPosition.created_at.asc(), TrackedPosition.id.asc())
        )
    ).all()
    result = {
        "positions_checked": len(rows),
        "alerts_created": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "emails_skipped": 0,
        "deduplicated": 0,
        "no_signal": 0,
    }
    effective_settings = settings or get_settings()
    for position in rows:
        await refresh_entry_if_waiting(session, position)
        alert, status = await create_alert_if_needed(session, position, effective_settings)
        if status == "deduplicated":
            result["deduplicated"] += 1
            continue
        if status == "no_signal":
            result["no_signal"] += 1
            continue
        if alert is not None:
            result["alerts_created"] += 1
        if status == "email_sent":
            result["emails_sent"] += 1
        elif status == "email_failed":
            result["emails_failed"] += 1
        elif status == "email_skipped":
            result["emails_skipped"] += 1
    return result
