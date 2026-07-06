from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfIntradayQuote,
    EtfPriceHistory,
    NotificationLog,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
)
from app.services.short_research.etf_exit_credibility import (
    EXECUTION_INTRADAY,
    CredibilityPricePoint,
    CredibilitySeries,
    generate_exit_events,
    run_etf_exit_credibility,
)
from app.services.short_research.etf_exit_policy_validation import (
    POLICY_INSURANCE_STOP,
    POLICY_PROFIT_PROTECTION,
    POLICY_TREND_GUARD,
    evidence_status_for_policy,
    kpi_summary_for_item,
    policy_class_for_signal,
    strong_conclusion_allowed,
)


def _points(values: list[float]) -> list[CredibilityPricePoint]:
    start = date(2026, 1, 1)
    return [
        CredibilityPricePoint(trade_date=start + timedelta(days=index), price=value)
        for index, value in enumerate(values)
    ]


def _series(values: list[float]) -> CredibilitySeries:
    return CredibilitySeries(
        code="560001",
        name="可信度测试ETF",
        asset_bucket="equity",
        theme_group="科技",
        points=_points(values),
    )


def test_exit_policy_classifies_signal_purpose() -> None:
    assert policy_class_for_signal("hard_stop") == POLICY_INSURANCE_STOP
    assert policy_class_for_signal("trailing_take_profit") == POLICY_PROFIT_PROTECTION
    assert policy_class_for_signal("take_profit_watch") == POLICY_PROFIT_PROTECTION
    assert policy_class_for_signal("trend_weakening") == POLICY_TREND_GUARD


def test_exit_policy_blocks_strong_conclusion_when_sample_is_insufficient() -> None:
    assert (
        evidence_status_for_policy(
            sample_count=3,
            evidence_level="样本不足",
            run_evidence_status="同源已验证",
            research_only=True,
        )
        == "sample_insufficient"
    )
    assert not strong_conclusion_allowed(
        sample_count=3,
        evidence_level="样本不足",
        run_evidence_status="同源已验证",
    )


def test_profit_protection_kpis_separate_missed_upside_from_stop_success() -> None:
    summary = kpi_summary_for_item(
        signal_type="trailing_take_profit",
        sample_count=12,
        success_avoidance_rate=0.42,
        false_stop_rate=0.1,
        sold_too_early_rate=0.33,
        avg_avoided_drawdown=-0.03,
        avg_missed_upside=0.05,
        avg_forward_return=-0.01,
    )
    assert summary["policy_class"] == POLICY_PROFIT_PROTECTION
    metric_labels = {item["label"] for item in summary["metrics"]}
    assert "浮盈保护有效率" in metric_labels
    assert "卖飞率" in metric_labels
    assert "错杀率" not in metric_labels


def test_trailing_take_profit_credibility_distinguishes_decline_and_rally() -> None:
    trailing_setup = [1.00] * 20 + [1.02, 1.04, 1.06, 1.05, 1.035]
    decline_events = generate_exit_events(
        _series(trailing_setup + [1.02, 1.00, 0.98, 0.96, 0.94, 0.93, 0.92])
    )
    decline_trailing = next(event for event in decline_events if event.signal_type == "trailing_take_profit")
    assert decline_trailing.windows[5]["outcome"] == "success_avoid_loss"

    rally_events = generate_exit_events(
        _series(trailing_setup + [1.06, 1.08, 1.10, 1.12, 1.14, 1.15, 1.16])
    )
    rally_trailing = next(event for event in rally_events if event.signal_type == "trailing_take_profit")
    assert rally_trailing.windows[5]["outcome"] == "sold_too_early"


def test_hard_stop_credibility_distinguishes_decline_and_rebound() -> None:
    decline_events = generate_exit_events(
        _series([1.00] * 18 + [0.99, 0.98, 0.96, 0.94, 0.92, 0.90, 0.88, 0.86, 0.85, 0.84, 0.83, 0.82])
    )
    decline_stop = next(event for event in decline_events if event.signal_type == "hard_stop")
    assert decline_stop.windows[5]["outcome"] == "success_avoid_loss"

    rebound_events = generate_exit_events(
        _series([1.00] * 18 + [0.99, 0.98, 0.96, 0.94, 0.97, 1.00, 1.03, 1.06, 1.08, 1.09, 1.10, 1.11])
    )
    rebound_stop = next(event for event in rebound_events if event.signal_type == "hard_stop")
    assert rebound_stop.windows[5]["outcome"] == "false_stop"


