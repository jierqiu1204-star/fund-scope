from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, hash_password, require_approved_user, verify_password
from app.core.db import get_db_session
from app.models.entities import User, utcnow
from app.schemas.auth import AuthTokenOut, LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        recipient_email=user.recipient_email,
        is_approved=user.is_approved,
        is_super_admin=user.is_super_admin,
        created_at=user.created_at,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
) -> UserOut:
    email = payload.email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=409, detail="这个邮箱已经注册")
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        recipient_email=email,
        is_approved=False,
        is_super_admin=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user_out(user)


@router.post("/login", response_model=AuthTokenOut)
async def login_user(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AuthTokenOut:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")
    if not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号等待管理员批准")
    user.last_login_at = utcnow()
    await session.commit()
    await session.refresh(user)
    token, expires_at = create_access_token(user, request.app.state.settings)
    return AuthTokenOut(access_token=token, expires_at=expires_at, user=user_out(user))


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(require_approved_user)) -> UserOut:
    return user_out(user)


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"status": "ok"}
