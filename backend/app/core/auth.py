from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_db_session
from app.models.entities import User, utcnow

HASH_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return (
        "pbkdf2_sha256"
        f"${HASH_ITERATIONS}"
        f"${base64.urlsafe_b64encode(salt).decode('ascii')}"
        f"${base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}".encode("ascii"))


def create_access_token(user: User, settings: Settings) -> tuple[str, datetime]:
    if not settings.auth_jwt_secret:
        raise RuntimeError("AUTH_JWT_SECRET is required for authentication")
    expires_at = utcnow() + timedelta(days=settings.auth_token_expire_days)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "is_super_admin": user.is_super_admin,
        "exp": int(expires_at.timestamp()),
    }
    signing_input = ".".join(
        [
            _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        settings.auth_jwt_secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}", expires_at


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.auth_jwt_secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="认证配置缺少 AUTH_JWT_SECRET")
    try:
        header_raw, payload_raw, signature_raw = token.split(".", 2)
        signing_input = f"{header_raw}.{payload_raw}"
        expected = hmac.new(
            settings.auth_jwt_secret.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64decode(signature_raw)
        if not hmac.compare_digest(actual, expected):
            raise ValueError("bad signature")
        header = json.loads(_b64decode(header_raw))
        if header.get("alg") != "HS256":
            raise ValueError("bad algorithm")
        payload = json.loads(_b64decode(payload_raw))
        if not isinstance(payload, dict):
            raise ValueError("bad payload")
        if int(payload.get("exp", 0)) < int(utcnow().timestamp()):
            raise ValueError("expired")
        return dict(payload)
    except (ValueError, json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录") from exc


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return authorization.split(" ", 1)[1].strip()


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    payload = decode_access_token(bearer_token(authorization), request.app.state.settings)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录") from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在，请重新登录")
    return user


async def require_approved_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号等待管理员批准")
    return user


async def get_optional_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> User | None:
    if not authorization:
        return None
    return await get_current_user(request, authorization=authorization, session=session)


async def optional_approved_user(user: User | None = Depends(get_optional_current_user)) -> User | None:
    if user is None:
        return None
    if not user.is_approved:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号等待管理员批准")
    return user


async def require_super_admin(user: User = Depends(require_approved_user)) -> User:
    if not user.is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


async def ensure_bootstrap_admin(session: AsyncSession, settings: Settings) -> None:
    email = settings.auth_bootstrap_admin_email.strip().lower()
    if not email or not settings.auth_bootstrap_admin_password:
        return
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        legacy_user = await session.get(User, 1)
        if (
            legacy_user is not None
            and legacy_user.is_super_admin
            and legacy_user.display_name == settings.auth_bootstrap_admin_display_name
            and not legacy_user.password_hash
        ):
            user = legacy_user
        else:
            user = User(
                email=email,
                recipient_email=email,
                reminder_day=1,
                reference_index_code="CSI300",
                base_monthly_amount=833.0,
            )
            session.add(user)
            await session.flush()
    user.email = email
    user.recipient_email = email
    user.display_name = settings.auth_bootstrap_admin_display_name or "qje"
    user.is_approved = True
    user.is_super_admin = True
    if settings.auth_bootstrap_admin_password:
        user.password_hash = hash_password(settings.auth_bootstrap_admin_password)
    user.updated_at = utcnow()
    await session.commit()