async def _seed_daily_only_etf(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="560101",
                name="日线缺盘中ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="equity",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        start = date(2026, 1, 1)
        for index in range(45):
            close = 1.0 + index * 0.01
            session.add(
                EtfPriceHistory(
                    etf_code="560101",
                    trade_date=start + timedelta(days=index),
                    open=close * 0.99,
                    high=close * 1.01,
                    low=close * 0.98,
                    close=close,
                    volume=1_000_000,
                    turnover=100_000_000,
                    pct_change=1.0,
                )
            )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 3),
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 1},
        )
        session.add(run)
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code="560101",
                rank=1,
                total_score=80.0,
                conclusion="短线观察",
                score_breakdown_json={},
                risk_flags_json=[],
                rationale_json={},
                metrics_json={
                    "opportunity_score": 80.0,
                    "catalyst_summary": "可用主题催化。",
                    "catalyst_limitations": [],
                },
            )
        )
        await session.commit()


async def _seed_intraday_etf(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="560102",
                name="盘中可信度ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="equity",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        start = date(2026, 1, 1)
        prices = [1.00] * 10 + [1.01, 1.02, 1.03, 1.04, 1.06, 1.055, 1.05, 1.04, 1.03, 1.02, 1.01, 0.99, 0.97, 0.96, 0.95]
        for index, price in enumerate([*prices, *[0.94, 0.93, 0.92, 0.91, 0.90, 0.89, 0.88, 0.87, 0.86, 0.85]]):
            current_date = start + timedelta(days=index)
            session.add(
                EtfIntradayQuote(
                    etf_code="560102",
                    quote_time=datetime.combine(current_date, datetime.min.time()) + timedelta(hours=10),
                    trade_date=current_date,
                    latest_price=price,
                    change_percent=0.0,
                    volume=1_000_000,
                    turnover=100_000_000,
                    source="test",
                    freshness_status="fresh",
                    raw_json={},
                )
            )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 3),
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": 1},
        )
        session.add(run)
        await session.flush()
        session.add(
            ShortResearchSignalItem(
                run_id=run.id,
                asset_type="etf",
                asset_code="560102",
                rank=1,
                total_score=80.0,
                conclusion="短线观察",
                score_breakdown_json={},
                risk_flags_json=[],
                rationale_json={},
                metrics_json={
                    "opportunity_score": 80.0,
                    "catalyst_summary": "可用主题催化。",
                    "catalyst_limitations": [],
                },
            )
        )
        await session.commit()


