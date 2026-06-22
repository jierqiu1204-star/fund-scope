from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import (
    EtfIntradayQuote,
    EtfPriceHistory,
    FundNavHistory,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPositionAlert,
    TradableEtf,
    User,
    utcnow,
)
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job
from app.services.tracked_positions.service import (
    create_alert_if_needed,
    create_position,
    position_analysis,
)


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


async def _seed_nav_series(app, values: list[tuple[date, float]], fund_code: str = "270042") -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                FundNavHistory(
                    fund_code=fund_code,
                    nav_date=nav_date,
                    nav=nav,
                    accumulated_nav=nav,
                )
                for nav_date, nav in values
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
async def test_tracked_position_uses_latest_signal_for_same_asset_type(client, app) -> None:
    await _seed_nav(app)
    async with app.state.db.session() as session:
        fund_run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 5),
            config_json={"asset_type": "fund", "theme": None, "codes": [], "language": "research_only"},
            summary_json={"item_count": 1, "fund_count": 1, "etf_count": 0},
        )
        session.add(fund_run)
        await session.commit()
        await session.refresh(fund_run)
        session.add(
            ShortResearchSignalItem(
                run_id=fund_run.id,
                asset_type="fund",
                asset_code="270042",
                rank=1,
                total_score=50.0,
                conclusion="不适合短线",
                score_breakdown_json={},
                risk_flags_json=["回撤较大"],
                rationale_json={"key_reason": "基金 run 的原因"},
                metrics_json={},
            )
        )
        etf_run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 6),
            config_json={"asset_type": "etf", "theme": None, "codes": [], "language": "research_only"},
            summary_json={"item_count": 1, "fund_count": 0, "etf_count": 1},
        )
        session.add(etf_run)
        await session.commit()
        await session.refresh(etf_run)
        session.add(
            ShortResearchSignalItem(
                run_id=etf_run.id,
                asset_type="etf",
                asset_code="512480",
                rank=1,
                total_score=80.0,
                conclusion="短线观察",
                score_breakdown_json={},
                risk_flags_json=[],
                rationale_json={"key_reason": "ETF run 的原因"},
                metrics_json={},
            )
        )
        await session.commit()

    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-03"},
    )

    response = await client.get("/api/tracked-positions")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["asset_type"] == "fund"
    assert item["current_snapshot"]["current_label"] == "不适合短线"
    assert item["current_snapshot"]["risk_flags"] == ["回撤较大"]


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
async def test_after_15_order_uses_next_available_nav_as_confirmed_nav(client, app) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 8), 7.179),
            (date(2026, 6, 9), 7.313),
            (date(2026, 6, 10), 7.25),
        ],
        fund_code="001410",
    )

    response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "fund",
            "asset_code": "001410",
            "buy_date": "2026-06-08",
            "order_time_bucket": "after_15",
            "buy_amount": 3000,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["order_time_bucket"] == "after_15"
    assert body["confirmed_nav_date"] == "2026-06-09"
    assert body["entry_price_date"] == "2026-06-09"
    assert body["entry_price"] == 7.313
    assert body["estimated_shares"] == pytest.approx(3000 / 7.313)
    assert body["current_snapshot"]["estimated_pnl"] == pytest.approx((3000 / 7.313) * 7.25 - 3000, abs=0.01)


@pytest.mark.asyncio
async def test_confirmed_shares_override_public_nav_estimate(client, app) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 8), 7.179),
            (date(2026, 6, 9), 7.313),
            (date(2026, 6, 10), 7.25),
        ],
        fund_code="001410",
    )

    response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "fund",
            "asset_code": "001410",
            "buy_date": "2026-06-08",
            "order_time_bucket": "after_15",
            "confirmed_nav_date": "2026-06-09",
            "confirmed_nav": 7.313,
            "confirmed_shares": 409.88,
            "buy_amount": 3000,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["confirmed_nav"] == 7.313
    assert body["confirmed_shares"] == 409.88
    assert body["entry_price"] == 7.313
    assert body["estimated_shares"] == 409.88
    assert body["cost_basis"] == pytest.approx(409.88 * 7.313, abs=0.01)
    assert body["cost_basis_source"] == "confirmed_shares_entry_price"
    assert body["current_snapshot"]["estimated_pnl"] == pytest.approx(409.88 * (7.25 - 7.313), abs=0.01)


