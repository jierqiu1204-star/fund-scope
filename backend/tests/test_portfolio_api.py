from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, update

from app.defaults.funds import DEFAULT_RESEARCH_FUNDS
from app.models.entities import Fund, FundNavHistory, HoldingsSnapshot, Transaction
from app.services.jobs import daily_holdings_snapshot_job


@pytest.mark.asyncio
async def test_record_successful_buy_transaction(client, app) -> None:
    response = await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.2345,
            "fee": 1.5,
            "traded_at": "2026-05-01",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["fund_code"] == "007339"
    assert data["shares"] == pytest.approx((500 - 1.5) / 1.2345, rel=1e-6)

    async with app.state.db.session() as session:
        transactions = (await session.execute(Transaction.__table__.select())).all()

    assert len(transactions) == 1


@pytest.mark.asyncio
async def test_reject_unknown_fund_code(client) -> None:
    response = await client.post(
        "/api/transactions",
        json={
            "fund_code": "999999",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.2,
            "fee": 0,
            "traded_at": "2026-05-01",
        },
    )

    assert response.status_code == 422
    assert "watchlist" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_record_sell_transaction_reduces_holdings(client) -> None:
    buy = await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.25,
            "fee": 0,
            "traded_at": "2026-05-01",
        },
    )
    assert buy.status_code == 201

    sell = await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "sell",
            "shares": 100,
            "nav_at_trade": 1.35,
            "fee": 0,
            "traded_at": "2026-05-10",
        },
    )

    assert sell.status_code == 201

    holdings = await client.get("/api/portfolio/holdings")
    body = holdings.json()
    assert body["items"][0]["shares"] == pytest.approx(300)


@pytest.mark.asyncio
async def test_new_user_portfolio_sources_are_empty(client) -> None:
    holdings = await client.get("/api/portfolio/holdings")
    history = await client.get("/api/portfolio/value-history")

    assert holdings.status_code == 200
    assert holdings.json() == {"items": []}
    assert history.status_code == 200
    assert history.json() == []


@pytest.mark.asyncio
async def test_csv_import_is_atomic_and_reports_row_errors(client) -> None:
    files = {
        "file": (
            "transactions.csv",
            "fund_code,action,amount_or_shares,nav,fee,traded_at\n007339,buy,500,1.2,1,2026-05-01\n999999,buy,500,1.1,0,2026-05-02\n",
            "text/csv",
        )
    }
    response = await client.post("/api/transactions/import-csv", files=files)

    assert response.status_code == 422
    payload = response.json()
    assert payload["inserted"] == 0
    assert payload["errors"][0]["row"] == 3

    listing = await client.get("/api/transactions")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_csv_import_creates_all_valid_rows_in_one_transaction(client) -> None:
    files = {
        "file": (
            "transactions.csv",
            "fund_code,action,amount_or_shares,nav,fee,traded_at\n"
            "007339,buy,500,1.25,0,2026-05-01\n"
            "001052,buy,300,1.50,1,2026-05-02\n",
            "text/csv",
        )
    }

    response = await client.post("/api/transactions/import-csv", files=files)

    assert response.status_code == 200
    assert response.json() == {"inserted": 2, "errors": []}

    listing = await client.get("/api/transactions")
    payload = listing.json()
    assert payload["total"] == 2
    assert {item["fund_code"] for item in payload["items"]} == {"007339", "001052"}


@pytest.mark.asyncio
async def test_alipay_csv_import_accepts_chinese_fund_transaction_columns(client) -> None:
    files = {
        "file": (
            "alipay-transactions.csv",
            "基金代码,交易类型,金额,份额,成交净值,手续费,交易日期\n"
            "007339,买入,500,,1.25,0.5,2026/05/01\n"
            "007339,卖出,,100,1.30,0.2,2026/05/10\n",
            "text/csv",
        )
    }

    response = await client.post("/api/transactions/import-alipay-csv", files=files)

    assert response.status_code == 200
    assert response.json() == {"inserted": 2, "errors": []}

    listing = await client.get("/api/transactions")
    payload = listing.json()
    assert payload["total"] == 2
    assert [item["action"] for item in payload["items"]] == ["sell", "buy"]
    assert payload["items"][1]["shares"] == pytest.approx((500 - 0.5) / 1.25)


