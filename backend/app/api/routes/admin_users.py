from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import user_out
from app.api.routes.tracked_positions import _position_out
from app.core.auth import require_super_admin
from app.core.db import get_db_session
from app.models.entities import TrackedPosition, TrackedPositionAlert, User, utcnow
from app.schemas.auth import AdminUserDetailOut, AdminUserSummaryOut, UserApprovalUpdate
from app.schemas.tracked_positions import TrackedPositionAlertOut, TrackedPositionOut
from app.services.tracked_positions.service import alert_out, email_configured

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_super_admin)],
)
logger = logging.getLogger(__name__)


def _mask_account(value: str | None) -> str | None:
    if not value:
        return None
    if "@" in value:
        left, right = value.split("@", 1)
        head = left[:2]
        tail = left[-2:] if len(left) > 4 else ""
        return f"{head}***{tail}@{right}"
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


async def _tracking_summary(session: AsyncSession, user_id: int) -> dict[str, int]:
    rows = (
        await session.execute(
            select(TrackedPosition.status, func.count(TrackedPosition.id))
            .where(TrackedPosition.user_id == user_id)
            .group_by(TrackedPosition.status)
        )
    ).all()
    summary = {str(status): int(count) for status, count in rows}
    summary["total"] = sum(summary.values())
    summary["active"] = summary.get("active", 0)
    return summary


async def admin_user_out(
    request: Request,
    session: AsyncSession,
    user: User,
) -> AdminUserSummaryOut:
    base = user_out(user)
    return AdminUserSummaryOut(
        **base.model_dump(),
        last_login_at=user.last_login_at,
        detail_url=f"/api/admin/users/{user.id}",
        notification_configured=email_configured(user, request.app.state.settings),
        smtp_host=user.smtp_host,
        smtp_port=user.smtp_port,
        smtp_username_masked=_mask_account(user.smtp_username),
        smtp_from=user.smtp_from,
        tracking_summary=await _tracking_summary(session, user.id),
    )


@router.get("", response_model=list[AdminUserSummaryOut])
async def list_users(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminUserSummaryOut]:
    rows = (
        await session.scalars(select(User).order_by(User.is_approved.asc(), User.created_at.desc(), User.id.desc()))
    ).all()
    return [await admin_user_out(request, session, user) for user in rows]


@router.get("/{user_id}", response_model=AdminUserDetailOut)
async def get_user_detail(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_super_admin),
) -> AdminUserDetailOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    logger.info("admin_user_read", extra={"admin_user_id": current_admin.id, "target_user_id": user_id})
    summary = await admin_user_out(request, session, user)
    return AdminUserDetailOut(**summary.model_dump(), readonly=True)


@router.get("/{user_id}/tracked-positions", response_model=list[TrackedPositionOut])
async def list_user_tracked_positions(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_super_admin),
) -> list[TrackedPositionOut]:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    logger.info("admin_user_tracked_positions_read", extra={"admin_user_id": current_admin.id, "target_user_id": user_id})
    rows = (
        await session.scalars(
            select(TrackedPosition)
            .where(TrackedPosition.user_id == user_id)
            .order_by(TrackedPosition.status.asc(), TrackedPosition.created_at.desc(), TrackedPosition.id.desc())
        )
    ).all()
    return [await _position_out(session, row) for row in rows]


@router.get("/{user_id}/alerts", response_model=list[TrackedPositionAlertOut])
async def list_user_alerts(
    user_id: int,
    session: AsyncSession = Depends(get_db_session),
    current_admin: User = Depends(require_super_admin),
) -> list[TrackedPositionAlertOut]:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    logger.info("admin_user_alerts_read", extra={"admin_user_id": current_admin.id, "target_user_id": user_id})
    position_ids = (
        await session.scalars(select(TrackedPosition.id).where(TrackedPosition.user_id == user_id))
    ).all()
    if not position_ids:
        return []
    rows = (
        await session.scalars(
            select(TrackedPositionAlert)
            .where(TrackedPositionAlert.tracked_position_id.in_(position_ids))
            .order_by(TrackedPositionAlert.created_at.desc(), TrackedPositionAlert.id.desc())
            .limit(100)
        )
    ).all()
    return [alert_out(row) for row in rows]


@router.patch("/{user_id}/approval", response_model=AdminUserSummaryOut)
async def update_user_approval(
    user_id: int,
    payload: UserApprovalUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserSummaryOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_super_admin and not payload.is_approved:
        raise HTTPException(status_code=422, detail="不能停用超级管理员")
    user.is_approved = payload.is_approved
    user.updated_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return await admin_user_out(request, session, user)