@pytest.mark.asyncio
async def test_confirmed_etf_shares_use_entry_price_cost_basis(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="513520",
                name="日经ETF",
                exchange="SH",
                theme_tags_json=["跨境"],
                trading_rule_label="T+1",
                asset_class="ETF",
            )
        )
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code="513520",
                    trade_date=date(2026, 6, 17),
                    open=2.491,
                    high=2.5,
                    low=2.45,
                    close=2.47,
                    volume=1_000_000,
                    turnover=2_470_000,
                    pct_change=-0.8,
                )
            ]
        )
        await session.commit()

    response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "etf",
            "asset_code": "513520",
            "buy_date": "2026-06-17",
            "confirmed_nav": 2.491,
            "confirmed_shares": 1300,
            "buy_amount": 3000,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["entry_price"] == 2.491
    assert body["estimated_shares"] == 1300
    assert body["cost_basis"] == pytest.approx(3238.3)
    assert body["cost_basis_source"] == "confirmed_shares_entry_price"
    assert body["current_snapshot"]["estimated_pnl"] == pytest.approx(-27.3, abs=0.01)
    assert body["current_snapshot"]["estimated_pnl_pct"] == pytest.approx(-0.84, abs=0.01)


@pytest.mark.asyncio
async def test_stale_intraday_quote_is_used_for_display_but_not_email_decision(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="513520",
                name="日经ETF",
                exchange="SH",
                theme_tags_json=["跨境"],
                trading_rule_label="T+1",
                asset_class="ETF",
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="513520",
                trade_date=date(2026, 6, 18),
                open=2.50,
                high=2.55,
                low=2.48,
                close=2.528,
                volume=1_000_000,
                turnover=2_528_000,
                pct_change=1.2,
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="513520",
                quote_time=datetime(2026, 6, 22, 11, 29),
                trade_date=date(2026, 6, 22),
                latest_price=2.586,
                change_percent=2.0,
                turnover=5_000_000,
                source="akshare",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

    await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "etf",
            "asset_code": "513520",
            "buy_date": "2026-06-17",
            "confirmed_nav": 2.491,
            "confirmed_shares": 1300,
            "buy_amount": 3000,
        },
    )

    response = await client.get("/api/tracked-positions")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["current_snapshot"]["current_price"] == 2.586
    assert item["current_snapshot"]["current_price_date"] == "2026-06-22"
    assert item["current_snapshot"]["estimated_pnl"] == pytest.approx(1300 * (2.586 - 2.491), abs=0.01)
    assert item["current_snapshot"]["price_source"] == "intraday_quote"
    assert item["current_snapshot"]["decision_eligible"] is False
    assert item["intraday_snapshot"]["price_source"] == "intraday_quote"
    assert item["intraday_snapshot"]["reliability_level"] == "stale_quote"
    assert item["intraday_snapshot"]["email_eligible"] is False

@pytest.mark.asyncio
async def test_tracking_chart_and_holding_days_start_from_confirmed_nav_date(client, app) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 8), 1.0),
            (date(2026, 6, 9), 2.0),
            (date(2026, 6, 10), 2.2),
        ],
    )

    create_response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "fund",
            "asset_code": "270042",
            "buy_date": "2026-06-08",
            "order_time_bucket": "after_15",
            "buy_amount": 3000,
        },
    )
    position_id = create_response.json()["id"]

    response = await client.get(f"/api/tracked-positions/{position_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["holding_days"] == 1
    assert [point["date"] for point in body["chart"]] == ["2026-06-09", "2026-06-10"]
    assert body["chart"][0]["is_entry"] is True


@pytest.mark.asyncio
async def test_fund_dynamic_thresholds_use_daily_nav_behavior(app) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 1), 1.00),
            (date(2026, 6, 2), 1.04),
            (date(2026, 6, 3), 1.01),
            (date(2026, 6, 4), 1.07),
            (date(2026, 6, 5), 1.02),
            (date(2026, 6, 6), 1.10),
            (date(2026, 6, 7), 1.05),
            (date(2026, 6, 8), 1.13),
            (date(2026, 6, 9), 1.08),
            (date(2026, 6, 10), 1.12),
        ],
        fund_code="001410",
    )

    async with app.state.db.session() as session:
        position = await create_position(
            session,
            asset_type="fund",
            asset_code="001410",
            user_id=1,
            buy_amount=3000,
            buy_date=date(2026, 6, 1),
        )
        analysis = await position_analysis(session, position)

    assert analysis.dynamic_thresholds is not None
    assert analysis.dynamic_thresholds.hard_stop_pct is not None
    assert analysis.dynamic_thresholds.hard_stop_pct < -4.0
    assert analysis.dynamic_thresholds.profit_start_pct is not None
    assert analysis.dynamic_thresholds.rule_version == "dynamic_exit_v2"
    assert analysis.dynamic_thresholds.distance_to_hard_stop_pct is not None
    assert analysis.dynamic_thresholds.explanation
    assert analysis.technical_metrics["threshold_rule_version"] == "dynamic_exit_v2"
    assert analysis.technical_metrics["distance_to_hard_stop_pct"] is not None
    assert analysis.technical_metrics["price_source"] == "daily_close"


