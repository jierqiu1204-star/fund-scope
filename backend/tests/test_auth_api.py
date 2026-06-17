from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import create_access_token, hash_password
from app.core.config import Settings
from app.models.entities import User


@pytest.mark.asyncio
async def test_register_creates_pending_user(client, app) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "new-user@example.com", "password": "password-123", "display_name": "新用户"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["email"] == "new-user@example.com"
    assert payload["is_approved"] is False
    assert payload["is_super_admin"] is False
    async with app.state.db.session() as session:
        user = await session.scalar(select(User).where(User.email == "new-user@example.com"))
    assert user is not None
    assert user.recipient_email == "new-user@example.com"


@pytest.mark.asyncio
async def test_pending_user_cannot_login(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            User(
                email="pending@example.com",
                password_hash=hash_password("password-123"),
                recipient_email="pending@example.com",
                is_approved=False,
                is_super_admin=False,
            )
        )
        await session.commit()

    response = await client.post(
        "/api/auth/login",
        json={"email": "pending@example.com", "password": "password-123"},
    )

    assert response.status_code == 403
    assert "批准" in response.json()["detail"]


@pytest.mark.asyncio
async def test_approved_user_can_login_and_read_me(client) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"email": "19535838578@163.com", "password": "test-password"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_super_admin"] is True


@pytest.mark.asyncio
async def test_protected_api_requires_token(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as bare_client:
        response = await bare_client.get("/api/tracked-positions")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_settings_test_send_requires_token(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as bare_client:
        response = await bare_client.post(
            "/api/settings/notifications/test-send",
            json={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_username": "mailer@example.com",
                "smtp_password": "secret",
                "recipient_email": "owner@example.com",
            },
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_normal_user_cannot_run_admin_job(client, app) -> None:
    async with app.state.db.session() as session:
        normal = User(
            email="normal-job@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="normal-job@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        session.add(normal)
        await session.commit()
        await session.refresh(normal)
        token, _ = create_access_token(normal, app.state.settings)

    response = await client.post(
        "/api/admin/jobs/daily_fund_nav/run",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_jwt_secret_is_required(app) -> None:
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None

    with pytest.raises(RuntimeError, match="AUTH_JWT_SECRET"):
        create_access_token(user, Settings(_env_file=None))


@pytest.mark.asyncio
async def test_super_admin_can_approve_user(client, app) -> None:
    async with app.state.db.session() as session:
        user = User(
            email="approve-me@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="approve-me@example.com",
            is_approved=False,
            is_super_admin=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    response = await client.patch(f"/api/admin/users/{user_id}/approval", json={"is_approved": True})

    assert response.status_code == 200
    assert response.json()["is_approved"] is True


@pytest.mark.asyncio
async def test_normal_user_cannot_approve_user(client, app) -> None:
    async with app.state.db.session() as session:
        normal = User(
            email="normal@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="normal@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        target = User(
            email="target@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="target@example.com",
            is_approved=False,
            is_super_admin=False,
        )
        session.add_all([normal, target])
        await session.commit()
        await session.refresh(normal)
        await session.refresh(target)
        token, _ = create_access_token(normal, app.state.settings)

    response = await client.patch(
        f"/api/admin/users/{target.id}/approval",
        json={"is_approved": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
