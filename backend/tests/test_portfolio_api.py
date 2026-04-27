from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.entities import FundNavHistory, HoldingsSnapshot, Transaction


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