async def _seed_opportunity_ranked_intraday_etfs(app, *, include_signal_run: bool = True) -> None:
    start = date(2026, 1, 1)
    rows = [
        ("159001", "低分代码靠前ETF", 10.0, True),
        ("159002", "不可用综合关注ETF", 99.0, False),
        ("588888", "高分综合关注ETF", 95.0, True),
        ("588889", "次高综合关注ETF", 90.0, True),
    ]
    async with app.state.db.session() as session:
        for code, name, _score, _available in rows:
            session.add(
                TradableEtf(
                    code=code,
                    name=name,
                    exchange="SH",
                    theme_tags_json=["综合关注取样"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            for index in range(36):
                current_date = start + timedelta(days=index)
                price = 1.0 + index * 0.01
                session.add(
                    EtfIntradayQuote(
                        etf_code=code,
                        quote_time=datetime.combine(current_date, datetime.min.time()) + timedelta(hours=10),
                        trade_date=current_date,
                        latest_price=price,
                        change_percent=0.0,
                        volume=1_000_000,
                        turnover=100_000_000,
                        source="test",
                        freshness_status="fresh",
                        raw_json={},
                    )
                )
        if include_signal_run:
            run = ShortResearchSignalRun(
                status="success",
                as_of_date=date(2026, 7, 3),
                config_json={"asset_type": "etf", "language": "research_only"},
                summary_json={"item_count": len(rows)},
            )
            session.add(run)
            await session.flush()
            for index, (code, _name, score, available) in enumerate(rows):
                session.add(
                    ShortResearchSignalItem(
                        run_id=run.id,
                        asset_type="etf",
                        asset_code=code,
                        rank=index + 1,
                        total_score=score,
                        conclusion="短线观察",
                        score_breakdown_json={},
                        risk_flags_json=[],
                        rationale_json={},
                        metrics_json={
                            "opportunity_score": score,
                            "technical_score": score,
                            "catalyst_score": 80 if available else 50,
                            "sentiment_heat_score": 63 if available else 50,
                            "catalyst_summary": (
                                "可用主题催化。"
                                if available
                                else "暂无可用于评分的主题催化事件。"
                            ),
                            "catalyst_limitations": [] if available else ["主题催化数据不可用。"],
                        },
                    )
                )
        await session.commit()


@pytest.mark.asyncio
async def test_intraday_credibility_does_not_fall_back_to_daily_close(client, app) -> None:
    await _seed_daily_only_etf(app)

    response = await client.post(
        "/api/short-research/etf-exit-credibility/run",
        json={"days": 120, "max_assets": 20, "execution_model": "intraday_alert"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_model"] == "intraday_alert"
    assert body["summary"]["asset_count"] == 0
    assert body["summary"]["event_count"] == 0
    assert any("缺少盘中历史行情" in reason for reason in body["insufficiency_reasons"])


@pytest.mark.asyncio
async def test_credibility_uses_latest_opportunity_top_not_code_order(app) -> None:
    await _seed_opportunity_ranked_intraday_etfs(app)

    async with app.state.db.session() as session:
        run = await run_etf_exit_credibility(
            session,
            days=120,
            max_assets=2,
            execution_model=EXECUTION_INTRADAY,
            universe_scope="latest_opportunity_top",
        )

    assert run.status == "success"
    assert run.summary_json["universe_scope"] == "latest_opportunity_top"
    assert run.summary_json["ranking_sort"] == "opportunity"
    assert run.summary_json["requested_top_n"] == 2
    assert run.summary_json["selected_codes"] == ["588888", "588889"]
    assert run.summary_json["excluded_unavailable_opportunity_count"] == 1


@pytest.mark.asyncio
async def test_credibility_without_signal_run_does_not_fall_back_to_code_order(app) -> None:
    await _seed_opportunity_ranked_intraday_etfs(app, include_signal_run=False)

    async with app.state.db.session() as session:
        run = await run_etf_exit_credibility(
            session,
            days=120,
            max_assets=2,
            execution_model=EXECUTION_INTRADAY,
            universe_scope="latest_opportunity_top",
        )

    assert run.status == "failed"
    assert run.summary_json["asset_count"] == 0
    assert run.summary_json["universe_scope"] == "latest_opportunity_top"
    assert "等待信号生成" in (run.error_message or "")


@pytest.mark.asyncio
async def test_credibility_run_is_research_only_and_has_latest_api(client, app) -> None:
    await _seed_intraday_etf(app)

    async with app.state.db.session() as session:
        alert_count_before = await session.scalar(select(func.count(TrackedPositionAlert.id)))
        notification_count_before = await session.scalar(select(func.count(NotificationLog.id)))
        position_count_before = await session.scalar(select(func.count(TrackedPosition.id)))
        run = await run_etf_exit_credibility(
            session,
            days=120,
            max_assets=20,
            execution_model=EXECUTION_INTRADAY,
        )
        assert run.summary_json["no_email_sent"] is True
        assert run.summary_json["no_tracked_position_mutation"] is True
        assert await session.scalar(select(func.count(TrackedPositionAlert.id))) == alert_count_before
        assert await session.scalar(select(func.count(NotificationLog.id))) == notification_count_before
        assert await session.scalar(select(func.count(TrackedPosition.id))) == position_count_before

    latest = await client.get("/api/short-research/etf-exit-credibility/latest?execution_model=intraday_alert")
    assert latest.status_code == 200
    body = latest.json()
    assert body["id"] == run.id
    assert body["research_only"] is True
    assert body["no_trade_instruction"] is True
    assert body["items"]
