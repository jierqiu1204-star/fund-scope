from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import Fund, FundNavHistory, IndexValuationHistory


async def _seed_strategy_history(app) -> None:
    start = date(2026, 1, 1)
    async with app.state.db.session() as session:
        csi_fund = await session.scalar(select(Fund).where(Fund.code == "007339"))
        sp_fund = await session.scalar(select(Fund).where(Fund.code == "001052"))
        cash_fund = await session.scalar(select(Fund).where(Fund.code == "110007"))
        assert csi_fund is not None
        assert sp_fund is not None
        assert cash_fund is not None
        csi_fund.tracking_index_code = "CSI300"
        sp_fund.tracking_index_code = "SP500"

        for offset in range(0, 91):
            current = start + timedelta(days=offset)
            session.add_all(
                [
                    FundNavHistory(
                        fund_code="007339",
                        nav_date=current,
                        nav=1.0 + offset * 0.002,
                        accumulated_nav=1.0 + offset * 0.002,
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
                        nav=1.0 + offset * 0.0001,
                        accumulated_nav=1.0 + offset * 0.0001,
                    ),
                ]
            )
            session.add_all(
                [
                    IndexValuationHistory(
                        index_code="CSI300",
                        valuation_date=current,
                        pe=12,
                        pb=1.5,
                        dividend_yield=2.0,
                        pe_percentile=50,
                        pb_percentile=50,
                        effective_window=offset + 1,
                    ),
                    IndexValuationHistory(
                        index_code="SP500",
                        valuation_date=current,
                        pe=30,
                        pb=4.5,
                        dividend_yield=1.2,
                        pe_percentile=95,
                        pb_percentile=90,
                        effective_window=offset + 1,
                    ),
                ]
            )
        await session.commit()


@pytest.mark.asyncio
async def test_strategy_lab_creates_momentum_strategy_and_runs_backtest(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "CSI momentum lab",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339", "001052"],
                "lookback_days": 30,
                "rebalance_frequency": "monthly",
                "top_n": 1,
                "max_pe_percentile": 80,
                "initial_cash": 100000,
                "fee_rate": 0.001,
            },
        },
    )

    assert create_response.status_code == 200
    strategy = create_response.json()
    assert strategy["strategy_type"] == "momentum_rotation"
    assert strategy["config"]["top_n"] == 1

    run_response = await client.post(
        f"/api/strategy-lab/strategies/{strategy['id']}/backtests",
        json={"start_date": "2026-02-01", "end_date": "2026-03-31"},
    )

    assert run_response.status_code == 200
    run = run_response.json()
    assert run["status"] == "success"
    assert run["run_type"] == "backtest"
    assert run["metrics"]["ending_equity"] > 100000
    assert run["metrics"]["max_drawdown"] <= 0
    assert run["equity_curve"]
    assert any(order["asset_code"] == "007339" for order in run["orders"])
    assert any(order["asset_name"] for order in run["orders"])
    assert any(position["asset_name"] for position in run["positions"])
    assert all(order["asset_code"] != "001052" for order in run["orders"])

    detail_response = await client.get(f"/api/strategy-lab/runs/{run['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == run["id"]


@pytest.mark.asyncio
async def test_strategy_lab_alipay_profile_records_visible_confirmation_dates(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Alipay-style CSI momentum",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339", "001052"],
                "platform_profile": "alipay",
                "lookback_days": 30,
                "top_n": 1,
                "max_pe_percentile": 80,
                "initial_cash": 100000,
                "fee_rate": 0.001,
            },
        },
    )
    assert create_response.status_code == 200

    run_response = await client.post(
        f"/api/strategy-lab/strategies/{create_response.json()['id']}/backtests",
        json={"start_date": "2026-02-01", "end_date": "2026-02-05"},
    )

    assert run_response.status_code == 200
    run = run_response.json()
    assert run["metrics"]["platform_profile"] == "alipay"
    assert run["metrics"]["execution_model"]["confirmation_lag_sessions"] == 1
    assert run["orders"][0]["platform"] == "alipay"
    assert run["orders"][0]["submitted_date"] == "2026-02-01"
    assert run["orders"][0]["trade_date"] == "2026-02-01"
    assert run["orders"][0]["confirmed_date"] == "2026-02-02"
    assert run["orders"][0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_strategy_lab_dca_backtest_generates_monthly_orders(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Monthly CSI DCA",
            "strategy_type": "dca_baseline",
            "asset_type": "fund",
            "config": {
                "target_asset_codes": ["007339"],
                "monthly_amount": 1000,
                "day_of_month": 1,
                "fee_rate": 0.001,
            },
        },
    )
    assert create_response.status_code == 200

    run_response = await client.post(
        f"/api/strategy-lab/strategies/{create_response.json()['id']}/backtests",
        json={"start_date": "2026-01-01", "end_date": "2026-03-31"},
    )

    assert run_response.status_code == 200
    run = run_response.json()
    assert run["metrics"]["contributions"] == 3000
    assert [order["trade_date"] for order in run["orders"]] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
    ]


