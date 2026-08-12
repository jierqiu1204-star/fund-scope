from __future__ import annotations

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import create_access_token, hash_password
from app.core.config import Settings
from app.models.entities import TrackedPosition, TrackedPositionAlert, User


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
async def test_user_can_update_etf_position_sizing_settings(client, app) -> None:
    response = await client.put(
        "/api/settings/notifications",
        json={
            "recipient_email": "19535838578@163.com",
            "reminder_day": 1,
            "reference_index_code": "CSI300",
            "base_monthly_amount": 833,
            "etf_trading_capital": 12000,
            "allow_full_exit": False,
            "smtp_host": "smtp.163.com",
            "smtp_port": 465,
            "smtp_username": "19535838578@163.com",
            "smtp_from": "FundScope <19535838578@163.com>",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["etf_trading_capital"] == 12000
    assert payload["etf_trading_capital_confirmed_at"] is not None
    assert payload["allow_full_exit"] is False
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
    assert user is not None
    assert user.etf_trading_capital == 12000
    assert user.etf_trading_capital_confirmed_at is not None
    assert user.allow_full_exit is False


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_capital", [True, "NaN", "12000"])
async def test_etf_position_sizing_settings_require_explicit_finite_numeric_capital(
    client,
    invalid_capital,
) -> None:
    response = await client.put(
        "/api/settings/notifications",
        json={
            "recipient_email": "19535838578@163.com",
            "reminder_day": 1,
            "reference_index_code": "CSI300",
            "base_monthly_amount": 833,
            "etf_trading_capital": invalid_capital,
            "allow_full_exit": False,
            "smtp_host": "smtp.163.com",
            "smtp_port": 465,
            "smtp_username": "19535838578@163.com",
            "smtp_from": "FundScope <19535838578@163.com>",
        },
    )

    assert response.status_code == 422


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


@pytest.mark.asyncio
async def test_super_admin_can_read_user_observability_without_secrets(client, app) -> None:
    async with app.state.db.session() as session:
        target = User(
            email="observable@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="observable@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="observable@example.com",
            smtp_password_ref="secret-ref",
            smtp_from="observable@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        session.add(target)
        await session.flush()
        position = TrackedPosition(
            user_id=target.id,
            asset_type="etf",
            asset_code="560777",
            asset_name="只读观察ETF",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            confirmed_nav=1.0,
            entry_price=1.0,
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        await session.flush()
        session.add(
            TrackedPositionAlert(
                tracked_position_id=position.id,
                alert_date=date(2026, 6, 2),
                alert_type="trend_weakening",
                trigger_label="趋势转弱",
                email_status="sent",
                reasons_json=["测试提醒"],
                risk_flags_json=[],
            )
        )
        await session.commit()
        target_id = target.id

    list_response = await client.get("/api/admin/users")
    assert list_response.status_code == 200
    target_summary = next(item for item in list_response.json() if item["id"] == target_id)
    assert target_summary["tracking_summary"]["active"] == 1
    assert target_summary["smtp_username_masked"] == "ob***le@example.com"
    assert "smtp_password_ref" not in target_summary
    assert "password_hash" not in target_summary

    detail_response = await client.get(f"/api/admin/users/{target_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["readonly"] is True
    assert isinstance(detail["notification_configured"], bool)
    assert "smtp_password_ref" not in detail
    assert "password_hash" not in detail

    positions_response = await client.get(f"/api/admin/users/{target_id}/tracked-positions")
    assert positions_response.status_code == 200
    positions = positions_response.json()
    assert len(positions) == 1
    assert positions[0]["asset_code"] == "560777"

    alerts_response = await client.get(f"/api/admin/users/{target_id}/alerts")
    assert alerts_response.status_code == 200
    alerts = alerts_response.json()
    assert len(alerts) == 1
    assert alerts[0]["email_status"] == "sent"


@pytest.mark.asyncio
async def test_normal_user_cannot_read_admin_user_observability(client, app) -> None:
    async with app.state.db.session() as session:
        normal = User(
            email="normal-observer@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="normal-observer@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        target = User(
            email="private-target@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="private-target@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        session.add_all([normal, target])
        await session.commit()
        await session.refresh(normal)
        await session.refresh(target)
        token, _ = create_access_token(normal, app.state.settings)

    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/admin/users", headers=headers)).status_code == 403
    assert (await client.get(f"/api/admin/users/{target.id}", headers=headers)).status_code == 403
    assert (await client.get(f"/api/admin/users/{target.id}/tracked-positions", headers=headers)).status_code == 403
    assert (await client.get(f"/api/admin/users/{target.id}/alerts", headers=headers)).status_code == 403