@pytest.mark.asyncio
async def test_ranking_label_and_holding_exit_signal_are_separate(client, app) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 1), 1.00),
            (date(2026, 6, 2), 1.03),
            (date(2026, 6, 3), 1.06),
            (date(2026, 6, 4), 1.09),
            (date(2026, 6, 5), 1.11),
            (date(2026, 6, 6), 1.08),
            (date(2026, 6, 7), 1.06),
        ],
    )
    await _seed_signal(app, conclusion="短线观察", risk_flags=[], action_label="重点观察")

    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )
    response = await client.get("/api/tracked-positions")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["current_snapshot"]["current_label"] == "短线观察"
    assert item["exit_signal"]["alert_type"] == "trailing_take_profit"
    assert item["exit_signal"]["level"] == "warning"


@pytest.mark.asyncio
async def test_high_watch_without_profit_does_not_send_sell_alert(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.5), (date(2026, 6, 5), 1.53)])
    await _seed_signal(app, conclusion="高位观察", risk_flags=["追高风险"], action_label="高位别追")
    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )
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
async def test_take_profit_watch_sends_soft_email_for_profitable_high_watch(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.0), (date(2026, 6, 5), 1.04)])
    await _seed_signal(app, conclusion="高位观察", risk_flags=["追高风险"], action_label="高位别追")
    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )
    sent: list[dict[str, object]] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.recipient_email = "19535838578@163.com"
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert result["alerts_created"] == 1
    assert result.get("web_only", 0) == 0
    assert result["emails_sent"] == 1
    assert alerts[0].alert_type == "take_profit_watch"
    assert alerts[0].email_status == "sent"
    assert alerts[0].trigger_label == "止盈观察提醒"
    assert any("盈利" in reason for reason in alerts[0].reasons_json)
    assert sent
    assert "止盈观察提醒" in str(sent[0]["title"])


@pytest.mark.asyncio
async def test_trailing_take_profit_triggers_after_profit_giveback(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 6, 1), 1.0),
            (date(2026, 6, 2), 1.06),
            (date(2026, 6, 3), 1.08),
            (date(2026, 6, 5), 1.053),
        ],
    )
    await _seed_signal(app, conclusion="短线观察", risk_flags=[], action_label="重点观察")
    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alert = await session.scalar(select(TrackedPositionAlert))

    assert result["alerts_created"] == 1
    assert alert is not None
    assert alert.alert_type == "trailing_take_profit"
    assert any("回吐" in reason for reason in alert.reasons_json)


@pytest.mark.asyncio
async def test_trend_weakening_triggers_when_price_breaks_short_averages(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(
        app,
        [
            (date(2026, 5, 22), 1.0),
            (date(2026, 5, 25), 1.035),
            (date(2026, 5, 26), 1.04),
            (date(2026, 5, 27), 1.04),
            (date(2026, 5, 28), 1.035),
            (date(2026, 5, 29), 1.03),
            (date(2026, 6, 1), 1.025),
            (date(2026, 6, 2), 1.02),
            (date(2026, 6, 3), 1.01),
            (date(2026, 6, 4), 1.0),
            (date(2026, 6, 5), 0.99),
        ],
    )
    await _seed_signal(app, conclusion="短线观察", risk_flags=[], action_label="重点观察")
    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-05-22"},
    )

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alert = await session.scalar(select(TrackedPositionAlert))

    assert result["alerts_created"] == 1
    assert alert is not None
    assert alert.alert_type == "trend_weakening"
    assert any("均线" in reason for reason in alert.reasons_json)