@pytest.mark.asyncio
async def test_strategy_lab_paper_portfolio_can_start_and_update_idempotently(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Paper momentum",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339", "001052"],
                "lookback_days": 30,
                "top_n": 1,
                "max_pe_percentile": 80,
                "initial_cash": 50000,
                "fee_rate": 0.001,
            },
        },
    )
    assert create_response.status_code == 200

    paper_response = await client.post(
        f"/api/strategy-lab/strategies/{create_response.json()['id']}/paper/start",
        json={"name": "Live paper", "started_at": "2026-02-01"},
    )
    assert paper_response.status_code == 200
    paper = paper_response.json()
    assert paper["status"] == "active"
    assert paper["latest_equity"] == 50000

    first_update = await client.post(
        f"/api/strategy-lab/paper/{paper['id']}/run",
        json={"as_of_date": "2026-03-31"},
    )
    second_update = await client.post(
        f"/api/strategy-lab/paper/{paper['id']}/run",
        json={"as_of_date": "2026-03-31"},
    )

    assert first_update.status_code == 200
    assert second_update.status_code == 200
    assert first_update.json()["latest_equity"] == second_update.json()["latest_equity"]
    assert first_update.json()["latest_run"]["status"] == "success"

    list_response = await client.get("/api/strategy-lab/paper")
    assert list_response.status_code == 200
    paper_items = list_response.json()
    assert len(paper_items) == 1
    assert paper_items[0]["id"] == paper["id"]
    assert paper_items[0]["latest_run"]["status"] == "success"


@pytest.mark.asyncio
async def test_strategy_lab_paper_list_does_not_share_runs_between_portfolios(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Shared strategy paper isolation",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339", "001052"],
                "lookback_days": 30,
                "top_n": 1,
                "max_pe_percentile": 80,
                "initial_cash": 50000,
                "fee_rate": 0.001,
            },
        },
    )
    assert create_response.status_code == 200
    strategy_id = create_response.json()["id"]

    first_paper = (
        await client.post(
            f"/api/strategy-lab/strategies/{strategy_id}/paper/start",
            json={"name": "First paper", "started_at": "2026-02-01"},
        )
    ).json()
    second_paper = (
        await client.post(
            f"/api/strategy-lab/strategies/{strategy_id}/paper/start",
            json={"name": "Second paper", "started_at": "2026-03-01"},
        )
    ).json()

    update_response = await client.post(
        f"/api/strategy-lab/paper/{first_paper['id']}/run",
        json={"as_of_date": "2026-02-05"},
    )
    assert update_response.status_code == 200

    list_response = await client.get("/api/strategy-lab/paper")

    assert list_response.status_code == 200
    papers = {item["id"]: item for item in list_response.json()}
    assert papers[first_paper["id"]]["latest_run"] is not None
    assert papers[second_paper["id"]]["latest_run"] is None


@pytest.mark.asyncio
async def test_strategy_lab_reconciles_paper_portfolio_against_manual_alipay_transactions(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Alipay reconciliation",
            "strategy_type": "momentum_rotation",
            "asset_type": "fund",
            "config": {
                "asset_codes": ["007339"],
                "platform_profile": "alipay",
                "lookback_days": 30,
                "top_n": 1,
                "max_pe_percentile": 80,
                "initial_cash": 1000,
                "fee_rate": 0.001,
            },
        },
    )
    assert create_response.status_code == 200

    paper_response = await client.post(
        f"/api/strategy-lab/strategies/{create_response.json()['id']}/paper/start",
        json={"name": "Alipay paper", "started_at": "2026-02-01"},
    )
    assert paper_response.status_code == 200
    update_response = await client.post(
        f"/api/strategy-lab/paper/{paper_response.json()['id']}/run",
        json={"as_of_date": "2026-02-05"},
    )
    assert update_response.status_code == 200

    transaction_response = await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.062,
            "fee": 0.5,
            "traded_at": "2026-02-01",
        },
    )
    assert transaction_response.status_code == 201

    reconcile_response = await client.get(f"/api/strategy-lab/paper/{paper_response.json()['id']}/reconciliation")

    assert reconcile_response.status_code == 200
    body = reconcile_response.json()
    assert body["platform_profile"] == "alipay"
    assert body["paper_id"] == paper_response.json()["id"]
    assert body["as_of_date"] == "2026-02-05"
    assert body["actual_equity"] > 0
    assert body["paper_equity"] > body["actual_equity"]
    assert body["items"][0]["asset_code"] == "007339"
    assert body["items"][0]["asset_name"]
    assert body["items"][0]["share_diff"] > 0


@pytest.mark.asyncio
async def test_recommendations_latest_uses_strategy_lab_screening_compatibility(client, app) -> None:
    await _seed_strategy_history(app)

    create_response = await client.post(
        "/api/strategy-lab/strategies",
        json={
            "name": "Fund screening compatibility",
            "strategy_type": "screening",
            "asset_type": "fund",
            "config": {},
        },
    )
    assert create_response.status_code == 200

    run_response = await client.post(
        f"/api/strategy-lab/strategies/{create_response.json()['id']}/backtests",
        json={"start_date": "2026-03-31", "end_date": "2026-03-31"},
    )
    assert run_response.status_code == 200

    latest_response = await client.get("/api/recommendations/latest?asset_type=fund")

    assert latest_response.status_code == 200
    payload = latest_response.json()
    assert payload["asset_type"] == "fund"
    assert payload["run"] is not None
    assert payload["items"]
    assert "research" in payload["disclaimer"].lower()
