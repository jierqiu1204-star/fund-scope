from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import (
    FundNavHistory,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPositionAlert,
    User,
    utcnow,
)
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job


async def _seed_nav(app, fund_code: str = "270042") -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                FundNavHistory(
                    fund_code=fund_code,
                    nav_date=date(2026, 6, 1),
                    nav=1.5,
                    accumulated_nav=1.5,
                ),
                FundNavHistory(
                    fund_code=fund_code,
                    nav_date=date(2026, 6, 5),
                    nav=1.65,
                    accumulated_nav=1.65,
                ),
            ]
        )
        await session.commit()


async def _seed_signal(
    app,
    *,
    conclusion: str,
    risk_flags: list[str] | None = None,
    action_label: str = "退出观察",
) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 5),
            config_json={},
            summary_json={"item_count": 1},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        item = ShortResearchSignalItem(
            run_id=run.id,
            asset_type="fund",
            asset_code="270042",
            rank=1,
            total_score=50.0,
            conclusion=conclusion,
            score_breakdown_json={},
            risk_flags_json=risk_flags or [],
            rationale_json={"key_reason": "测试原因"},
            metrics_json={"return_20d": -0.03, "max_drawdown_60d": -0.2},
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        session.add(
            ShortResearchAdvisorReport(
                signal_run_id=run.id,
                signal_item_id=item.id,
                asset_type="fund",
                asset_code="270042",
                status="success",
                action_label=action_label,
                plain_summary="规则认为需要退出观察。",
                opportunity_json=["暂无机会。"],
                risks_json=risk_flags or ["回撤较大"],
                opposing_view="可能只是短期波动。",
                watch_conditions_json=["继续看回撤是否扩大。"],
                holding_note="如果已经持有，只作为人工检查提醒。",
                data_limitations="公开净值可能滞后。",
                model_name="test-model",
                prompt_version="test",
                source="fallback",
                deterministic_snapshot_json={},
                raw_response_json={},
                generated_at=utcnow(),
            )
        )
        await session.commit()
        return run.id


@pytest.mark.asyncio
async def test_create_tracked_position_estimates_shares_from_latest_nav(client, app) -> None:
    await _seed_nav(app)

    response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "fund",
            "asset_code": "270042",
            "buy_date": "2026-06-03",
            "buy_amount": 3000,
            "note": "支付宝手动买入",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset_name"] == "广发纳斯达克100ETF联接A"
    assert body["buy_amount"] == 3000
    assert body["entry_price"] == 1.5
    assert body["entry_price_date"] == "2026-06-01"
    assert body["estimated_shares"] == 2000
    assert body["current_snapshot"]["current_price"] == 1.65
    assert body["current_snapshot"]["estimated_pnl"] == 300


@pytest.mark.asyncio
async def test_high_watch_does_not_send_sell_alert(client, app, settings, monkeypatch) -> None:
    await _seed_nav(app)
    await _seed_signal(app, conclusion="高位观察", risk_flags=["追高风险"], action_label="高位别追")
    await client.post("/api/tracked-positions", json={"asset_type": "fund", "asset_code": "270042"})
    sent: list[str] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(template_name)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alert_count = len((await session.scalars(select(TrackedPositionAlert))).all())

    assert result["alerts_created"] == 0
    assert result["positions_checked"] == 1
    assert alert_count == 0
    assert sent == []


@pytest.mark.asyncio
async def test_exit_watch_sends_email_once_per_signal_day(client, app, settings, monkeypatch) -> None:
    await _seed_nav(app)
    await _seed_signal(app, conclusion="不适合短线", risk_flags=["回撤较大"], action_label="退出观察")
    await client.post("/api/tracked-positions", json={"asset_type": "fund", "asset_code": "270042"})
    sent: list[dict[str, object]] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append({"recipient": recipient, "template_name": template_name, "payload": payload})
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.recipient_email = "19535838578@163.com"
        user.smtp_host = "smtp.163.com"
        user.smtp_port = 465
        user.smtp_username = "19535838578@163.com"
        user.smtp_password_ref = "env:SMTP_PASSWORD"
        await session.commit()

        first = await daily_tracked_position_alerts_job(session, settings)
        second = await daily_tracked_position_alerts_job(session, settings)
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert first["alerts_created"] == 1
    assert first["emails_sent"] == 1
    assert second["alerts_created"] == 0
    assert second["deduplicated"] == 1
    assert len(alerts) == 1
    assert alerts[0].alert_type == "exit_watch"
    assert alerts[0].email_status == "sent"
    assert sent[0]["recipient"] == "19535838578@163.com"
    assert sent[0]["template_name"] == "tracked_position_alert.html.j2"
    assert "退出观察提醒" in sent[0]["payload"]["title"]

