from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import user_out
from app.core.auth import require_super_admin
from app.core.db import get_db_session
from app.models.entities import User, utcnow
from app.schemas.auth import AdminUserOut, UserApprovalUpdate

router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_super_admin)],
)


def admin_user_out(user: User) -> AdminUserOut:
    base = user_out(user)
    return AdminUserOut(**base.model_dump(), last_login_at=user.last_login_at)


@router.get("", response_model=list[AdminUserOut])
async def list_users(session: AsyncSession = Depends(get_db_session)) -> list[AdminUserOut]:
    rows = (
        await session.scalars(select(User).order_by(User.is_approved.asc(), User.created_at.desc(), User.id.desc()))
    ).all()
    return [admin_user_out(user) for user in rows]


@router.patch("/{user_id}/approval", response_model=AdminUserOut)
async def update_user_approval(
    user_id: int,
    payload: UserApprovalUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_super_admin and not payload.is_approved:
        raise HTTPException(status_code=422, detail="不能停用超级管理员")
    user.is_approved = payload.is_approved
    user.updated_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return admin_user_out(user)
