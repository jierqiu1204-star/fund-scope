from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    Fund,
    FundNavHistory,
    StrategyEvaluation,
    StrategyEvaluationItem,
    StrategyRun,
)


async def _seed_evaluation_history(app, *, days: int = 210) -> None:
    start = date(2025, 1, 1)
    codes = ["007339", "001052", "110007"]
    async with app.state.db.session() as session:
        funds = (await session.scalars(select(Fund).where(Fund.code.in_(codes)))).all()
        assert len(funds) == len(codes)
        for offset in range(days):
            current = start + timedelta(days=offset)
            session.add_all(
                [
                    FundNavHistory(
                        fund_code="007339",
                        nav_date=current,
                        nav=1.0 + offset * 0.0015,
                        accumulated_nav=1.0 + offset * 0.0015,
                    ),
                    FundNavHistory(
                        fund_code="001052",
                        nav_date=current,
                        nav=1.0 + offset * 0.001,
                        accumulated_nav=1.0 + offset * 0.001,
                    ),
                    FundNavHistory(
                        fund_code="110007",
                        nav_date=current,
                        nav=1.0 + offset * 0.0002,
                        accumulated_nav=1.0 + offset * 0.0002,
                    ),
                ]
            )
        await session.commit()


async def _remove_one_nav_date(app) -> None:
    async with app.state.db.session() as session:
        row = await session.scalar(
            select(FundNavHistory).where(
                FundNavHistory.fund_code == "001052",
                FundNavHistory.nav_date == date(2025, 4, 1),
            )
        )
        assert row is not None
        await session.delete(row)
        await session.commit()


async def _create_default_like_strategy(client) -> dict:
    response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Evaluation default momentum",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339", "001052", "110007"],
                "platform_profile": "alipay",
                "lookback_days": 60,
                "rebalance_frequency": "monthly",
                "top_n": 3,
                "max_pe_percentile": 80,
                "initial_cash": 100000,
                "fee_rate": 0.001,
            },
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_strategy_evaluation_persists_research_results_without_strategy_runs(client, app) -> None:
    await _seed_evaluation_history(app)
    strategy = await _create_default_like_strategy(client)

    response = await client.post(
        "/api/strategy-lab/evaluations",
        json={
            "strategy_id": strategy["id"],
            "start_date": "2025-03-15",
            "end_date": "2025-07-29",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == strategy["id"]
    assert payload["status"] == "success"
    assert payload["conclusion"] == "样本不足，继续观察"
    assert payload["data_coverage"]["is_sample_sufficient"] is False
    assert "样本不足" in payload["risk_flags"]
    assert len(payload["items"]) == 39
    assert {item["item_type"] for item in payload["items"]} >= {
        "parameter_grid",
        "baseline_equal_weight",
        "baseline_dca",
    }
    assert payload["summary"]["parameter_grid_count"] == 36
    assert "默认策略" in payload["summary"]["baseline_names"]
    assert payload["summary"]["execution_assumptions"] == {
        "fee_rate": 0.001,
        "slippage_rate": 0.0,
        "slippage_model": "not_applied",
    }
    assert payload["summary"]["benchmark_names"] == ["默认策略", "等权买入持有", "定投对照"]
    assert payload["summary"]["turnover_summary"]["default_trade_count"] > 0
    assert payload["summary"]["turnover_summary"]["median_parameter_turnover_rate"] >= 0
    assert payload["summary"]["out_of_sample_comparison"]["best_parameter_label"]
    assert "overfit_warning" in payload["summary"]["out_of_sample_comparison"]
    assert all("turnover_rate" in item["metrics"] for item in payload["items"])
    assert all("fee_drag" in item["metrics"] for item in payload["items"])

    async with app.state.db.session() as session:
        assert await session.scalar(select(func.count()).select_from(StrategyRun)) == 0
        assert await session.scalar(select(func.count()).select_from(StrategyEvaluation)) == 1
        assert await session.scalar(select(func.count()).select_from(StrategyEvaluationItem)) == len(payload["items"])


@pytest.mark.asyncio
async def test_strategy_evaluation_list_and_detail_return_saved_result(client, app) -> None:
    await _seed_evaluation_history(app)
    strategy = await _create_default_like_strategy(client)
    create_response = await client.post(
        "/api/strategy-lab/evaluations",
        json={
            "strategy_id": strategy["id"],
            "start_date": "2025-03-15",
            "end_date": "2025-07-29",
        },
    )
    assert create_response.status_code == 200
    evaluation_id = create_response.json()["id"]

    list_response = await client.get("/api/strategy-lab/evaluations")
    detail_response = await client.get(f"/api/strategy-lab/evaluations/{evaluation_id}")

    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == evaluation_id
    assert list_response.json()[0]["items"] == []
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == evaluation_id
    assert len(detail["items"]) == 39
    assert detail["items"][0]["metrics"]["ending_equity"] > 0


@pytest.mark.asyncio
async def test_strategy_evaluation_rejects_missing_nav_history(client, app) -> None:
    strategy = await _create_default_like_strategy(client)

    response = await client.post(
        "/api/strategy-lab/evaluations",
        json={
            "strategy_id": strategy["id"],
            "start_date": "2025-03-15",
            "end_date": "2025-07-29",
        },
    )

    assert response.status_code == 422
    assert "没有足够的基金净值" in response.json()["detail"]


@pytest.mark.asyncio
async def test_strategy_evaluation_handles_baseline_funds_with_missing_nav_dates(client, app) -> None:
    await _seed_evaluation_history(app)
    await _remove_one_nav_date(app)
    strategy = await _create_default_like_strategy(client)

    response = await client.post(
        "/api/strategy-lab/evaluations",
        json={
            "strategy_id": strategy["id"],
            "start_date": "2025-03-15",
            "end_date": "2025-07-29",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["baseline_names"] == ["默认策略", "等权买入持有", "定投对照"]
