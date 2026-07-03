from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfIntradayQuote,
    EtfPriceHistory,
    NotificationLog,
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
