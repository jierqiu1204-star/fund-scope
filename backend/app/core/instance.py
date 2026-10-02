from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_db_session
from app.models.entities import User


async def resolve_instance_owner(session: AsyncSession, settings: Settings) -> User:
    """Reuse the existing deployment owner and preserve its position/settings IDs."""
    configured_email = (
        settings.instance_owner_email or settings.auth_bootstrap_admin_email
    ).strip().lower()
    owner = None
    if configured_email:
        owner = await session.scalar(select(User).where(User.email == configured_email))
        if owner is None and settings.instance_owner_email:
            raise ValueError("INSTANCE_OWNER_EMAIL must match an existing user in this database")
    if owner is None:
        owner = await session.scalar(
            select(User).order_by(User.is_super_admin.desc(), User.id).limit(1)
        )
    recipient = settings.smtp_username.strip()
    if "@" not in recipient:
        recipient = ""
    if owner is not None:
        # Old migrations seed a personal mailbox. Claim only the untouched seed
        # identity; existing accounts and custom notification settings stay intact.
        if (
            owner.email == "owner@example.com"
            and not owner.password_hash
            and owner.display_name != "本实例"
        ):
            if owner.recipient_email in {"owner@example.com", "19535838578@163.com"}:
                owner.recipient_email = recipient
            if owner.smtp_host == "smtp.163.com" and owner.smtp_username == "19535838578@163.com":
                owner.smtp_host = None
                owner.smtp_username = None
                if owner.smtp_port == 465:
                    owner.smtp_port = None
            if owner.smtp_from == "FundScope <19535838578@163.com>":
                owner.smtp_from = None
            owner.display_name = "本实例"
            await session.commit()
        return owner
    owner = User(
        email="instance@localhost.invalid",
        display_name="本实例",
        recipient_email=recipient,
    )
    session.add(owner)
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent first requests must reuse the same newly-created owner.
        await session.rollback()
        owner = await session.scalar(
            select(User).where(User.email == "instance@localhost.invalid")
        )
        if owner is None:
            raise
    return owner


async def get_instance_owner(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    return await resolve_instance_owner(session, request.app.state.settings)
