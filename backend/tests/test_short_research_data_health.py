from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.api.routes import short_research as short_research_routes
from app.models.entities import EtfDataHealth, EtfPriceHistory, JobRun, TradableEtf


@pytest.mark.asyncio
async def test_data_health_separates_raw_and_research_etf_prices(client, app) -> None:
    today = date.today()
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="561991",
                name="数据健康测试ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code="561991",
                    trade_date=today - timedelta(days=1),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=0.0,
                    research_adjusted_value=1.0,
                    research_price_basis="total_return_adjusted",
                    data_provider="fixture",
                    provider_version="fixture-v1",
                    source_timestamp=datetime.combine(today - timedelta(days=1), datetime.min.time()),
                    adjustment_version="fixture-v1",
                    decision_eligible=True,
                ),
                EtfPriceHistory(
                    etf_code="561991",
                    trade_date=today,
                    open=1.1,
                    high=1.1,
                    low=1.1,
                    close=1.1,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=10.0,
                    raw_price_basis="raw_ohlc",
                    data_provider="fixture",
                    provider_version="fixture-v1",
                    source_timestamp=datetime.combine(today, datetime.min.time()),
                    decision_eligible=False,
                    decision_ineligibility_reason="missing_total_return_provenance",
                ),
                EtfDataHealth(
                    etf_code="561991",
                    status="success",
                    provider="fixture",
                    latest_price_date=today,
                    successful_rows=2,
                ),
                JobRun(
                    job_name="daily_short_research_data",
                    status="partial",
                    started_at=datetime.combine(today, datetime.min.time()),
                    finished_at=datetime.combine(today, datetime.min.time()),
                    details_json={"etf": {"etfs": {"skipped": 3}}},
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/short-research/status?include_health=true")

    assert response.status_code == 200
    health = next(item for item in response.json()["data_health"] if item["code"] == "561991")
    assert health["raw_latest_date"] == today.isoformat()
    assert health["research_latest_date"] == (today - timedelta(days=1)).isoformat()
    assert health["research_price_basis"] == "total_return_adjusted"
    assert health["provider"] == "fixture"
    assert health["provider_version"] == "fixture-v1"
    assert health["decision_eligible"] is False
    assert health["sync_state"] == "deferred"
    assert "missing_total_return_provenance" in health["issue_details"]


@pytest.mark.asyncio
async def test_data_issues_endpoint_filters_and_bounds_health_rows(
    client,
    monkeypatch,
) -> None:
    async def fake_data_health(_session, *, asset_type=None):
        assert asset_type == "etf"
        return [
            {
                "asset_type": "etf",
                "code": "510001",
                "name": "Issue ETF 1",
                "status": "stale",
                "usable_days": 61,
                "source_note": "fixture",
                "is_stale": True,
            },
            {
                "asset_type": "etf",
                "code": "510002",
                "name": "Healthy ETF",
                "status": "success",
                "usable_days": 61,
                "source_note": "fixture",
                "is_stale": False,
            },
            {
                "asset_type": "etf",
                "code": "510003",
                "name": "Issue ETF 2",
                "status": "failed",
                "usable_days": 0,
                "source_note": "fixture",
                "is_stale": True,
            },
        ]

    monkeypatch.setattr(short_research_routes, "data_health", fake_data_health)

    response = await client.get(
        "/api/short-research/status/data-issues?asset_type=etf&limit=1"
    )

    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == ["510001"]