@pytest.mark.asyncio
async def test_holdings_include_latest_nav_values_for_allocation_source_data(client, app) -> None:
    await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.25,
            "fee": 0,
            "traded_at": "2026-04-20",
        },
    )

    async with app.state.db.session() as session:
        session.add(FundNavHistory(fund_code="007339", nav_date=date.today(), nav=1.5, accumulated_nav=1.5))
        await session.commit()
        result = await daily_holdings_snapshot_job(session)

    response = await client.get("/api/portfolio/holdings")

    assert result["rows_upserted"] == 1
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["fund_code"] == "007339"
    assert item["shares"] == pytest.approx(400)
    assert item["cost_basis"] == 500
    assert item["market_value"] == 600
    assert item["pnl"] == 100
    assert item["pnl_pct"] == 20
    assert item["is_stale"] is False


@pytest.mark.asyncio
async def test_holdings_mark_data_as_stale_when_latest_nav_is_older_than_two_business_days(client, app) -> None:
    await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.25,
            "fee": 0,
            "traded_at": "2026-05-01",
        },
    )

    async with app.state.db.session() as session:
        stale_day = date.today() - timedelta(days=5)
        session.add(FundNavHistory(fund_code="007339", nav_date=stale_day, nav=1.4, accumulated_nav=1.4))
        session.add(
            HoldingsSnapshot(
                portfolio_id=1,
                snapshot_date=stale_day,
                fund_code="007339",
                shares=400,
                cost_basis=500,
                market_value=560,
            )
        )
        await session.commit()

    response = await client.get("/api/portfolio/holdings")

    assert response.status_code == 200
    assert response.json()["items"][0]["is_stale"] is True


@pytest.mark.asyncio
async def test_daily_holdings_snapshot_is_idempotent_for_same_snapshot_date(client, app) -> None:
    await client.post(
        "/api/transactions",
        json={
            "fund_code": "007339",
            "action": "buy",
            "amount": 500,
            "nav_at_trade": 1.25,
            "fee": 0,
            "traded_at": "2026-05-01",
        },
    )

    async with app.state.db.session() as session:
        session.add(FundNavHistory(fund_code="007339", nav_date=date(2026, 5, 2), nav=1.4, accumulated_nav=1.4))
        await session.commit()

        first = await daily_holdings_snapshot_job(session)
        second = await daily_holdings_snapshot_job(session)
        row_count = await session.scalar(select(func.count()).select_from(HoldingsSnapshot))
        snapshot = await session.scalar(select(HoldingsSnapshot))

    assert first == {"snapshot_date": "2026-05-02", "rows_upserted": 1}
    assert second == {"snapshot_date": "2026-05-02", "rows_upserted": 1}
    assert row_count == 1
    assert snapshot is not None
    assert snapshot.market_value == 560


@pytest.mark.asyncio
async def test_portfolio_value_history_aggregates_latest_snapshots(client, app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                HoldingsSnapshot(
                    portfolio_id=1,
                    snapshot_date=date(2026, 5, 1),
                    fund_code="007339",
                    shares=100,
                    cost_basis=120,
                    market_value=130,
                ),
                HoldingsSnapshot(
                    portfolio_id=1,
                    snapshot_date=date(2026, 5, 2),
                    fund_code="007339",
                    shares=100,
                    cost_basis=120,
                    market_value=140,
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/portfolio/value-history")

    assert response.status_code == 200
    assert response.json() == [
        {"date": "2026-05-01", "value": 130.0},
        {"date": "2026-05-02", "value": 140.0},
    ]


@pytest.mark.asyncio
async def test_onboarding_requires_force_to_overwrite_existing_watchlist(client) -> None:
    response = await client.post("/api/onboarding/apply-default-portfolio")

    assert response.status_code == 409
    assert response.json()["detail"] == "Watchlist already contains funds. Pass force=true and choose merge or replace."


@pytest.mark.asyncio
async def test_onboarding_applies_default_seed_without_creating_transactions(client, app) -> None:
    async with app.state.db.session() as session:
        await session.execute(update(Fund).values(is_watchlist=False, target_allocation=0.0))
        await session.commit()

    response = await client.post("/api/onboarding/apply-default-portfolio")

    assert response.status_code == 200
    assert response.json() == {"applied": True, "mode": "merge"}

    async with app.state.db.session() as session:
        funds = (
            await session.scalars(select(Fund).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
        ).all()
        transaction_count = await session.scalar(select(func.count()).select_from(Transaction))

    assert [(fund.code, fund.target_allocation) for fund in funds] == sorted(
        [(fund.code, fund.target_allocation) for fund in DEFAULT_RESEARCH_FUNDS],
        key=lambda item: item[0],
    )
    assert transaction_count == 0
