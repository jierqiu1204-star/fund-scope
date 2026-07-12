from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import TrackedPosition, TrackedPositionAlert

TRACKING_STATE_ACTIVE = "我已持仓"
TRACKING_STATE_ALERT = "触发提醒"
TRACKING_STATE_WEB_ONLY = "仅网页提示"
SUPPORTED_TRACKING_STATES = frozenset(
    {TRACKING_STATE_ACTIVE, TRACKING_STATE_ALERT, TRACKING_STATE_WEB_ONLY}
)


def validate_tracking_states(states: set[str]) -> None:
    unsupported = sorted(states - SUPPORTED_TRACKING_STATES)
    if unsupported:
        raise ValueError(f"不支持的持仓筛选：{', '.join(unsupported)}")


def _is_current_alert(alert: TrackedPositionAlert, as_of_date: date) -> bool:
    return alert.alert_date == as_of_date


async def tracking_states_by_code(
    session: AsyncSession,
    *,
    user_id: int,
    as_of_date: date,
) -> dict[str, set[str]]:
    positions = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.user_id == user_id,
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == "active",
            )
        )
    ).all()
    states_by_code = {position.asset_code: {TRACKING_STATE_ACTIVE} for position in positions}
    position_code_by_id = {position.id: position.asset_code for position in positions}
    if not position_code_by_id:
        return states_by_code
    alerts = (
        await session.scalars(
            select(TrackedPositionAlert)
            .where(TrackedPositionAlert.tracked_position_id.in_(position_code_by_id))
            .order_by(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc())
        )
    ).all()
    for alert in alerts:
        if not _is_current_alert(alert, as_of_date):
            continue
        code = position_code_by_id.get(alert.tracked_position_id)
        if code is None:
            continue
        states = states_by_code.setdefault(code, {TRACKING_STATE_ACTIVE})
        if alert.alert_type:
            states.add(TRACKING_STATE_ALERT)
        if alert.suppression_status in {"web_only", "suppressed"} or alert.email_status == "skipped":
            states.add(TRACKING_STATE_WEB_ONLY)
    return states_by_code
