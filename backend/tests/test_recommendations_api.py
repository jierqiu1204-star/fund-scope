from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import (
    FundMetric,
    RecommendationItem,
    RecommendationProfile,
    RecommendationRun,
    Stock,
    StockFundamental,
    StockMetric,
    StockPriceHistory,
)
from app.services.jobs import daily_asset_recommendations_job, daily_recommendation_metrics_job
from app.services.recommendations import engine
from app.services.recommendations.engine import generate_recommendations


@pytest.mark.asyncio
async def test_latest_recommendations_empty_state_has_disclaimer(client) -> None:
    response = await client.get("/api/recommendations/latest?asset_type=fund")

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset_type"] == "fund"
    assert payload["run"] is None
    assert payload["items"] == []
    assert "research" in payload["disclaimer"].lower()


@pytest.mark.asyncio
async def test_latest_recommendations_returns_latest_successful_run(client, app) -> None:
    async with app.state.db.session() as session:
        profile = RecommendationProfile(
            name="Fund default",
            asset_type="fund",
            risk_level="balanced",
            is_default=True,
            config_json={},
        )
        session.add(profile)
        await session.flush()
        old_run = RecommendationRun(
            profile_id=profile.id,
            asset_type="fund",
            status="success",
            as_of_date=date(2026, 5, 1),
            started_at=datetime(2026, 5, 1, 19, 40),
            finished_at=datetime(2026, 5, 1, 19, 41),
            data_cutoff_json={},
            details_json={},
        )
        new_run = RecommendationRun(
            profile_id=profile.id,
            asset_type="fund",
            status="success",
            as_of_date=date(2026, 5, 2),
            started_at=datetime(2026, 5, 2, 19, 40),
            finished_at=datetime(2026, 5, 2, 19, 41),
            data_cutoff_json={},
            details_json={},
        )
        session.add_all([old_run, new_run])
        await session.flush()
        session.add(
            RecommendationItem(
                run_id=new_run.id,
                asset_code="007339",
                asset_name="E Fund CSI 300",
                asset_type="fund",
                rank=1,
                total_score=82.5,
                score_breakdown_json={"valuation_fit": {"score": 90.0}},
                rationale_json={"summary": "估值较低，组合缺口明显。"},
                risk_flags_json=[],
                data_freshness_json={"missing_metrics": []},
            )
        )
        await session.commit()

    response = await client.get("/api/recommendations/latest?asset_type=fund")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["id"] == new_run.id
    assert payload["items"][0]["asset_code"] == "007339"
    assert "target_price" not in payload["items"][0]
    assert "expected_return" not in payload["items"][0]


@pytest.mark.asyncio
async def test_recommendation_run_history_is_newest_first(client, app) -> None:
    async with app.state.db.session() as session:
        profile = RecommendationProfile(
            name="Stock default",
            asset_type="stock",
            risk_level="balanced",
            is_default=True,
            config_json={},
        )
        session.add(profile)
        await session.flush()
        session.add_all(
            [
                RecommendationRun(
                    profile_id=profile.id,
                    asset_type="stock",
                    status="success",
                    as_of_date=date(2026, 5, 1),
                    started_at=datetime(2026, 5, 1, 20, 0),
                    finished_at=datetime(2026, 5, 1, 20, 1),
                    data_cutoff_json={},
                    details_json={},
                ),
                RecommendationRun(
                    profile_id=profile.id,
                    asset_type="stock",
                    status="failed",
                    as_of_date=date(2026, 5, 2),
                    started_at=datetime(2026, 5, 2, 20, 0),
                    finished_at=datetime(2026, 5, 2, 20, 1),
                    data_cutoff_json={},
                    details_json={},
                    error_message="boom",
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/recommendations/runs?asset_type=stock")

    assert response.status_code == 200
    runs = response.json()
    assert [run["status"] for run in runs] == ["failed", "success"]


@pytest.mark.asyncio
async def test_metric_and_recommendation_jobs_persist_items(client, app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                RecommendationProfile(
                    name="Fund default",
                    asset_type="fund",
                    risk_level="balanced",
                    is_default=True,
                    config_json={},
                ),
                RecommendationProfile(
                    name="Stock default",
                    asset_type="stock",
                    risk_level="balanced",
                    is_default=True,
                    config_json={},
                ),
                Stock(code="600519.SH", exchange="SH", name="Kweichow Moutai", industry="consumer", is_candidate=True),
                StockPriceHistory(
                    stock_code="600519.SH",
                    trade_date=date.today() - timedelta(days=180),
                    open=100,
                    high=100,
                    low=100,
                    close=100,
                    volume=1000,
                    turnover=100_000,
                ),
                StockPriceHistory(
                    stock_code="600519.SH",
                    trade_date=date.today(),
                    open=120,
                    high=122,
                    low=118,
                    close=120,
                    volume=2000,
                    turnover=240_000,
                ),
                StockFundamental(
                    stock_code="600519.SH",
                    report_date=date.today(),
                    pe=24,
                    pb=6,
                    roe=28,
                    gross_margin=90,
                    debt_to_asset=18,
                    operating_cashflow=1000,
                    dividend_yield=2.0,
                ),
            ]
        )
        await session.commit()

        metric_result = await daily_recommendation_metrics_job(session)
        run_result = await daily_asset_recommendations_job(session)

        fund_metrics = (await session.scalars(select(FundMetric))).all()
        stock_metrics = (await session.scalars(select(StockMetric))).all()
        items = (await session.scalars(select(RecommendationItem))).all()

    assert metric_result["fund_metrics"] >= 1
    assert metric_result["stock_metrics"] >= 1
    assert run_result["fund_items"] >= 1
    assert run_result["stock_items"] >= 1
    assert fund_metrics
    assert stock_metrics
    assert items


@pytest.mark.asyncio
async def test_failed_recommendation_run_is_persisted(monkeypatch: pytest.MonkeyPatch, app) -> None:
    async def fail_scores(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("metric source unavailable")

    monkeypatch.setattr(engine, "_fund_scores", fail_scores)

    async with app.state.db.session() as session:
        session.add(
            RecommendationProfile(
                name="Fund default",
                asset_type="fund",
                risk_level="balanced",
                is_default=True,
                config_json={},
            )
        )
        await session.commit()

        with pytest.raises(RuntimeError):
            await generate_recommendations(session, "fund")

        run = await session.scalar(select(RecommendationRun).where(RecommendationRun.asset_type == "fund"))

    assert run is not None
    assert run.status == "failed"
    assert run.error_message == "metric source unavailable"
