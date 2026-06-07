from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import (
    RecommendationItem,
    RecommendationProfile,
    RecommendationReview,
    RecommendationReviewItem,
    RecommendationRun,
    StrategyDefinition,
    StrategyEvaluation,
)


async def _seed_recommendation_run(app) -> tuple[int, list[int]]:
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
        run = RecommendationRun(
            profile_id=profile.id,
            asset_type="fund",
            status="success",
            as_of_date=date(2026, 6, 4),
            started_at=datetime(2026, 6, 4, 19, 40),
            finished_at=datetime(2026, 6, 4, 19, 41),
            data_cutoff_json={},
            details_json={},
        )
        session.add(run)
        await session.flush()
        first = RecommendationItem(
            run_id=run.id,
            asset_code="007339",
            asset_name="易方达沪深300ETF联接A",
            asset_type="fund",
            rank=1,
            total_score=84.5,
            score_breakdown_json={"risk_control": {"score": 72.0}, "valuation_fit": {"score": 80.0}},
            rationale_json={"summary": "规则评分靠前。"},
            risk_flags_json=[],
            data_freshness_json={"missing_metrics": []},
        )
        second = RecommendationItem(
            run_id=run.id,
            asset_code="001052",
            asset_name="华夏中证500ETF联接A",
            asset_type="fund",
            rank=2,
            total_score=68.0,
            score_breakdown_json={"risk_control": {"score": 42.0}, "valuation_fit": {"score": 62.0}},
            rationale_json={"summary": "规则评分居中。"},
            risk_flags_json=["large_drawdown"],
            data_freshness_json={"missing_metrics": []},
        )
        session.add_all([first, second])
        await session.commit()
        return run.id, [first.id, second.id]


async def _seed_sample_insufficient_evaluation(app) -> None:
    async with app.state.db.session() as session:
        strategy = StrategyDefinition(
            name="默认基金轮动策略",
            strategy_type="momentum_rotation",
            asset_type="fund",
            status="active",
            config_json={},
        )
        session.add(strategy)
        await session.flush()
        session.add(
            StrategyEvaluation(
                strategy_id=strategy.id,
                status="success",
                started_at=datetime(2026, 6, 4, 20, 0),
                finished_at=datetime(2026, 6, 4, 20, 1),
                start_date=date(2025, 6, 3),
                end_date=date(2026, 6, 4),
                data_coverage_json={"is_sample_sufficient": False, "available_days": 367, "required_days": 730},
                summary_json={},
                conclusion="样本不足，继续观察",
                risk_flags_json=["样本不足", "风险偏高"],
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_recommendation_review_generates_rule_based_agent_report_without_changing_ranking(client, app) -> None:
    run_id, item_ids = await _seed_recommendation_run(app)
    await _seed_sample_insufficient_evaluation(app)

    response = await client.post(f"/api/recommendations/runs/{run_id}/review")

    assert response.status_code == 200
    review = response.json()
    assert review["run_id"] == run_id
    assert review["status"] == "success"
    assert review["model_name"] == "rules-v1"
    assert review["summary"]["ranking_policy"] == "保持原推荐评分排序，审查只做解释和风控，不改排名。"
    assert review["summary"]["strategy_evaluation"]["conclusion"] == "样本不足，继续观察"
    assert [item["recommendation_item_id"] for item in review["items"]] == item_ids
    assert [item["asset_code"] for item in review["items"]] == ["007339", "001052"]
    assert all("数据员" in item["agent_notes"] for item in review["items"])
    assert all("反方" in item["agent_notes"] for item in review["items"])
    assert all(item["verdict"] in {"可观察", "谨慎", "样本不足", "不建议采用"} for item in review["items"])
    assert review["items"][0]["verdict"] == "样本不足"
    assert review["items"][1]["verdict"] == "样本不足"

    latest_response = await client.get("/api/recommendations/latest?asset_type=fund")
    assert latest_response.status_code == 200
    assert [item["asset_code"] for item in latest_response.json()["items"]] == ["007339", "001052"]


@pytest.mark.asyncio
async def test_recommendation_review_is_idempotent_for_same_run(client, app) -> None:
    run_id, _ = await _seed_recommendation_run(app)

    first_response = await client.post(f"/api/recommendations/runs/{run_id}/review")
    second_response = await client.post(f"/api/recommendations/runs/{run_id}/review")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["id"] == second_response.json()["id"]
    async with app.state.db.session() as session:
        assert len((await session.scalars(select(RecommendationReview))).all()) == 1
        assert len((await session.scalars(select(RecommendationReviewItem))).all()) == 2


@pytest.mark.asyncio
async def test_latest_with_review_returns_latest_run_and_saved_review(client, app) -> None:
    run_id, _ = await _seed_recommendation_run(app)
    create_response = await client.post(f"/api/recommendations/runs/{run_id}/review")
    assert create_response.status_code == 200

    response = await client.get("/api/recommendations/latest-with-review?asset_type=fund")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["id"] == run_id
    assert [item["asset_code"] for item in payload["items"]] == ["007339", "001052"]
    assert payload["review"]["id"] == create_response.json()["id"]
    assert payload["review"]["items"][0]["asset_code"] == "007339"


@pytest.mark.asyncio
async def test_recommendation_review_rejects_stock_runs_for_v1(client, app) -> None:
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
        run = RecommendationRun(
            profile_id=profile.id,
            asset_type="stock",
            status="success",
            as_of_date=date(2026, 6, 4),
            started_at=datetime(2026, 6, 4, 19, 40),
            finished_at=datetime(2026, 6, 4, 19, 41),
            data_cutoff_json={},
            details_json={},
        )
        session.add(run)
        await session.commit()

    response = await client.post(f"/api/recommendations/runs/{run.id}/review")

    assert response.status_code == 422
    assert "第一版只支持基金" in response.json()["detail"]
