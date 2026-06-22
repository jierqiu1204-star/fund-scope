from __future__ import annotations

from datetime import date

import pytest

from app.core.auth import hash_password
from app.models.entities import (
    EtfPriceHistory,
    TrackedPosition,
    TrackedPositionAlertAudit,
    TradableEtf,
    User,
)
from app.services.tracked_positions.service import (
    ALERT_HARD_STOP,
    AlertDecision,
    create_alert_if_needed,
)


@pytest.mark.asyncio
async def test_tracked_positions_are_filtered_by_owner(client, app) -> None:
    async with app.state.db.session() as session:
        other = User(
            email="other@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="other@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        session.add(other)
        await session.flush()
        session.add_all(
            [
                TrackedPosition(
                    user_id=1,
                    asset_type="etf",
                    asset_code="510300",
                    asset_name="沪深300ETF",
                    buy_date=date(2026, 6, 1),
                    buy_amount=1000,
                    entry_price=1.0,
                    entry_price_date=date(2026, 6, 1),
                    estimated_shares=1000,
                    status="active",
                ),
                TrackedPosition(
                    user_id=other.id,
                    asset_type="etf",
                    asset_code="512800",
                    asset_name="银行ETF",
                    buy_date=date(2026, 6, 1),
                    buy_amount=1000,
                    entry_price=1.0,
                    entry_price_date=date(2026, 6, 1),
                    estimated_shares=1000,
                    status="active",
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/tracked-positions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["asset_code"] == "510300"


@pytest.mark.asyncio
async def test_tracked_position_audit_is_owner_scoped(client, app) -> None:
    async with app.state.db.session() as session:
        other = User(
            email="other-audit@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="other-audit@example.com",
            is_approved=True,
            is_super_admin=False,
        )
        session.add(other)
        await session.flush()
        position = TrackedPosition(
            user_id=other.id,
            asset_type="etf",
            asset_code="512800",
            asset_name="银行ETF",
            buy_date=date(2026, 6, 1),
            buy_amount=1000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=1000,
            status="active",
        )
        session.add(position)
        await session.flush()
        session.add(
            TrackedPositionAlertAudit(
                tracked_position_id=position.id,
                outcome="sent",
                signal_type="hard_stop",
                alert_date=date(2026, 6, 2),
                alert_type="hard_stop",
                trigger_label="硬止损",
                data_source="intraday_quote",
                quote_freshness="fresh_intraday",
                threshold_context_json={},
                decision_context_json={},
            )
        )
        await session.commit()
        position_id = position.id

    response = await client.get(f"/api/tracked-positions/{position_id}/audit")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_tracked_position_alert_uses_owner_email(app, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        captured["recipient"] = recipient
        captured["template_name"] = template_name
        return "sent"

    monkeypatch.setattr("app.services.tracked_positions.service.Notifier.send_template", fake_send_template)
    monkeypatch.setattr(
        "app.services.tracked_positions.service._email_payload",
        lambda position, alert, decision: {"title": "测试提醒"},
    )

    async def fake_decision(session, position):
        return (
            AlertDecision(
                alert_type=ALERT_HARD_STOP,
                trigger_label="硬止损提醒",
                reasons=["测试触发"],
                risk_flags=[],
                advisor_summary=None,
                signal_item=None,  # type: ignore[arg-type]
                advisor_report=None,
                alert_level="urgent",
            ),
            date(2026, 6, 2),
        )

    monkeypatch.setattr(
        "app.services.tracked_positions.service.evaluate_alert_decision_v2",
        fake_decision,
    )

    async with app.state.db.session() as session:
        user = User(
            email="holder@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="holder-alert@example.com",
            is_approved=True,
            is_super_admin=False,
            smtp_host="smtp.saved.local",
            smtp_port=2525,
            smtp_username="saved-user",
            smtp_password_ref="env:SMTP_PASSWORD",
            smtp_from="FundScope <saved@example.com>",
        )
        session.add(user)
        await session.flush()
        session.add(TradableEtf(code="510300", name="沪深300ETF", exchange="SH", trading_rule_label="T+1", asset_class="ETF"))
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code="510300",
                    trade_date=date(2026, 6, 1),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1000,
                    turnover=100000,
                    pct_change=0,
                ),
                EtfPriceHistory(
                    etf_code="510300",
                    trade_date=date(2026, 6, 2),
                    open=0.9,
                    high=0.9,
                    low=0.9,
                    close=0.9,
                    volume=1000,
                    turnover=100000,
                    pct_change=-10,
                ),
            ]
        )
        position = TrackedPosition(
            user_id=user.id,
            asset_type="etf",
            asset_code="510300",
            asset_name="沪深300ETF",
            buy_date=date(2026, 6, 1),
            buy_amount=1000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=1000,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(session, position, app.state.settings)

    assert alert is not None
    assert alert.alert_type == ALERT_HARD_STOP
    assert status == "email_sent"
    assert captured["recipient"] == "holder-alert@example.com"
