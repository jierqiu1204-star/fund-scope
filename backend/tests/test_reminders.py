from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import (
    HoldingsSnapshot,
    IndexValuationHistory,
    NewsItem,
    NewsSummary,
    NotificationLog,
    User,
)
from app.services.dca_calculator import compute_dca_amount
from app.services.notifier import Notifier


@pytest.mark.parametrize(
    ("percentile", "amount", "label"),
    [
        (15, 1249.5, "低估 加码"),
        (40, 833.0, "合理 常规定投"),
        (65, 416.5, "偏高 减量"),
        (85, 0.0, "高估 暂停本月定投"),
        (None, 833.0, "估值数据缺失 使用默认金额"),
    ],
)
def test_dca_calculator_matches_percentile_bands(percentile, amount, label) -> None:
    result = compute_dca_amount(base_amount=833, percentile=percentile)

    assert result.amount == amount
    assert result.reason == label


@pytest.mark.asyncio
async def test_invalid_smtp_credentials_are_not_persisted(client) -> None:
    response = await client.post(
        "/api/settings/notifications/test-send",
        json={
            "smtp_host": "smtp.invalid",
            "smtp_port": 587,
            "smtp_username": "broken",
            "smtp_password": "broken",
            "recipient_email": "owner@example.com",
        },
    )

    assert response.status_code == 422
    assert "smtp" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_notification_test_send_uses_submitted_smtp_fields(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, *, hostname: str, port: int, use_tls: bool) -> None:
            captured["hostname"] = hostname
            captured["port"] = port
            captured["use_tls"] = use_tls

        async def connect(self) -> None:
            captured["connected"] = True

        async def login(self, username: str, password: str) -> None:
            captured["username"] = username
            captured["password"] = password

        async def quit(self) -> None:
            captured["quit"] = True

    monkeypatch.setattr("app.services.notifier.aiosmtplib.SMTP", FakeSMTP)

    response = await client.post(
        "/api/settings/notifications/test-send",
        json={
            "smtp_host": "smtp.real.local",
            "smtp_port": 2525,
            "smtp_username": "saved-user",
            "smtp_password": "saved-password",
            "smtp_from": "FundScope <saved@example.com>",
            "recipient_email": "saved-recipient@example.com",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "hostname": "smtp.real.local",
        "port": 2525,
        "use_tls": False,
        "connected": True,
        "username": "saved-user",
        "password": "saved-password",
        "quit": True,
    }


@pytest.mark.asyncio
async def test_manual_monthly_reminder_returns_computed_amount_and_send_status(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            IndexValuationHistory(
                index_code="CSI300",
                valuation_date=date(2026, 5, 1),
                pe=12.3,
                pb=1.4,
                dividend_yield=2.1,
                pe_percentile=15,
                pb_percentile=20,
                effective_window=3650,
            )
        )
        news_item = NewsItem(
            fund_code="007339",
            published_at=date(2026, 5, 1),
            title="Manager changed",
            url="https://example.com/news/3",
            raw_content="Manager changed",
        )
        session.add(news_item)
        await session.flush()
        session.add(
            NewsSummary(
                news_item_id=news_item.id,
                summary="manager_change|基金经理变更",
                event_type="manager_change",
                model_name="test-model",
            )
        )
        await session.commit()

    response = await client.post("/api/admin/jobs/monthly_dca_reminder/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["amount"] == 1249.5
    assert payload["send_status"] == "sent"


@pytest.mark.asyncio
async def test_manual_monthly_reminder_uses_saved_notification_settings(
    client,
    app,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        captured["smtp_host"] = self.smtp_host
        captured["smtp_port"] = self.smtp_port
        captured["smtp_username"] = self.smtp_username
        captured["smtp_password"] = self.smtp_password
        captured["smtp_from"] = self.smtp_from
        captured["recipient"] = recipient
        captured["template_name"] = template_name
        captured["payload"] = payload
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.recipient_email = "saved-recipient@example.com"
        user.smtp_host = "smtp.saved.local"
        user.smtp_port = 2525
        user.smtp_username = "saved-user"
        user.smtp_password_ref = "env:SMTP_PASSWORD"
        user.smtp_from = "FundScope <saved@example.com>"
        session.add(
            IndexValuationHistory(
                index_code="CSI300",
                valuation_date=date(2026, 5, 1),
                pe=12.3,
                pb=1.4,
                dividend_yield=2.1,
                pe_percentile=40,
                pb_percentile=20,
                effective_window=3650,
            )
        )
        session.add(
            HoldingsSnapshot(
                portfolio_id=1,
                snapshot_date=date(2026, 5, 1),
                fund_code="007339",
                shares=100,
                cost_basis=100,
                market_value=150,
            )
        )
        await session.commit()

    response = await client.post("/api/admin/jobs/monthly_dca_reminder/run")

    assert response.status_code == 200
    assert captured["smtp_host"] == "smtp.saved.local"
    assert captured["smtp_port"] == 2525
    assert captured["smtp_username"] == "saved-user"
    assert captured["smtp_password"] == "secret"
    assert captured["smtp_from"] == "FundScope <saved@example.com>"
    assert captured["recipient"] == "saved-recipient@example.com"
    payload = captured["payload"]
    assert payload["valuation_statuses"][0]["index_code"] == "CSI300"
    assert payload["portfolio_return"]["pnl"] == 50.0


@pytest.mark.asyncio
async def test_notifier_retries_and_logs_failed_send(app, monkeypatch) -> None:
    attempts = 0

    async def fail_send(*_: object, **__: object) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("smtp down")

    monkeypatch.setattr("app.services.notifier.aiosmtplib.send", fail_send)

    notifier = Notifier(
        smtp_host="smtp.real.local",
        smtp_port=2525,
        smtp_username="saved-user",
        smtp_password="saved-password",
        smtp_from="FundScope <saved@example.com>",
    )
    async with app.state.db.session() as session:
        with pytest.raises(RuntimeError, match="smtp down"):
            await notifier.send_template(
                session,
                recipient="saved-recipient@example.com",
                template_name="dca_monthly.html.j2",
                payload={
                    "title": "Monthly DCA Reminder",
                    "amount": 833,
                    "reason": "base",
                    "breakdown": [],
                    "valuation_statuses": [],
                    "portfolio_return": None,
                    "critical_events": [],
                },
            )
        log = await session.scalar(select(NotificationLog).order_by(NotificationLog.id.desc()))

    assert attempts == 3
    assert log is not None
    assert log.status == "failed"
    assert log.error_message == "smtp down"
