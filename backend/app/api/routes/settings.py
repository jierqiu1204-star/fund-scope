from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_db_session
from app.core.instance import get_instance_owner
from app.models.entities import User, utcnow
from app.schemas.settings import (
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    NotificationTestSend,
)
from app.services.notifier import Notifier, SMTPError

router = APIRouter(prefix="/api/settings/notifications", tags=["settings"])


def _notification_settings(user: User, settings: Settings) -> NotificationSettingsRead:
    return NotificationSettingsRead.model_validate(user, from_attributes=True).model_copy(
        update={
            "smtp_host": user.smtp_host or settings.smtp_host,
            "smtp_port": user.smtp_port or settings.smtp_port,
            "smtp_username": user.smtp_username or settings.smtp_username,
            "smtp_from": user.smtp_from or settings.smtp_from,
        }
    )


@router.get("", response_model=NotificationSettingsRead)
async def get_notification_settings(
    request: Request,
    user: User = Depends(get_instance_owner),
) -> NotificationSettingsRead:
    return _notification_settings(user, request.app.state.settings)


@router.put("", response_model=NotificationSettingsRead)
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    request: Request,
    user: User = Depends(get_instance_owner),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationSettingsRead:
    user.recipient_email = payload.recipient_email
    user.reminder_day = payload.reminder_day
    user.reference_index_code = payload.reference_index_code
    user.base_monthly_amount = payload.base_monthly_amount
    user.etf_trading_capital = payload.etf_trading_capital
    # Existing default values are deliberately not treated as owner-confirmed.
    # Saving this settings form is the explicit confirmation boundary.
    user.etf_trading_capital_confirmed_at = utcnow()
    user.allow_full_exit = payload.allow_full_exit
    user.smtp_host = payload.smtp_host
    user.smtp_port = payload.smtp_port
    user.smtp_username = payload.smtp_username
    user.smtp_from = payload.smtp_from
    if payload.smtp_password:
        user.smtp_password_ref = "env:SMTP_PASSWORD"
    await session.commit()
    return _notification_settings(user, request.app.state.settings)


@router.post("/test-send")
async def test_send(
    payload: NotificationTestSend,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    notifier = Notifier(
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_username=payload.smtp_username,
        smtp_password=payload.smtp_password or request.app.state.settings.smtp_password,
        smtp_from=payload.smtp_from or f"FundScope <{payload.smtp_username}>",
    )
    try:
        await notifier.test_connection()
    except SMTPError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        await notifier.send_test_email(session, recipient=payload.recipient_email)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"SMTP 登录或发送失败：{exc}") from exc
    return {"status": "sent"}