@pytest.mark.asyncio
async def test_hard_stop_triggers_at_four_percent_loss(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.0), (date(2026, 6, 5), 0.95)])
    await _seed_signal(app, conclusion="短线观察", risk_flags=[], action_label="重点观察")
    await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alert = await session.scalar(select(TrackedPositionAlert))

    assert result["alerts_created"] == 1
    assert alert is not None
    assert alert.alert_type == "hard_stop"
    assert any("亏损" in reason for reason in alert.reasons_json)


@pytest.mark.asyncio
async def test_tracked_position_hard_stop_without_latest_signal_item(app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.0), (date(2026, 6, 5), 0.94)])
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=date(2026, 6, 5),
            config_json={"asset_type": "fund"},
            summary_json={"item_count": 1},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="fund",
                asset_code="999999",
                rank=1,
                total_score=80,
                conclusion="短线观察",
                score_breakdown_json={},
                risk_flags_json=[],
                rationale_json={},
                metrics_json={},
            )
        )
        position = await create_position(
            session,
            asset_type="fund",
            asset_code="270042",
            user_id=1,
            buy_amount=3000,
            buy_date=date(2026, 6, 1),
        )

        async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
            return "sent"

        monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        alert, status = await create_alert_if_needed(session, position, settings)

    assert status == "email_sent"
    assert alert is not None
    assert alert.alert_type == "hard_stop"
    assert alert.trigger_label == "硬止损提醒"


@pytest.mark.asyncio
async def test_take_profit_watch_uses_three_day_cooldown(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.0), (date(2026, 6, 5), 1.04)])
    await _seed_signal(app, conclusion="高位观察", risk_flags=["追高风险"], action_label="高位别追")
    response = await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )
    position_id = response.json()["id"]

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        session.add(
            TrackedPositionAlert(
                tracked_position_id=position_id,
                alert_date=date(2026, 6, 5) - timedelta(days=2),
                alert_type="take_profit_watch",
                trigger_label="止盈观察",
                reasons_json=["旧提醒"],
                risk_flags_json=[],
                email_status="sent",
            )
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert result["alerts_created"] == 0
    assert result["deduplicated"] == 1
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_take_profit_watch_cooldown_ignores_non_sent_history(client, app, settings, monkeypatch) -> None:
    await _seed_nav_series(app, [(date(2026, 6, 1), 1.0), (date(2026, 6, 5), 1.04)])
    await _seed_signal(app, conclusion="高位观察", risk_flags=["追高风险"], action_label="高位别追")
    response = await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042", "buy_date": "2026-06-01"},
    )
    position_id = response.json()["id"]
    sent: list[dict[str, object]] = []

    async def fake_send_template(self, session, *, recipient: str, template_name: str, payload: dict) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        session.add_all(
            [
                TrackedPositionAlert(
                    tracked_position_id=position_id,
                    alert_date=date(2026, 6, 3),
                    alert_type="take_profit_watch",
                    trigger_label="止盈观察",
                    reasons_json=["网页提示"],
                    risk_flags_json=[],
                    suppression_status="web_only",
                    email_status="skipped",
                ),
                TrackedPositionAlert(
                    tracked_position_id=position_id,
                    alert_date=date(2026, 6, 4),
                    alert_type="take_profit_watch",
                    trigger_label="止盈观察",
                    reasons_json=["去重"],
                    risk_flags_json=[],
                    suppression_status="suppressed",
                    email_status="skipped",
                ),
                TrackedPositionAlert(
                    tracked_position_id=position_id,
                    alert_date=date(2026, 6, 4),
                    alert_type="take_profit_watch",
                    trigger_label="止盈观察",
                    reasons_json=["失败"],
                    risk_flags_json=[],
                    email_status="failed",
                ),
            ]
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        result = await daily_tracked_position_alerts_job(session, settings)
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert result["alerts_created"] == 1
    assert result["emails_sent"] == 1
    assert len(alerts) == 4
    assert sent


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
    assert "卖出/减仓提醒" in sent[0]["payload"]["title"]
