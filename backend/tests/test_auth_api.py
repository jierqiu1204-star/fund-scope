from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.instance import resolve_instance_owner
from app.db.base import Base
from app.main import create_app
from app.models.entities import User


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/admin/users"),
        ("PATCH", "/api/admin/users/1/approval"),
    ],
)
async def test_account_routes_are_removed(client, method, path) -> None:
    response = await client.request(method, path, json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_no_token_secret_or_approval_is_needed(client, app) -> None:
    app.state.settings.auth_jwt_secret = ""
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        owner.is_approved = False
        owner.password_hash = None
        await session.commit()
    assert "Authorization" not in client.headers
    response = await client.get("/api/settings/notifications")
    assert response.status_code == 200
    assert response.json()["recipient_email"] == "19535838578@163.com"
    assert (await client.get("/api/tracked-positions")).status_code == 200
    assert (await client.get("/api/admin/jobs")).status_code == 200


@pytest.mark.asyncio
async def test_instance_settings_update_preserves_existing_owner(client, app) -> None:
    response = await client.put(
        "/api/settings/notifications",
        json={
            "recipient_email": "instance-owner@example.com",
            "reminder_day": 1,
            "reference_index_code": "CSI300",
            "base_monthly_amount": 833,
            "etf_trading_capital": 12000,
            "allow_full_exit": False,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_username": "mailer@example.com",
            "smtp_from": "FundScope <mailer@example.com>",
        },
    )
    assert response.status_code == 200
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        assert owner.recipient_email == "instance-owner@example.com"
        assert owner.etf_trading_capital == 12000
        assert await session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.asyncio
async def test_legacy_configured_owner_is_reused_without_overwriting_settings(app) -> None:
    async with app.state.db.session() as session:
        owner = User(
            email="existing-owner@example.com",
            recipient_email="custom-recipient@example.com",
            smtp_username="custom-mailer@example.com",
            is_approved=False,
        )
        session.add(owner)
        await session.commit()
        existing_id = owner.id
        app.state.settings.auth_bootstrap_admin_email = owner.email
        selected = await resolve_instance_owner(session, app.state.settings)
        assert selected.id == existing_id
        assert selected.recipient_email == "custom-recipient@example.com"
        assert selected.smtp_username == "custom-mailer@example.com"
        assert await session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.asyncio
async def test_separate_deployments_keep_separate_settings(settings, tmp_path) -> None:
    instances = []
    try:
        for name in ("first", "second"):
            instance_settings = settings.model_copy(
                update={
                    "database_url": f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}",
                    "auth_jwt_secret": "",
                    "smtp_username": f"{name}@example.com",
                }
            )
            application = create_app(settings=instance_settings, start_scheduler=False)
            instances.append(application)
            async with application.state.db.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as visitor:
                first = await visitor.get("/api/settings/notifications")
                second = await visitor.get("/api/settings/notifications")
                assert first.status_code == second.status_code == 200
                assert first.json()["recipient_email"] == f"{name}@example.com"
            async with application.state.db.session() as session:
                assert await session.scalar(select(func.count()).select_from(User)) == 1
    finally:
        for application in instances:
            await application.state.db.engine.dispose()


@pytest.mark.asyncio
async def test_migration_seed_uses_local_smtp_and_keeps_position_owner_id(app) -> None:
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        owner.email = "owner@example.com"
        owner.password_hash = None
        owner.recipient_email = "19535838578@163.com"
        owner.smtp_host = "smtp.163.com"
        owner.smtp_port = 465
        owner.smtp_username = "19535838578@163.com"
        owner.smtp_from = "FundScope <19535838578@163.com>"
        await session.commit()
        app.state.settings.instance_owner_email = "owner@example.com"
        selected = await resolve_instance_owner(session, app.state.settings)
        assert selected.id == 1
        assert selected.email == "owner@example.com"
        assert selected.recipient_email == "mailer@example.com"
        assert selected.smtp_host is None
        assert selected.smtp_port is None
        assert selected.smtp_username is None
        assert selected.smtp_from is None
        selected.recipient_email = "custom@example.com"
        await session.commit()
        assert (await resolve_instance_owner(session, app.state.settings)).recipient_email == "custom@example.com"


@pytest.mark.asyncio
async def test_migration_seed_preserves_custom_mail_settings(app) -> None:
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        owner.email = "owner@example.com"
        owner.password_hash = None
        owner.recipient_email = "custom-recipient@example.com"
        owner.smtp_host = "smtp.custom.example.com"
        owner.smtp_port = 2525
        owner.smtp_username = "custom-mailer@example.com"
        owner.smtp_from = "Custom <custom-mailer@example.com>"
        await session.commit()
        selected = await resolve_instance_owner(session, app.state.settings)
        assert selected.id == 1
        assert selected.recipient_email == "custom-recipient@example.com"
        assert selected.smtp_host == "smtp.custom.example.com"
        assert selected.smtp_port == 2525
        assert selected.smtp_username == "custom-mailer@example.com"
        assert selected.smtp_from == "Custom <custom-mailer@example.com>"


@pytest.mark.asyncio
async def test_explicit_instance_owner_takes_precedence_over_legacy_admin(app) -> None:
    async with app.state.db.session() as session:
        owner = User(email="chosen-owner@example.com", recipient_email="chosen@example.com")
        session.add(owner)
        await session.commit()
        app.state.settings.instance_owner_email = owner.email
        app.state.settings.auth_bootstrap_admin_email = "19535838578@163.com"
        selected = await resolve_instance_owner(session, app.state.settings)
        assert selected.id == owner.id
        app.state.settings.instance_owner_email = "missing-owner@example.com"
        with pytest.raises(ValueError, match="INSTANCE_OWNER_EMAIL"):
            await resolve_instance_owner(session, app.state.settings)


@pytest.mark.asyncio
async def test_notification_settings_show_effective_smtp_without_exposing_password(client, app) -> None:
    app.state.settings.smtp_host = "smtp.instance.local"
    app.state.settings.smtp_port = 465
    app.state.settings.smtp_username = "instance@example.com"
    app.state.settings.smtp_from = "Instance <instance@example.com>"
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        owner.smtp_host = owner.smtp_port = owner.smtp_username = owner.smtp_from = None
        await session.commit()
    response = await client.get("/api/settings/notifications")
    assert response.status_code == 200
    body = response.json()
    assert body["smtp_host"] == "smtp.instance.local"
    assert body["smtp_port"] == 465
    assert body["smtp_username"] == "instance@example.com"
    assert body["smtp_from"] == "Instance <instance@example.com>"
    assert "smtp_password" not in body


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
